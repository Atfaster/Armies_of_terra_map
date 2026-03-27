@echo off
setlocal

cd /d "%~dp0"

echo Starting Armies Of Terra local map server...
echo.
start "" powershell -NoProfile -Command "Start-Sleep -Seconds 1; Start-Process 'http://127.0.0.1:8000/'"
python scripts\local_map_server.py

echo.
echo Server stopped.
pause
