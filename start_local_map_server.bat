@echo off
setlocal

cd /d "%~dp0"

echo Starting Armies Of Terra local map server...
echo.
python scripts\local_map_server.py

echo.
echo Server stopped.
pause
