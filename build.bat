@echo off
REM Builds DiskPulse.exe as a single-file, windowed Windows executable.
REM Run this from a Windows command prompt inside this folder.

python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --onefile --windowed ^
    --name DiskPulse ^
    --icon NONE ^
    main.py

echo.
echo Build complete. Find DiskPulse.exe in the dist\ folder.
pause
