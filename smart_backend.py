"""
smart_backend.py
Wraps the `smartctl` command-line tool (from smartmontools) to enumerate
physical drives and pull S.M.A.R.T. health data from them.

Requires smartmontools to be installed on the system:
    https://www.smartmontools.org/  (also installable via `winget install smartmontools`)

This module auto-detects smartctl.exe in common install locations, or
falls back to whatever is on PATH.
"""

import json
import os
import re
import shutil
import subprocess

# Attribute IDs (standard ATA S.M.A.R.T. table) that matter most for
# predicting drive failure. Used by health_score.py as well.
CRITICAL_ATTR_IDS = {5, 10, 184, 187, 188, 196, 197, 198, 199, 201}

COMMON_INSTALL_PATHS = [
    r"C:\Program Files\smartmontools\bin\smartctl.exe",
    r"C:\Program Files (x86)\smartmontools\bin\smartctl.exe",
]


class SmartctlNotFound(Exception):
    pass


def find_smartctl():
    """Locate smartctl executable. Raises SmartctlNotFound if unavailable."""
    on_path = shutil.which("smartctl")
    if on_path:
        return on_path

    for path in COMMON_INSTALL_PATHS:
        if os.path.isfile(path):
            return path

    raise SmartctlNotFound(
        "smartctl.exe was not found. Install smartmontools "
        "(https://www.smartmontools.org) or run: winget install smartmontools"
    )


def _run_smartctl_full(args, smartctl_path=None):
    """Runs smartctl and returns the full CompletedProcess (returncode, stdout, stderr)."""
    exe = smartctl_path or find_smartctl()
    try:
        result = subprocess.run(
            [exe] + args,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError:
        raise SmartctlNotFound("smartctl executable could not be launched.")
    return result


def _run_smartctl(args, smartctl_path=None):
    """Back-compat helper: returns stdout only (used for scan/data reads,
    where smartctl exits non-zero on drives with SMART warnings by design)."""
    return _run_smartctl_full(args, smartctl_path).stdout


def scan_drives(smartctl_path=None):
    """
    Returns a list of dicts: [{"device": "/dev/sda" or "\\\\.\\PhysicalDrive0",
                                "type": "ata"/"nvme"/"scsi", "info": "..."}]
    """
    raw = _run_smartctl(["--scan", "-j"], smartctl_path)
    drives = []
    try:
        data = json.loads(raw)
        for d in data.get("devices", []):
            drives.append({
                "device": d.get("name"),
                "type": d.get("type", "auto"),
                "info": d.get("info_name", d.get("name")),
            })
    except (json.JSONDecodeError, ValueError):
        # Fallback: parse plain-text `--scan` output
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"(\S+)\s+-d\s+(\S+)", line)
            if m:
                drives.append({"device": m.group(1), "type": m.group(2), "info": line})
    return drives


