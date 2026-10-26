"""Selected-node LED control, without changing radio roles or ranging configuration."""
import tkinter as tk
import re
from tkinter import ttk,messagebox

class ManualLEDControls(ttk.LabelFrame):
    def __init__(self,parent,app):
        super().__init__(parent,text='Manual UV / RGB · click nodes to select multiple')
        self.app=app;self.selection=tk.StringVar();self.result=tk.StringVar(value='UV outputs: left P0.30 / right P0.02 · firmware 1.2.7+ required')
        row=ttk.Frame(self);row.pack(fill='x')
        ttk.Label(row,textvariable=self.selection).pack(side='left',padx=4)
        for title,cmd in [('UV left','led aux uv 1 0'),('UV right','led aux uv 0 1'),('UV both','led aux uv 1 1'),('LED off','led aux off'),('Read LED','led aux status')]:
            ttk.Button(row,text=title,command=lambda c=cmd:self.send(c)).pack(side='left',padx=2)
        ttk.Button(row,text='Clear selection',command=self.clear).pack(side='left',padx=2)
        ttk.Button(row,text='RGB…',command=self.rgb).pack(side='left',padx=2)
        ttk.Label(self,textvariable=self.result,wraplength=1100).pack(anchor='w',padx=4,pady=3)
        self.after(500,self.refresh)
    def clear(self):self.app.uwb3d.scene.selected_nodes.clear();self.refresh_label()
    def refresh_label(self):
        ids=sorted(self.app.uwb3d.scene.selected_nodes)
        self.selection.set('Selected: '+(', '.join(f'{n:04X}' for n in ids) or 'none'))
    def refresh(self):
        if self.app.closing:return
        self.refresh_label();self.after(500,self.refresh)
    def send(self,command):
        if self.app.busy or self.app.closing:
            self.result.set('Wait for the current command to finish.');return
        nodes=sorted(self.app.uwb3d.scene.selected_nodes);view=self.app.uwb3d
        if not nodes:messagebox.showinfo('LED','Click one or more node markers first.',parent=self);return
        fleet=view.runtime.gateway
        if fleet is None or not hasattr(fleet,'manual_led') or view.gate_start!='ready':
            messagebox.showerror('LED','Start the swarm and wait for configuration to complete.',parent=self);return
        def done(rows):
            summaries=[]
            for row in rows:
                if 'FAILED:' in row:summaries.append(row);continue
                if 'profile=uv' in row or 'profile=off' in row:
                    values=dict(re.findall(r'(UV_L|UV_R)=(\d)',row))
                    summaries.append(row[:4]+': UV left '+('ON' if values.get('UV_L')=='1' else 'OFF')+', right '+('ON' if values.get('UV_R')=='1' else 'OFF')+' · acknowledged')
                else:summaries.append(row)
                timing=re.search(r'elapsed_ms=(\d+) connection=(\w+)',row)
                if timing and ('profile=uv' in row or 'profile=off' in row):summaries[-1]+=f' · {timing[1]} ms ({timing[2]})'
            self.result.set(' | '.join(summaries))
        self.result.set('Sending to '+', '.join(f'{n:04X}' for n in nodes)+'…')
        self.app.run('Manual LED · '+', '.join(f'{n:04X}' for n in nodes),lambda:fleet.manual_led(nodes,command),done,preserve_tracking=True)
    def rgb(self):
        dialog=tk.Toplevel(self);dialog.title('RGB daughterboard · selected nodes')
        ttk.Label(dialog,text='Observed colour mapping corrected: R = red, G = green, B = blue. Blue 1 is unavailable.').pack(padx=12,pady=8)
        values=[tk.IntVar(value=0) for _ in range(6)]
        for i,label in enumerate(('R1 · Red','G1 · Green','B1 · unavailable','R2 · Red','G2 · Green','B2 · Blue')):
            box=ttk.Checkbutton(dialog,text=label,variable=values[i]);box.pack(anchor='w',padx=12)
            if i==2:box.state(['disabled'])
        ttk.Button(dialog,text='Apply to selected nodes',command=lambda:self.send('led aux rgb '+' '.join(str(v.get()) for v in values))).pack(padx=12,pady=8)
