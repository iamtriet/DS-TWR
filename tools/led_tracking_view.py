"""Dedicated manual LED page sharing the existing multi-tag tracking solution."""
import tkinter as tk
from tkinter import ttk
from manual_led_view import ManualLEDControls
from uwb3d_view import Scene

class LEDTrackingView(ttk.Frame):
    def __init__(self,parent,app):
        super().__init__(parent,padding=10);self.app=app;self.fitted=False
        ttk.Label(self,text='UV / RGB Control',font=('Segoe UI',18,'bold')).pack(anchor='w')
        ttk.Label(self,text='Live multi-tag tracking · click node markers to select / deselect · white rings = selected nodes').pack(anchor='w',pady=4)
        self.controls=ManualLEDControls(self,app);self.controls.pack(fill='x',pady=6)
        bar=ttk.Frame(self);bar.pack(fill='x')
        ttk.Button(bar,text='Select all visible',command=self.select_all).pack(side='left')
        ttk.Button(bar,text='Fit map',command=lambda:self.scene.fit()).pack(side='left',padx=4)
        ttk.Button(bar,text='Tracking setup',command=lambda:parent.select(app.uwb3d)).pack(side='left')
        self.status=tk.StringVar(value='Start tracking to see nodes here')
        ttk.Label(bar,textvariable=self.status).pack(side='left',padx=10)
        self.scene=Scene(self);self.scene.projection='XY'
        self.scene.selected_nodes=app.uwb3d.scene.selected_nodes
        self.scene.pack(fill='both',expand=True,pady=8)
        ttk.Label(self,text='LED commands affect only selected nodes. Quiet lock and jump filtering are shared with Tracking; the UWB solver is unchanged.').pack(anchor='w')
        self.after(200,self.tick)
    def select_all(self):
        self.scene.selected_nodes.update(int(n) for n in (self.scene.state or {}).get('positions',{}))
        self.controls.refresh_label();self.scene.render(self.scene.state)
    def tick(self):
        if self.app.closing:return
        state=self.app.uwb3d.filtered_snapshot()
        if state:
            if self.master.select()==str(self):self.scene.render(state)
            if not self.fitted and state.get('positions') and self.winfo_viewable():
                self.scene.render(state);self.scene.fit();self.fitted=True
            self.status.set(f"{len(state.get('positions',{}))} nodes · {len(state.get('edges',[]))} UWB links · {len(state.get('stationary_held',[]))} quiet locks")
        self.after(200,self.tick)