def get_drive_data(device, dev_type="auto", smartctl_path=None):
    """
    Runs `smartctl -a -j <device>` and returns a normalized dict of
    health information for both ATA/SATA and NVMe drives.
    """
    args = ["-a", "-j", "-d", dev_type, device] if dev_type and dev_type != "auto" \
        else ["-a", "-j", device]
    raw = _run_smartctl(args, smartctl_path)

    try:
        raw_json = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {"device": device, "error": "Could not parse smartctl output", "raw": raw}

    result = {
        "device": device,
        "error": None,
        "model": raw_json.get("model_name", "Unknown"),
        "serial": raw_json.get("serial_number", "Unknown"),
        "firmware": raw_json.get("firmware_version", "Unknown"),
        "capacity_bytes": raw_json.get("user_capacity", {}).get("bytes", 0),
        "interface": raw_json.get("device", {}).get("type", dev_type),
        "smart_supported": raw_json.get("smart_support", {}).get("available", False),
        "smart_enabled": raw_json.get("smart_support", {}).get("enabled", False),
        "health_passed": raw_json.get("smart_status", {}).get("passed"),
        "temperature_c": raw_json.get("temperature", {}).get("current"),
        "power_on_hours": raw_json.get("power_on_time", {}).get("hours"),
        "power_cycles": raw_json.get("power_cycle_count"),
        "rotation_rate": raw_json.get("rotation_rate"),  # 0 = SSD, >0 = HDD RPM
        "attributes": [],
        "nvme": {},
    }

    # ATA/SATA style attribute table
    ata_table = raw_json.get("ata_smart_attributes", {}).get("table", [])
    for attr in ata_table:
        result["attributes"].append({
            "id": attr.get("id"),
            "name": attr.get("name"),
            "value": attr.get("value"),
            "worst": attr.get("worst"),
            "thresh": attr.get("thresh"),
            "raw": attr.get("raw", {}).get("value"),
            "raw_str": attr.get("raw", {}).get("string"),
            "when_failed": attr.get("when_failed", ""),
            "critical": attr.get("id") in CRITICAL_ATTR_IDS,
        })

    # NVMe style health log
    nvme_log = raw_json.get("nvme_smart_health_information_log")
    if nvme_log:
        result["nvme"] = nvme_log
        result["temperature_c"] = nvme_log.get("temperature", result["temperature_c"])
        result["power_on_hours"] = nvme_log.get("power_on_hours", result["power_on_hours"])
        result["power_cycles"] = nvme_log.get("power_cycles", result["power_cycles"])
        result["percentage_used"] = nvme_log.get("percentage_used")
        result["media_errors"] = nvme_log.get("media_errors")
        result["critical_warning"] = nvme_log.get("critical_warning")

    # Self-test status/log - ATA style
    self_test_block = raw_json.get("ata_smart_data", {}).get("self_test", {})
    ata_status_block = self_test_block.get("status", {})
    ata_status = ata_status_block.get("string")
    ata_remaining_pct = ata_status_block.get("remaining_percent")
    ata_in_progress = ("in progress" in (ata_status or "").lower()) or \
        (ata_status_block.get("value") == 0xF0 if ata_status_block else False)

    polling = self_test_block.get("polling_minutes", {})
    ata_capabilities = {
        "short": polling.get("short"),
        "long": polling.get("long") or polling.get("extended"),
        "conveyance": polling.get("conveyance"),
    }

    log_table = raw_json.get("ata_smart_self_test_log", {}).get("standard", {}).get("table", [])
    ata_log = [
        {
            "type": e.get("type", {}).get("string"),
            "status": e.get("status", {}).get("string"),
            "hours": e.get("lifetime_hours"),
        }
        for e in log_table
    ]

    # Self-test status/log - NVMe style (key names vary a bit by smartctl version)
    nvme_st_log = raw_json.get("nvme_self_test_log", {})
    nvme_current = nvme_st_log.get("current_self_test_operation", {})
    nvme_status = nvme_current.get("string")
    nvme_remaining_pct = None
    for key in ("current_self_test_completion_percent", "self_test_completion_percent",
                "completion_percent"):
        if key in nvme_st_log:
            # This field is generally "percent complete", unlike ATA's "percent remaining".
            nvme_remaining_pct = 100 - nvme_st_log[key]
            break
    nvme_in_progress = None
    if nvme_status:
        nvme_in_progress = "no self-test" not in nvme_status.lower()

    nvme_test_table = nvme_st_log.get("table", [])
    nvme_log = [
        {
            "type": e.get("self_test_code", {}).get("string"),
            "status": e.get("self_test_result", {}).get("string"),
            "hours": e.get("power_on_hours"),
        }
        for e in nvme_test_table
    ]

    result["self_test_status"] = ata_status or nvme_status
    result["self_test_in_progress"] = ata_in_progress if ata_status else nvme_in_progress
    result["self_test_remaining_pct"] = ata_remaining_pct if ata_status else nvme_remaining_pct
    result["self_test_capabilities"] = ata_capabilities
    result["self_test_log"] = ata_log or nvme_log

    # Surface permission/access problems clearly instead of a generic parse error.
    # smartctl exits non-zero for various reasons (including normal SMART
    # warnings), so only flag this when we got no usable identification data
    # back at all, which is the signature of an access-denied failure.
    if result["model"] == "Unknown" and not result["attributes"] and not result["nvme"]:
        msg = (raw_json.get("smartctl", {}).get("messages") or [{}])[0].get("string", "")
        if "permission" in msg.lower() or "access" in msg.lower() or "administrator" in msg.lower():
            result["error"] = (
                "Access denied reading this drive. DiskPulse needs to be run "
                "as Administrator to read raw S.M.A.R.T. data on Windows."
            )
        elif msg:
            result["error"] = msg

    return result


def start_self_test(device, dev_type="auto", test_type="short", smartctl_path=None):
    """
    Kicks off a background S.M.A.R.T. self-test on the drive itself.
    test_type: "short" (~2 min) or "long" (can take hours).
    Returns {"success": bool, "message": str}. The test runs on the
    drive's own controller; poll get_drive_data() afterwards
    (self_test_status / self_test_log) for progress and results.
    """
    if test_type not in ("short", "long"):
        raise ValueError("test_type must be 'short' or 'long'")
    args = (["-d", dev_type] if dev_type and dev_type != "auto" else []) + ["-t", test_type, device]
    proc = _run_smartctl_full(args, smartctl_path)
    output = (proc.stdout or "") + (proc.stderr or "")

    lower = output.lower()
    if "please wait" in lower or "test will complete" in lower or "test has begun" in lower:
        success = True
    elif "permission" in lower or "access is denied" in lower or "administrator" in lower:
        success = False
        output += "\n\nRun DiskPulse as Administrator to control self-tests."
    elif "not supported" in lower or "unavailable" in lower:
        success = False
    else:
        # Fall back to smartctl's own exit code (0 or 4 generally mean the
        # command itself ran; specific bits in returncode flag SMART
        # warnings unrelated to whether the test command was accepted).
        success = proc.returncode in (0, 4)

    return {"success": success, "message": output.strip() or "No output from smartctl."}


def abort_self_test(device, dev_type="auto", smartctl_path=None):
    args = (["-d", dev_type] if dev_type and dev_type != "auto" else []) + ["-X", device]
    proc = _run_smartctl_full(args, smartctl_path)
    output = (proc.stdout or "") + (proc.stderr or "")
    lower = output.lower()
    if "permission" in lower or "access is denied" in lower or "administrator" in lower:
        success = False
        output += "\n\nRun DiskPulse as Administrator to control self-tests."
    else:
        success = proc.returncode in (0, 4)
    return {"success": success, "message": output.strip() or "No output from smartctl."}
