# DiskPulse — Drive Health Monitor

A lightweight Windows desktop app that watches the S.M.A.R.T. health of your
internal HDDs/SSDs/NVMe drives and warns you before they fail.

Windows Exe Download https://github.com/misbahedm/DiskPulse/releases/download/DiskPulse/DiskPulse.v1.0.0.exe

## Features

- Auto-detects all physical drives (HDD, SATA SSD, NVMe) with a type tag
- Health score (0–100) with Good / Caution / Bad status and a color-graded
  animated health bar, based on an original scoring heuristic that weighs
  reallocated sectors, pending sectors, uncorrectable errors, CRC errors,
  temperature, and (for NVMe) endurance percentage and media errors
- **Resizable drive list panel** — drag the divider to make it bigger/smaller;
  the width is remembered between sessions
- **System-wide health bar** summarizing all drives at a glance, plus a count
  of drives that need attention
- **Search/filter box** to quickly find a drive by model, serial, or device
- **Sortable columns** — click Drive / Type / Status / Health headers to sort
- **Performance tab** with live line-graphs of health score and temperature
  history, sampled every refresh cycle
- **Self-Test tab** — trigger the drive's own built-in SMART short/long
  self-test, abort a running test, and view the self-test result log
- Full S.M.A.R.T. attribute table per drive (ATA/SATA) or health log (NVMe)
- Live temperature, power-on hours, power cycle count
- Background polling on a configurable interval, with a visible countdown
  to the next refresh
- System tray icon that reflects the worst drive status at a glance
- Desktop notifications when a drive's status changes
- Exportable health report as plain text **or CSV**
- Dark mode

## Requirements

1. **Python 3.9+** (only needed if running from source; not needed if you
   build/use the standalone .exe)
2. **smartmontools** — provides the `smartctl` command DiskPulse uses to
   read S.M.A.R.T. data. Install with:
   ```
   winget install smartmontools
   ```
   or download from https://www.smartmontools.org
3. Run DiskPulse **as Administrator** — reading raw S.M.A.R.T. data from a
   physical drive on Windows requires elevated privileges.

## Running from source

```bash
pip install -r requirements.txt
python main.py
```

## Building a standalone .exe

On Windows, inside this folder:

```bat
build.bat
```

This uses PyInstaller to produce `dist\DiskPulse.exe`, a single-file
windowed app with no console window. Copy it anywhere and run it directly
(smartmontools must still be installed separately, or you can bundle
`smartctl.exe` alongside the .exe and point Settings > Preferences at it).

## Project layout

```
main.py            App entry point, Tkinter GUI, tray icon, notifications
smart_backend.py   Wraps smartctl, normalizes ATA/NVMe SMART data
health_score.py    Converts raw SMART attributes into a 0-100 health score
ui_theme.py         Light/dark color palettes
requirements.txt    Python dependencies
build.bat            PyInstaller packaging script
```

## Notes / next steps you might want to add later

- A temperature history chart per drive (the app already logs history
  in-memory; just needs a small matplotlib or canvas-based graph)
- Auto-start with Windows (registry Run key, same pattern as your NetMeter app)
- Scheduled SMART short/long self-tests via `smartctl -t short|long`
- CSV export in addition to the plain-text report
