@echo off
cd /d "%~dp0"
python tools\swarm_localization.py --seconds 30
pause
