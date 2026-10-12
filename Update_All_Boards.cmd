@echo off
setlocal
cd /d "%~dp0"
echo Updating the enrolled boards with build/images/validation_b.signed.bin
echo Successful trial images will be confirmed and reboot-checked automatically.
python -u tools\ota_batch.py update --parallel 2
set "otaResult=%ERRORLEVEL%"
echo.
if "%otaResult%"=="0" (echo Batch completed successfully.) else (echo Batch incomplete. Read the errors above and per-board report.)
pause
exit /b %otaResult%
