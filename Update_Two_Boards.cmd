@echo off
setlocal
cd /d "%~dp0"
echo Updating the two enrolled test boards over BLE.
python -u tools\ota_batch.py update --roster boards_ota_two.json --parallel 2
set "otaResult=%ERRORLEVEL%"
echo.
if "%otaResult%"=="0" (echo Both boards are updated and confirmed.) else (echo Batch incomplete. Check the report and retry after fixing the failed board.)
pause
exit /b %otaResult%
