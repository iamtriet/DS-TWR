import sys,json,time,tkinter as tk
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from control_app import ControlApp
from PIL import ImageGrab
base=ROOT/'build/uwb3d';base.mkdir(parents=True,exist_ok=True)
root=tk.Tk();app=ControlApp(root);root.geometry('1280x960')
view=app.uwb3d;view.master.select(view);errors=[];checks=[];started=time.monotonic()
def fail(exc,value,tb):
    import traceback
    errors.append(''.join(traceback.format_exception(exc,value,tb)))
root.report_callback_exception=fail
def begin():view.start();root.after(2500,inspect)
def inspect():
    state=view.runtime.snapshot()
    try:
        assert state['source']=='simulation';assert len(state['nodes'])==30
        assert len(view.table.get_children())==30
        assert len(view.scene.nodes)==30,state['reason']
        checks.append('30 nodes rendered from decoded ranges')
        view.table.selection_set('1');view.select();assert view.scene.selected==1
        view.projection.set('XZ');view.project();assert view.scene.projection=='XZ'
        view.scene.yaw+=.4;view.scene.zoom(1.1);view.scene.reset()
        checks.extend(('selection/follow/camera projection','distance matrix 30x30'))
        assert len(view.matrix_ids)==30
        view.pause();before=state['time']
        root.after(800,lambda:after_pause(before))
    except Exception as exc:errors.append(str(exc));finish()
def after_pause(before):
    try:
        assert view.runtime.snapshot()['time']>before
        checks.append('paused render does not pause acquisition')
        view.pause();view.projection.set('3D');view.project()
        root.after(2200,capture)
    except Exception as exc:errors.append(str(exc));finish()
def capture():
    import ctypes
    root.update();ctypes.windll.user32.RedrawWindow(int(root.wm_frame(),16),None,None,0x581);root.update()
    ImageGrab.grab(window=int(root.wm_frame(),16)).save(str(base/'simulation-30-gui.png'))
    checks.append(f'active render window: {sum(time.monotonic()-t<1 for t in view.last_render)} frames/s')
    assert len(view.heat_cells)==900
    view.show_link(1,2);assert '0001' in view.link_text.get()
    checks.append('900 heatmap cells and selected-link history')
    finish()
def finish():
    (base/'gui-test.json').write_text(json.dumps(dict(checks=checks,errors=errors,seconds=time.monotonic()-started,state=view.runtime.snapshot()),indent=2))
    app.close()
root.after(400,begin);root.after(20000,finish);root.mainloop()
print(json.dumps(dict(checks=checks,errors=errors)))
if errors:raise SystemExit(1)
