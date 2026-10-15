@echo off
setlocal
cd /d "%~dp0"
echo Close Minecraft before updating your client map.
echo The team ZIP will be copied to the configured Google Drive folder.
echo Use --no-publish to skip GitHub publication.
echo The private Cloudflare map will update automatically after merging.
echo Use --no-cloudflare to skip Cloudflare publication.
echo New original ZIPs in imports\steel-soil will be imported automatically.
python -u scripts\build_season.py build --import-zips %*
set "taskMergeExit=%errorlevel%"
if "%taskMergeExit%"=="0" (
    python scripts\season_log.py success "Done. Wait for Google Drive synchronization before sharing the update."
) else (
    python scripts\season_log.py error "Update stopped. Read the error above; backups are retained."
)
pause
exit /b %taskMergeExit%
