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
    parser.add_argument('--gateway',required=True);parser.add_argument('--port',required=True)
    parser.add_argument('--period',type=int,default=500)
    parser.add_argument('--max-age',type=float,default=1.5)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--capture',action='store_true',help='Save this actual window once live positions are available')
    parser.add_argument('--planar',action='store_true',help='Explicit table-plane assumption; estimate XY, do not measure altitude')
    a=parser.parse_args();boards=json.loads(a.roster.read_text())['boards']
    boards=sorted(boards,key=lambda b:(b['name']!=a.gateway,b['name']))
    if not boards or boards[0]['name']!=a.gateway:parser.error('Gateway must be in selected roster')
    a.output.mkdir(parents=True,exist_ok=True)
    root=tk.Tk();app=ControlApp(root);root.geometry('1280x960');view=app.uwb3d
    app.set_devices(boards);app.boards.selection_set(app.boards.get_children())
    view.master.select(view);view.source.set('gateway');view.port.set(a.port);view.count.set(len(boards))
    config=Config(dimensions=2 if a.planar else 3,max_age=a.max_age,gateway_superframe_ms=a.period).validate()
    view.geometry.set('Tabletop 2D' if a.planar else 'Relative 3D')
    view.runtime.config=config;view.age.set(config.max_age);view.projection.set('XY');view.project()
    (a.output/'mapping.json').write_text(json.dumps({i+1:b for i,b in enumerate(boards)},indent=2))
    async def begin():
        await view.runtime.start('gateway',boards=boards,port=a.port)
        await view.runtime.recording(a.output/'session.jsonl')
    captured=False;fitted=False
    def snapshot():
        nonlocal captured,fitted
        state=view.runtime.snapshot()
        if state:(a.output/'latest.json').write_text(json.dumps(state,indent=2))
        if not fitted and state and len(state.get('positions',{}))==len(boards):
            view.scene.render(state);view.scene.fit();fitted=True
        if a.capture and not captured and state and len(state.get('measured_positions',{}))==len(boards):
            from PIL import ImageGrab
            root.update_idletasks()
            ImageGrab.grab(window=int(root.wm_frame(),16)).save(a.output/'live-top-down.png')
            captured=True
        if not app.closing:root.after(2000,snapshot)
    root.after(300,lambda:view.submit(begin()));root.after(2000,snapshot)
    root.mainloop()


if __name__=='__main__':main()
