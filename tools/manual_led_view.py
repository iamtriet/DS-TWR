"""Selected-node LED control, without changing radio roles or ranging configuration."""
import tkinter as tk
import re
from tkinter import ttk,messagebox

class ManualLEDControls(ttk.LabelFrame):
    def __init__(self,parent,app):
        super().__init__(parent,text='Manual UV / RGB · click nodes to select multiple')
        self.app=app;self.standalone=None;self.board_keys=[];self.selection=tk.StringVar();self.result=tk.StringVar(value='Prepare BLE once, then commands reuse connections. UV: P0.30 / P0.02; RGB B1: P0.02.')
        chooser=ttk.Frame(self);chooser.pack(fill='x')
        ttk.Label(chooser,text='Without tracking: select BLE boards (Ctrl/Shift for multiple)').pack(side='left')
        self.board_list=tk.Listbox(chooser,selectmode='extended',exportselection=False,height=4,width=40)
        self.board_list.pack(side='left',padx=6)
        ttk.Button(chooser,text='Scan BLE',command=app.scan).pack(side='left')
        ttk.Button(chooser,text='Disconnect LED',command=self.disconnect).pack(side='left',padx=4)
        ttk.Label(self,textvariable=self.selection,wraplength=1100).pack(anchor='w',padx=4)
        row=ttk.Frame(self);row.pack(fill='x')
        for title,cmd in [('UV left','led aux uv 1 0'),('UV right','led aux uv 0 1'),('UV both','led aux uv 1 1'),('LED off','led aux off'),('Read LED','led aux status')]:
            ttk.Button(row,text=title,command=lambda c=cmd:self.send(c)).pack(side='left',padx=2)
        ttk.Button(row,text='Clear selection',command=self.clear).pack(side='left',padx=2)
        ttk.Button(row,text='Prepare BLE',command=lambda:self.send('led aux status')).pack(side='left',padx=2)
        self.build_rgb_panel()
        ttk.Label(self,textvariable=self.result,wraplength=1100).pack(anchor='w',padx=4,pady=3)
        self.after(500,self.refresh)
    def clear(self):
        self.app.uwb3d.scene.selected_nodes.clear();self.board_list.selection_clear(0,'end');self.refresh_label()
    def refresh_label(self):
        if self.app.uwb3d.runtime.gateway is None:
            names=[self.board_list.get(i) for i in self.board_list.curselection()]
            self.selection.set('Selected: '+(', '.join(names) or 'none'));return
        ids=sorted(self.app.uwb3d.scene.selected_nodes)
        self.selection.set('Selected: '+(', '.join(f'{n:04X}' for n in ids) or 'none'))
    def refresh(self):
        if self.app.closing:return
        keys=list(self.app.devices)
        if keys!=self.board_keys and self.standalone is None:
            self.board_keys=keys;self.board_list.delete(0,'end')
            for key in keys:self.board_list.insert('end',self.app.devices[key]['name'])
        self.refresh_label();self.after(500,self.refresh)
    async def close_connections(self):
        if self.standalone:
            await self.standalone.stop();self.standalone=None
    def disconnect(self):
        async def close():
            await self.close_connections()
            fleet=self.app.uwb3d.runtime.gateway
            if fleet and hasattr(fleet,'close_led_sessions'):
                async with fleet.control_lock:await fleet.close_led_sessions()
        self.app.run('Disconnect LED',close,preserve_tracking=True)
    def send(self,command):
        if self.app.busy or self.app.closing:
            self.result.set('Wait for the current command to finish.');return
        nodes=sorted(self.app.uwb3d.scene.selected_nodes);view=self.app.uwb3d
        fleet=view.runtime.gateway
        if fleet is None:
            nodes=[i+1 for i in self.board_list.curselection()]
            if not nodes:messagebox.showinfo('LED','Select BLE boards from the list first.',parent=self);return
            if self.standalone is None:
                from swarm_gateway import LEDFleet
                self.standalone=LEDFleet([self.app.devices[k] for k in self.board_keys],self.app.emit)
            fleet=self.standalone
        elif not hasattr(fleet,'manual_led') or view.gate_start!='ready':
            messagebox.showerror('LED','Start the swarm and wait for configuration to complete.',parent=self);return
        if not nodes:messagebox.showinfo('LED','Click one or more node markers first.',parent=self);return
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
            if getattr(fleet,'led_only',False):
                summaries=[re.sub(r'^([0-9A-F]{4}):',lambda m:fleet.boards[int(m[1],16)]['name']+':',s) for s in summaries]
            self.result.set(' | '.join(summaries))
        self.result.set('Sending to '+', '.join(f'{n:04X}' for n in nodes)+'…')
        self.app.run('Manual LED · '+', '.join(f'{n:04X}' for n in nodes),lambda:fleet.manual_led(nodes,command),done,preserve_tracking=True)
    def build_rgb_panel(self):
        panel=ttk.LabelFrame(self,text='RGB LED · selected nodes')
        panel.pack(fill='x',padx=4,pady=5)
        self.rgb_values=[tk.IntVar(value=0) for _ in range(6)]
        for side in range(2):
            group=ttk.Frame(panel);group.grid(row=0,column=side,padx=10,pady=5,sticky='w')
            ttk.Label(group,text=f'LED {side+1}:').pack(side='left',padx=(0,6))
            for channel,label in enumerate(('Red','Green','Blue')):
                ttk.Checkbutton(group,text=label,variable=self.rgb_values[side*3+channel]).pack(side='left',padx=3)
        presets=ttk.Frame(panel);presets.grid(row=0,column=2,padx=8,pady=5,sticky='e')
        for name,bits in [('Red','1 0 0 1 0 0'),('Green','0 1 0 0 1 0'),('Blue','0 0 1 0 0 1'),('Off','0 0 0 0 0 0')]:
            def preset(b=bits):
                for v,bit in zip(self.rgb_values,b.split()):v.set(int(bit))
                self.send('led aux rgb '+b)
            ttk.Button(presets,text=name,width=7,command=preset).pack(side='left',padx=2)
        ttk.Button(panel,text='Apply RGB',command=lambda:self.send('led aux rgb '+' '.join(str(v.get()) for v in self.rgb_values))).grid(row=0,column=3,padx=8,pady=5)
