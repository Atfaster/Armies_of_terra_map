@echo off
setlocal
cd /d "%~dp0"
title Steel and Soil - Local map
echo TerraFirmaColonies: Steel ^& Soil - http://127.0.0.1:8000
echo Keep this window open while using the map. Press Ctrl+C to stop.
python -u scripts\local_map_server.py --season steel-soil --open-browser %*
if errorlevel 1 pause
