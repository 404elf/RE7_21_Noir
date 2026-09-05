@echo off
cd /d "%~dp0"
if exist "dist\audio-v1\RE7_21_Noir\RE7_21_Noir.exe" (
    start "" "dist\audio-v1\RE7_21_Noir\RE7_21_Noir.exe"
    exit /b
)
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto fail
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto fail
start "" ".venv\Scripts\pythonw.exe" main.py
exit /b
:fail
echo Setup failed. Install Python 3.12 or newer and try again.
pause
