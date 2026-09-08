@echo off
cd /d "%~dp0"
if exist "RE7_21_Noir.exe" (
    "RE7_21_Noir.exe" --room-server "room-server.json"
) else (
    ".venv\Scripts\python.exe" room_server.py --config "room-server.json"
)
pause
