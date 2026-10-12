@echo off
setlocal
cd /d "%~dp0"
python tools\control_app.py --scan
if errorlevel 1 pause
