@echo off
setlocal
cd /d "%~dp0"
echo Power the intended 30 boards. Enrollment reads identity/state without uploading.
python -u tools\ota_batch.py enroll --count 30
set "otaResult=%ERRORLEVEL%"
pause
exit /b %otaResult%
