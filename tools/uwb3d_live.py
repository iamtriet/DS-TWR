"""Open the existing Control Center with an explicitly selected live gateway fleet."""
import argparse
import json
import time
import tkinter as tk
from pathlib import Path
from control_app import ControlApp
from uwb3d_core import Config


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--roster',type=Path,required=True)
    parser.add_argument('--preserve-roster-order',action='store_true',help='Keep mobile IDs in roster order when adding boards')
    parser.add_argument('--gateway',required=True);parser.add_argument('--port',required=True)
    parser.add_argument('--period',type=int,default=500)
    parser.add_argument('--source',choices=('gateway','reference','swarm'),default='gateway')
    parser.add_argument('--moving-node',type=int,default=0,help='Exclude this node from display-frame alignment; 0 uses stationary majority')
    parser.add_argument('--reference-nodes',default='',help='Comma-separated IDs for separately surveyed planar reference group')
    parser.add_argument('--kalman',action='store_true',help='Enable optional position Kalman filter')
    parser.add_argument('--max-age',type=float,default=1.5)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--capture',action='store_true',help='Save this actual window once live positions are available')
    parser.add_argument('--planar',action='store_true',help='Explicit table-plane assumption; estimate XY, do not measure altitude')
    parser.add_argument('--sniffer-origin',action='store_true',help='Stationary UART board actively ranges as last ID and defines XY origin (firmware 1.2.6+)')
    parser.add_argument('--geofencing',action='store_true',help='Open the Virtual Geofencing mission tab after starting tracking')
    a=parser.parse_args();boards=json.loads(a.roster.read_text())['boards']
    if a.sniffer_origin and a.source!='swarm':parser.error('--sniffer-origin requires --source swarm')
    boards=sorted(boards,key=lambda b:b['name']!=a.gateway) if a.preserve_roster_order else sorted(boards,key=lambda b:(b['name']!=a.gateway,b['name']))
    if not boards or boards[0]['name']!=a.gateway:parser.error('Gateway must be in selected roster')
    a.output.mkdir(parents=True,exist_ok=True)
    root=tk.Tk();app=ControlApp(root);root.geometry('1280x960');view=app.uwb3d
    app.set_devices(boards);app.boards.selection_set(app.boards.get_children())
    view.master.select(view);view.source.set(a.source);view.port.set(a.port);view.count.set(len(boards))
    config=Config(dimensions=2 if a.planar else 3,max_age=a.max_age,range_filtering=True,
                  swarm_origin=len(boards) if a.sniffer_origin else 0,
                  moving_node=a.moving_node,
                  reference_nodes=tuple(int(n) for n in a.reference_nodes.split(',') if n),
                  motion_profile=a.source=='gateway',filtering=a.kalman,prediction_limit=.6,temporal_weight=.01 if a.source=='gateway' else .1,
                  gateway_superframe_ms=a.period if a.source=='gateway' else 500,
                  reference_period_ms=a.period if a.source=='reference' else 200).validate()
    view.geometry.set('Tabletop 2D' if a.planar else 'Relative 3D')
    view.runtime.config=config;view.age.set(config.max_age);view.projection.set('XY');view.project()
    view.filtering.set(config.filtering)
    view.sniffer_origin.set(a.sniffer_origin)
    (a.output/'mapping.json').write_text(json.dumps({(len(boards) if i==0 and a.sniffer_origin else i) if a.source=='swarm' else i+1:b for i,b in enumerate(boards)},indent=2))
    mobile_count=len(boards)-(a.source=='swarm' and not a.sniffer_origin)
    if a.source=='swarm':view.rate.set(25);view.count.set(mobile_count)
    async def begin():
        await view.runtime.start(a.source,boards=boards,port=a.port)
        await view.runtime.recording(a.output/'session.jsonl')
    captured=False;fitted=False
    def snapshot():
        nonlocal captured,fitted
        state=view.runtime.snapshot()
        if state:
            (a.output/'latest.json').write_text(json.dumps(state,indent=2))
            (a.output/'latest-display.json').write_text(json.dumps(view.filtered_snapshot(),indent=2))
        if not fitted and state and len(state.get('positions',{}))==mobile_count:
            view.scene.render(view.filtered_snapshot());view.scene.fit();fitted=True
        if a.capture and not captured and state and len(state.get('measured_positions',{}))==mobile_count:
            from PIL import ImageGrab
            root.update_idletasks()
            ImageGrab.grab(window=int(root.wm_frame(),16)).save(a.output/'live-top-down.png')
            captured=True
        if not app.closing:root.after(2000,snapshot)
    root.after(300,lambda:view.submit(begin()));root.after(2000,snapshot)
    if a.geofencing:root.after(500,lambda:view.master.select(app.geofencing))
    root.mainloop()


if __name__=='__main__':main()
