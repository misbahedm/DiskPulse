"""
health_score.py
Turns raw S.M.A.R.T. data into a single 0-100 health score plus a
status label (Good / Caution / Bad) and a list of human-readable
warning reasons. This is an original scoring heuristic, not a copy
of any third-party tool's proprietary algorithm.
"""

STATUS_GOOD = "Good"
STATUS_CAUTION = "Caution"
STATUS_BAD = "Bad"

# Attribute-specific raw-value penalty rules for ATA/SATA drives.
# Each entry: attribute id -> function(raw_value) -> (points_to_deduct, reason or None)
def _ata_penalties(attr_id, raw, name):
    penalties = []

    if attr_id == 5 and raw > 0:  # Reallocated Sectors Count
        pts = min(40, 5 + raw * 2)
        penalties.append((pts, f"{raw} reallocated sector(s) detected"))

    elif attr_id == 197 and raw > 0:  # Current Pending Sector
        pts = min(35, 8 + raw * 3)
        penalties.append((pts, f"{raw} sector(s) currently pending reallocation"))

    elif attr_id == 198 and raw > 0:  # Offline Uncorrectable
        pts = min(35, 8 + raw * 3)
        penalties.append((pts, f"{raw} uncorrectable sector(s) found"))

    elif attr_id == 184 and raw > 0:  # End-to-End Error
        penalties.append((min(30, raw * 5), f"{raw} end-to-end data error(s)"))

    elif attr_id == 187 and raw > 0:  # Reported Uncorrectable Errors
        penalties.append((min(25, raw * 4), f"{raw} reported uncorrectable error(s)"))

    elif attr_id == 188 and raw > 0:  # Command Timeout
        penalties.append((min(10, raw), f"{raw} command timeout(s)"))

    elif attr_id == 196 and raw > 0:  # Reallocation Event Count
        penalties.append((min(15, raw), f"{raw} reallocation event(s)"))

    elif attr_id == 199 and raw > 0:  # UDMA CRC Error Count (often cabling)
        penalties.append((min(10, raw // 5 + 1), f"{raw} interface CRC error(s) (check cable/connection)"))

    elif attr_id == 10 and raw > 0:  # Spin Retry Count
        penalties.append((min(20, raw * 5), f"{raw} spin retry event(s)"))

    elif attr_id == 201 and raw > 0:  # Soft Read Error Rate
        penalties.append((min(10, raw // 10 + 1), f"{raw} soft read error(s)"))

    return penalties


def compute_health(drive_data):
    """
    drive_data: normalized dict from smart_backend.get_drive_data()
    Returns: {"score": int 0-100, "status": str, "reasons": [str, ...]}
    """
    if drive_data.get("error"):
        return {"score": 0, "status": STATUS_BAD, "reasons": [drive_data["error"]]}

    score = 100
    reasons = []

    # Overall self-assessment test result is the strongest signal
    if drive_data.get("health_passed") is False:
        score -= 60
        reasons.append("Drive failed its own SMART overall-health self-assessment")

    # NVMe path
    if drive_data.get("nvme"):
        crit = drive_data.get("critical_warning") or 0
        if crit:
            score -= min(50, crit * 15)
            reasons.append(f"NVMe critical warning flags set (0x{crit:02x})")

        pct_used = drive_data.get("percentage_used")
        if pct_used is not None:
            if pct_used >= 100:
                score -= 40
                reasons.append("Drive has reached 100% of its rated endurance")
            elif pct_used >= 80:
                score -= 20
                reasons.append(f"Drive endurance at {pct_used}% used")
            elif pct_used >= 50:
                score -= 5

        media_errors = drive_data.get("media_errors") or 0
        if media_errors > 0:
            score -= min(30, media_errors * 5)
            reasons.append(f"{media_errors} media error(s) logged")

    # ATA/SATA path
    for attr in drive_data.get("attributes", []):
        raw = attr.get("raw") or 0
        if not isinstance(raw, (int, float)):
            continue
        for pts, reason in _ata_penalties(attr.get("id"), int(raw), attr.get("name")):
            score -= pts
            if reason:
                reasons.append(reason)
        if attr.get("when_failed"):
            score -= 25
            reasons.append(f"Attribute '{attr.get('name')}' has failed its threshold")

    # Temperature
    temp = drive_data.get("temperature_c")
    if temp:
        if temp >= 60:
            score -= 15
            reasons.append(f"Very high temperature ({temp}\u00b0C)")
        elif temp >= 50:
            score -= 5
            reasons.append(f"Elevated temperature ({temp}\u00b0C)")

    score = max(0, min(100, score))

    if score >= 85:
        status = STATUS_GOOD
    elif score >= 60:
        status = STATUS_CAUTION
    else:
        status = STATUS_BAD

    if not reasons:
        reasons.append("No significant issues detected")

    return {"score": score, "status": status, "reasons": reasons}
