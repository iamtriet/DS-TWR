@echo off
cd /d "%~dp0"
python tools\uwb3d_live.py --source swarm --roster build/uwb3d/hardware-six-com9/roster.json --preserve-roster-order --gateway PCB_854655FD --port COM9 --max-age 0.3 --output build/uwb3d/hardware-six-com9/sniffer-origin --planar --sniffer-origin
