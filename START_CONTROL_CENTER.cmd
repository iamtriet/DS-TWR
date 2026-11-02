@echo off
cd /d "%~dp0"
start "" pythonw tools\control_app.py --led --scan
