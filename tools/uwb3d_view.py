"""Incremental Tk canvas 3D rendering and integrated tracking controls."""
import asyncio
import json
import math
import time
from dataclasses import asdict
from copy import deepcopy
from pathlib import Path
from collections import deque
import tkinter as tk
from tkinter import ttk,filedialog,messagebox,simpledialog
import numpy as np
from cooperative_view import CooperativeView
from uwb3d_runtime import Runtime,SCENARIOS,export_csv
from uwb3d_core import Config
from position_gate import PositionGate

COLORS={'reacquired':'#79b5ff','outlier-rejected':'#ffa650','measured-supported':'#3cdbc1','estimated':'#79b5ff','predicted':'#ffbf5f','stale':'#85919d','unavailable':'#ef7777'}


class Scene(tk.Canvas):
    def __init__(self,parent):
        super().__init__(parent,bg='#102331',highlightthickness=0,height=330)
        self.yaw=.5;self.pitch=.5;self.scale=45.;self.pan=np.zeros(2);self.drag=None;self.selected=None;self.selected_nodes=set();self.follow=False
        self.projection='3D';self.nodes={};self.links={};self.trails={};self.state=None;self.last_time=None
        self.options=dict(labels=True,trails=True,links=False,grid=True,axes=True,uncertainty=False)
        self.grid_items=[self.create_line(0,0,0,0,fill='#213b4d') for _ in range(22)]
        self.axes=[(self.create_line(0,0,0,0,width=2,fill=c),self.create_text(0,0,text=l,fill=c)) for c,l in (('#ff8181','X'),('#7ddc95','Y'),('#78aaff','Z'))]
        self.title=self.create_text(14,12,anchor='nw',fill='#c7dbea',font=('Segoe UI',11,'bold'))
        self.bind('<ButtonPress-1>',self.press);self.bind('<B1-Motion>',self.move)
        self.bind('<MouseWheel>',lambda e:self.zoom(1.12 if e.delta>0 else 1/1.12))
        self.bind('<Configure>',lambda e:self.render(self.state))
    def press(self,e):
        self.drag=(e.x,e.y)
        positions={int(n):np.asarray(p) for n,p in (self.state or {}).get('positions',{}).items()}
        center=positions.get(self.selected,np.zeros(3)) if self.follow else np.zeros(3)
        candidates=[(math.hypot(self.project(p,center)[0]-e.x,self.project(p,center)[1]-e.y),n) for n,p in positions.items()]
        if candidates and min(candidates)[0]<=16:
            self.selected=min(candidates)[1]
            if self.selected in self.selected_nodes:self.selected_nodes.remove(self.selected)
            else:self.selected_nodes.add(self.selected)
            self.render(self.state)
    def move(self,e):
        if self.drag:
            dx,dy=e.x-self.drag[0],e.y-self.drag[1]
            if e.state&1:self.pan+=np.array([dx,dy])
            else:self.yaw+=dx*.01;self.pitch=max(-1.5,min(1.5,self.pitch+dy*.01))
            self.drag=e.x,e.y;self.render(self.state)
    def zoom(self,gain):self.scale=max(2,min(1000,self.scale*gain));self.render(self.state)
    def reset(self):self.yaw=.5;self.pitch=.5;self.pan[:]=0;self.scale=45.;self.render(self.state)
    def fit(self):
        if not self.state or not self.state.get('positions'):return
        points=np.array(list(self.state['positions'].values()))
        radius=max(.1,float(np.max(np.linalg.norm(points,axis=1))))
        self.scale=max(2,min(1000,(min(self.winfo_width(),self.winfo_height())-100)/(2*radius)))
        self.pan[:]=0;self.render(self.state)
    def project(self,p,center):
        p=np.asarray(p)-center
        if self.projection!='3D':
            axes={'XY':(0,1),'XZ':(0,2),'YZ':(1,2)}[self.projection];q=p[list(axes)]
        else:
            cy,sy=math.cos(self.yaw),math.sin(self.yaw);cp,sp=math.cos(self.pitch),math.sin(self.pitch)
            x=cy*p[0]-sy*p[1];y=sy*p[0]+cy*p[1];q=np.array([x,cp*p[2]-sp*y])
        return (self.winfo_width()/2+self.pan[0]+q[0]*self.scale,self.winfo_height()/2+self.pan[1]-q[1]*self.scale)
    def render(self,state):
        if not state:return
        self.state=state;positions={int(n):np.array(p) for n,p in state.get('positions',{}).items()}
        center=positions.get(self.selected,np.zeros(3)) if self.follow else np.zeros(3)
        for idx,(axis,k) in enumerate((a,k) for a in (0,1) for k in range(-5,6)):
            p=[-5.,k,0.] if axis==0 else [k,-5.,0.];q=[5.,k,0.] if axis==0 else [k,5.,0.]
            self.coords(self.grid_items[idx],*self.project(p,center),*self.project(q,center));self.itemconfigure(self.grid_items[idx],state='normal' if self.options['grid'] else 'hidden')
        for k,(line,label) in enumerate(self.axes):
            p=np.zeros(3);p[k]=2;x,y=self.project(p,center)
            self.coords(line,*self.project(np.zeros(3),center),x,y);self.coords(label,x+10,y)
            for item in (line,label):self.itemconfigure(item,state='normal' if self.options['axes'] else 'hidden')
        for items in self.nodes.values():
            for item in items:self.itemconfigure(item,state='hidden')
        for item in self.links.values():self.itemconfigure(item,state='hidden')
        for r in state.get('edges',[]):
            a,b=r['a'],r['b'];key=(a,b)
            if self.options['links'] and a in positions and b in positions:
                if key not in self.links:self.links[key]=self.create_line(0,0,0,0,fill='#365870')
                item=self.links[key];self.coords(item,*self.project(positions[a],center),*self.project(positions[b],center));self.itemconfigure(item,state='normal');self.tag_lower(item)
        for id,p in positions.items():
            if id not in self.nodes:
                self.nodes[id]=(self.create_oval(0,0,1,1,tags=(f'node:{id}',)),self.create_text(0,0,anchor='w',fill='#e1eff8',tags=(f'node:{id}',)),self.create_line(0,0,0,0,fill='#3a7c85'),self.create_oval(0,0,1,1,outline='#7795a5',dash=(2,3)))
            node_state=state.get('node_states',{}).get(id,state['state'])
            dot,label,trail,uncertainty=self.nodes[id];x,y=self.project(p,center);color=COLORS.get(node_state,'white')
            self.coords(dot,x-6,y-6,x+6,y+6);self.itemconfigure(dot,fill=color,outline='white' if id in self.selected_nodes else color,width=3,state='normal')
            name='SNIFFER (0,0)' if id==state.get('metrics',{}).get('origin_node') else f'{id:04X}'
            if node_state=='outlier-rejected':name+=' · HELD'
            elif id in state.get('stationary_held',[]):name+=' · QUIET LOCK'
            self.coords(label,x+10,y);self.itemconfigure(label,text=name,state='normal' if self.options['labels'] else 'hidden')
            history=self.trails.setdefault(id,deque(maxlen=100))
            if id in state.get('position_reacquired',[]):history.clear()
            if state['time']!=self.last_time and node_state in ('measured-supported','estimated'):history.append(p.copy())
            if self.options['trails'] and len(history)>1:
                self.coords(trail,*[v for point in history for v in self.project(point,center)]);self.itemconfigure(trail,state='normal')
            sigma=state.get('uncertainty')
            if self.options['uncertainty'] and sigma is not None:
                radius=min(200,max(2,2*sigma*self.scale));self.coords(uncertainty,x-radius,y-radius,x+radius,y+radius);self.itemconfigure(uncertainty,state='normal')
        self.last_time=state['time']
        model='PLANAR 2D · Z=0 assumed' if state.get('dimensions',3)==2 else 'RELATIVE 3D'
        if state.get('metrics',{}).get('cooperative'):model='ALL MOBILE · CENTROID-RELATIVE XY · Z=0 assumed'
        if state.get('metrics',{}).get('origin_node'):model='SNIFFER ORIGIN · RELATIVE HEADING · Z=0 assumed'
        self.itemconfigure(self.title,text=f"{state.get('source','').upper()}  |  {model}  |  {self.projection}  |  {state['state'].upper()}\n"+(state.get('reason','') if not positions else 'Orbit: drag · Pan: Shift-drag · Zoom: wheel · metres'))
        self.tag_raise(self.title)


class UWB3DView(ttk.Frame):
    def __init__(self,parent,app):
        super().__init__(parent);self.app=app;self.runtime=Runtime(app.loop);self.pending=None;self.paused=False;self.last_render=deque(maxlen=100);self.last_snapshot={};self.position_gate=PositionGate();self.gate_start=None;self.gate_generation=None
        tabs=ttk.Notebook(self);tabs.pack(fill='both',expand=True);self.tabs=tabs
        live=ttk.Frame(tabs,padding=8);tabs.add(live,text='3D network')
        self.legacy=CooperativeView(tabs,lambda:app.prepare_multi(True),lambda:app.start_multi(True),app.stop_fleet)
        tabs.add(self.legacy,text='Legacy 4-anchor cooperative')
        matrix=ttk.Frame(tabs,padding=8);tabs.add(matrix,text='Distance matrix');self.matrix_page=matrix
        ttk.Label(live,text='UWB 3D Tracking · mobile nodes · relative frame',font=('Segoe UI',16,'bold')).pack(anchor='w')
        controls=ttk.Frame(live);controls.pack(fill='x',pady=5)
        self.source=tk.StringVar(value='simulation');ttk.Combobox(controls,textvariable=self.source,values=('swarm','simulation','replay','uart','gateway','reference','legacy'),width=11,state='readonly').pack(side='left')
        self.count=tk.IntVar(value=30);ttk.Spinbox(controls,from_=2,to=30,textvariable=self.count,width=4).pack(side='left',padx=4)
        self.scenario=tk.StringVar(value='Random motion');ttk.Combobox(controls,textvariable=self.scenario,values=SCENARIOS,width=18,state='readonly').pack(side='left')
        self.port=tk.StringVar();ttk.Entry(controls,textvariable=self.port,width=8).pack(side='left',padx=4)
        ttk.Button(controls,text='Ports',command=self.ports).pack(side='left')
        ttk.Button(controls,text='Start',command=self.start).pack(side='left',padx=4)
        ttk.Button(controls,text='Stop',command=lambda:self.submit(self.runtime.stop())).pack(side='left')
        ttk.Button(controls,text='Pause view',command=self.pause).pack(side='left',padx=4)
        ttk.Button(controls,text='Reset frame',command=lambda:self.submit(self.runtime.reset())).pack(side='left')
        ttk.Button(controls,text='Reference setup',command=self.reference_setup).pack(side='left')
        ttk.Button(controls,text='Swarm calibration',command=self.swarm_calibration).pack(side='left')
        second=ttk.Frame(live);second.pack(fill='x')
        self.mode=tk.StringVar(value='Full mesh simulation');ttk.Combobox(second,textvariable=self.mode,state='readonly',width=23,values=('Full mesh simulation','Sparse simulation')).pack(side='left')
        self.rate=tk.DoubleVar(value=10);ttk.Label(second,text='Publish Hz').pack(side='left');ttk.Spinbox(second,from_=1,to=30,textvariable=self.rate,width=4).pack(side='left')
        self.age=tk.DoubleVar(value=.5);ttk.Label(second,text='Age s').pack(side='left');ttk.Spinbox(second,from_=.1,to=5,increment=.1,textvariable=self.age,width=4).pack(side='left')
        self.filtering=tk.BooleanVar(value=True);ttk.Checkbutton(second,text='Kalman',variable=self.filtering).pack(side='left',padx=4)
        self.range_filtering=tk.BooleanVar(value=True);ttk.Checkbutton(second,text='Range filter',variable=self.range_filtering).pack(side='left')
        self.imu_gate=tk.BooleanVar(value=True);ttk.Checkbutton(second,text='IMU gate',variable=self.imu_gate).pack(side='left')
        self.speed=tk.DoubleVar(value=1);ttk.Label(second,text='Replay ×').pack(side='left');ttk.Spinbox(second,from_=.25,to=8,increment=.25,textvariable=self.speed,width=4).pack(side='left')
        self.seek=tk.DoubleVar(value=0);ttk.Label(second,text='Seek s').pack(side='left');ttk.Entry(second,textvariable=self.seek,width=5).pack(side='left')
        ttk.Button(second,text='Pause replay',command=self.pause_replay).pack(side='left')
        ttk.Button(second,text='Config',command=self.config_dialog).pack(side='left')
        ttk.Button(second,text='Register node',command=self.register).pack(side='left')
        self.scene=Scene(live);self.scene.pack(fill='both',expand=True,pady=6)
        options=ttk.Frame(live);options.pack(fill='x')
        self.sniffer_origin=tk.BooleanVar(value=False)
        ttk.Checkbutton(options,text='Sniffer origin (stationary)',variable=self.sniffer_origin).pack(side='left')
        self.projection=tk.StringVar(value='3D');box=ttk.Combobox(options,textvariable=self.projection,values=('3D','XY','XZ','YZ'),width=4,state='readonly');box.pack(side='left');box.bind('<<ComboboxSelected>>',lambda e:self.project())
        self.geometry=tk.StringVar(value='Relative 3D')
        ttk.Combobox(options,textvariable=self.geometry,values=('Relative 3D','Tabletop 2D'),width=12,state='readonly').pack(side='left')
        for key in self.scene.options:
            var=tk.BooleanVar(value=self.scene.options[key]);ttk.Checkbutton(options,text=key.title(),variable=var,command=lambda k=key,v=var:self.option(k,v.get())).pack(side='left')
        self.follow=tk.BooleanVar();ttk.Checkbutton(options,text='Follow',variable=self.follow,command=lambda:setattr(self.scene,'follow',self.follow.get())).pack(side='left')
        ttk.Button(options,text='Camera reset',command=self.scene.reset).pack(side='left')
        ttk.Button(options,text='Fit',command=self.scene.fit).pack(side='left')
        ttk.Button(options,text='Record',command=self.record).pack(side='left')
        ttk.Button(options,text='End log',command=lambda:self.submit(self.runtime.recording())).pack(side='left')
        ttk.Button(options,text='Export CSV',command=self.export).pack(side='left')
        self.dashboard=tk.StringVar(value='Choose a source. UART is passive U3D v1 input; existing firmware ASCII is not binary gateway data.')
        ttk.Label(live,textvariable=self.dashboard,wraplength=1060).pack(anchor='w',pady=4)
        columns=('id','connection','xyz','speed','age','neighbors','confidence','battery','state')
        self.table=ttk.Treeview(live,columns=columns,show='headings',height=5)
        for col in columns:self.table.heading(col,text=col.title());self.table.column(col,width=90 if col!='xyz' else 210)
        self.table.pack(fill='x');self.table.bind('<<TreeviewSelect>>',self.select)
        ttk.Label(live,text='No absolute world pose. Reflection is aligned for continuity. IMU norm telemetry supports motion gating only; vector fusion unavailable.').pack(anchor='w',pady=3)
        self.heatmap=tk.Canvas(matrix,height=340,bg='#102331',highlightthickness=0);self.heatmap.pack(fill='x')
        self.heatmap.bind('<ButtonRelease-1>',self.heatmap_detail);self.heat_cells={}
        self.matrix=ttk.Treeview(matrix,show='headings',height=8);self.matrix.pack(fill='both',expand=True)
        scroll=ttk.Scrollbar(matrix,orient='horizontal',command=self.matrix.xview);scroll.pack(fill='x');self.matrix.configure(xscrollcommand=scroll.set)
        self.matrix.bind('<ButtonRelease-1>',self.link_detail);self.link_text=tk.StringVar(value='Select a matrix cell for age and quality. — means missing/stale, never zero distance.')
        ttk.Label(matrix,textvariable=self.link_text,wraplength=1000).pack(anchor='w')
        self.history=tk.Canvas(matrix,height=100,bg='#102331');self.history.pack(fill='x')
        self.link_samples={};self.matrix_ids=[];self.message='';self.after(100,self.tick)
    def submit(self,coro):
        if self.pending and not self.pending.done():coro.close();return
        self.pending=asyncio.run_coroutine_threadsafe(coro,self.app.loop)
    def ports(self):
        from serial.tools.list_ports import comports
        messagebox.showinfo('UART ports','\n'.join(f'{p.device}: {p.description}' for p in comports()) or 'No UART ports')
    def start(self):
        try:
            self.runtime.config.dimensions=2 if self.geometry.get()=='Tabletop 2D' else 3
            self.runtime.config.target_rate_hz=self.rate.get();self.runtime.config.max_age=self.age.get();self.runtime.config.filtering=self.filtering.get();self.runtime.config.imu_motion_gating=self.imu_gate.get();self.runtime.config.validate()
            self.runtime.config.range_filtering=self.range_filtering.get()
            source=self.source.get();kwargs={}
            if source=='simulation':kwargs=dict(count=self.count.get(),scenario=self.scenario.get(),sparse=self.mode.get().startswith('Sparse'))
            elif source=='replay':
                path=filedialog.askopenfilename(filetypes=[('U3D sessions','*.jsonl')])
                if not path:return
                kwargs=dict(path=path,speed=self.speed.get(),seek=self.seek.get())
            elif source in ('uart','gateway','reference','swarm'):
                if self.app.busy or self.app.fleet is not None:raise ValueError('Stop the existing fleet and wait for hardware operations first')
                port=self.port.get().strip()
                if not port:raise ValueError('Enter the gateway UART COM port; ST-Link is not UART')
                if any(getattr(s,'port',None)==port for s in self.app.sessions.values()):raise ValueError('Port already used by Control Center')
                kwargs=dict(port=port)
                if source in ('gateway','reference','swarm'):
                    if self.app.sessions:raise ValueError('Disconnect individual control sessions before configuring the gateway fleet')
                    boards=[dict(b) for b in self.app.selected()]
                    if source=='swarm':
                        if not 4<=len(boards)<=18:raise ValueError('Swarm UART: select one sniffer plus 3–17 mobile nodes')
                        self.runtime.config.swarm_origin=len(boards) if self.sniffer_origin.get() else 0
                        self.geometry.set('Tabletop 2D');self.projection.set('XY');self.project();self.rate.set(25)
                    if source=='reference' and not 2<=len(boards)<=9:raise ValueError('Reference mode: select 2–9 boards in Boards & OTA')
                    if not 2<=len(boards)<=30:raise ValueError('Select 2–30 boards in Boards & OTA')
                    gateway=simpledialog.askstring('UWB collector / sniffer' if source=='swarm' else 'Mobile coordinator / UART gateway','Exact board name connected to this UART port:',initialvalue=boards[0]['name'])
                    if gateway is None:return
                    if gateway not in {b['name'] for b in boards}:raise ValueError('Gateway must be a selected board')
                    kwargs['boards']=sorted(boards,key=lambda b:(b['name']!=gateway,b['name']))
            self.scene.trails.clear();self.link_samples.clear();self.submit(self.runtime.start(source,**kwargs))
        except Exception as exc:messagebox.showerror('3D tracking',str(exc))
    def swarm_calibration(self):
        if self.runtime.tasks:
            messagebox.showinfo('Swarm calibration','Stop tracking before changing calibration.');return
        path=filedialog.askopenfilename(title='Residual body-mounted pair calibration (hardware UIDs)',filetypes=[('Calibration JSON','*.json')])
        if path:self.runtime.config.swarm_calibration=path
    def reference_setup(self):
        if self.runtime.tasks:
            messagebox.showinfo('Reference DS-TWR','Stop the current session before changing reference settings.');return
        c=self.runtime.config
        value=simpledialog.askstring('Albifache reference TDMA / DS-TWR',
            'Requires reference firmware 1.1.0 via Boards & OTA.\n2–9 nodes; coordinator is ID 1.\nEnter period ms, tag ID (0 rotates), anchor mask in hex (0 = all other nodes).\nUnselected roles listen. STS/AES enabled; bench keys, security not qualified.',
            initialvalue=f'{c.reference_period_ms},{c.reference_tag},{c.reference_anchor_mask:X}')
        if value is None:return
        try:
            period,tag,mask=value.split(',');period=int(period);tag=int(tag);mask=int(mask,16)
            if not 150<=period<=5000 or not 0<=tag<=9 or not 0<=mask<512:raise ValueError('Invalid reference settings')
            c.reference_period_ms=period;c.reference_tag=tag;c.reference_anchor_mask=mask
            c.max_age=max(c.max_age,2.0);self.age.set(c.max_age);self.source.set('reference')
            self.message='Reference: INIT / REQUEST / RESPONSE / REPORT / FINAL; requires reference.signed.bin'
            self.projection.set('XY');self.project()
            self.dashboard.set(self.message+' · stopped · select 2–9 boards and gateway UART')
        except ValueError as exc:messagebox.showerror('Reference setup',str(exc))
    def pause(self):self.paused=not self.paused
    def pause_replay(self):self.runtime.replay_paused=not self.runtime.replay_paused
    def project(self):self.scene.projection=self.projection.get();self.scene.render(self.last_snapshot)
    def option(self,key,value):self.scene.options[key]=value;self.scene.render(self.last_snapshot)
    def select(self,e=None):
        if self.table.selection():self.scene.selected=int(self.table.selection()[0]);self.scene.render(self.last_snapshot)
    def record(self):
        name=filedialog.asksaveasfilename(defaultextension='.jsonl',initialfile='uwb3d-session.jsonl')
        if name:self.submit(self.runtime.recording(name))
    def export(self):
        name=filedialog.askopenfilename(filetypes=[('U3D sessions','*.jsonl')])
        if not name:return
        target=filedialog.asksaveasfilename(defaultextension='.csv')
        if target:
            async def job():await asyncio.to_thread(export_csv,name,target)
            self.submit(job())
    def config_dialog(self):
        action=messagebox.askyesnocancel('Configuration','Yes: save current configuration. No: load a JSON configuration.')
        if action is None:return
        if action:
            name=filedialog.asksaveasfilename(defaultextension='.json')
            if name:Path(name).write_text(json.dumps(asdict(self.runtime.config),indent=2))
        else:
            if self.runtime.tasks:messagebox.showinfo('Configuration','Stop 3D tracking before loading settings.');return
            name=filedialog.askopenfilename(filetypes=[('JSON','*.json')])
            if name:
                try:
                    self.runtime.config=Config(**json.loads(Path(name).read_text())).validate()
                    self.geometry.set('Tabletop 2D' if self.runtime.config.dimensions==2 else 'Relative 3D')
                    self.rate.set(self.runtime.config.target_rate_hz);self.age.set(self.runtime.config.max_age);self.filtering.set(self.runtime.config.filtering);self.imu_gate.set(self.runtime.config.imu_motion_gating)
                    self.range_filtering.set(self.runtime.config.range_filtering)
                except Exception as exc:messagebox.showerror('Configuration',str(exc))
    def register(self):
        value=simpledialog.askstring('Manual node','Node ID (decimal), hardware UID (decimal), name')
        if not value:return
        try:
            id,uid,name=value.split(',',2);id=int(id);uid=str(int(uid))
            async def job():await self.app.loop.run_in_executor(self.runtime.executor,lambda:self.runtime.engine.registry.register(id,uid,time.monotonic(),name.strip()))
            self.submit(job())
        except Exception as exc:messagebox.showerror('Node registration',str(exc))
    def filtered_snapshot(self):
        source=self.runtime.snapshot() or {};now=time.monotonic()
        if not source:return {}
        if self.gate_generation!=self.runtime.generation:
            self.gate_start=None;self.gate_generation=self.runtime.generation
        if self.runtime.source=='swarm' and self.gate_start!='ready':
            # Do not establish speed baselines from a partially configured fleet.
            complete=len(source.get('positions',{}))>=self.count.get()
            if not complete or 'configuring' in source.get('error',''):
                self.gate_start=None
            elif self.gate_start is None:self.gate_start=now
            if self.gate_start is not None and now-self.gate_start>=1.:
                self.gate_start='ready'
            else:
                waiting=deepcopy(source);waiting['positions']={}
                waiting['reason']='Waiting for full fleet before initializing speed gate'
                return waiting
        return self.position_gate.update(source,self.runtime.generation,now)

    def tick(self):
        tick_started=time.monotonic()
        if self.pending and self.pending.done():
            try:self.pending.result();self.message=''
            except Exception as exc:self.message=str(exc)
            self.pending=None
        state=self.filtered_snapshot()
        if state and not self.paused:
            self.last_snapshot=state;self.scene.render(state);now=time.monotonic();self.last_render.append(now)
            for node in state['nodes']:
                id=node['id'];p=state['positions'].get(id);velocity=state['velocity'].get(id)
                neighbors=sum(id in (r['a'],r['b']) for r in state['edges']);online=id in state['ids']
                node_uncertainty=state['metrics'].get('node_quality',{}).get(id,{}).get('uncertainty',state['uncertainty'])
                values=(f'{id:04X}','ranging' if online else 'no fresh ranges',' / '.join(f'{v:.2f}' for v in p) if p is not None else '—',
                    f'{np.linalg.norm(velocity):.2f}' if velocity is not None else '—',f'{now-node["last"]:.2f}s',neighbors,
                    f'±{node_uncertainty:.2f}m*' if p is not None and node_uncertainty is not None else '—',
                    '—' if node['battery'] is None else f'{node["battery"]:.1f}%',state.get('node_states',{}).get(id,state['state'] if online else 'stale'))
                if self.table.exists(str(id)):self.table.item(str(id),values=values)
                else:self.table.insert('','end',iid=str(id),values=values)
            present={str(n['id']) for n in state['nodes']}
            for id in self.table.get_children():
                if id not in present:self.table.delete(id)
            m=state['metrics'];parser=m['parser'];seen=parser.get('accepted',0);lost=parser.get('dropped',0)
            hz=sum(now-t<1 for t in self.last_render);latency=max(0.,now-state.get('published_at',state['time']))*1000
            age='—' if state['age'] is None else f"{state['age']:.3f}"
            net=state['network'];mode={2:'experimental sparse BP',3:'reference TDMA / STS DS-TWR',4:'Swarm: weighted SMACOF / Kabsch / EKF'}.get(net.get('mode'),self.mode.get() if state['source']=='simulation' else 'unknown')
            error=f' · SIM aligned RMSE {state["simulation_rmse"]:.3f}m' if 'simulation_rmse' in state else ''
            self.dashboard.set(f"{state['source'].upper()} · {len(state['ids'])}/{len(state['nodes'])} online · links {m['valid_edges']}/{m['possible_edges']} · stale {m['stale_edges']} · packet loss {lost}/{seen+lost}\n"
                f"Ranges {m['measurement_hz']}/s · localization {m['localization_hz']}/s · render {hz}/s · solver {m['solver_ms']:.1f}ms · publication→render {latency:.1f}ms · range age {age}s{error} · range filter {'on' if m.get('range_filtering') else 'off'} / spikes {m.get('range_spikes_rejected',0)}\n"
                f"{state['reason']} · {state.get('error','')} {self.message} · drops {m['queue_drops']}/{m['log_drops']} · {mode} · frame {net.get('superframe','—')} · sync {net.get('synchronized','unknown')} · *relative conditional uncertainty")
            self.update_matrix(state)
        self.after(max(1,(40 if self.source.get()=='swarm' else 100)-round((time.monotonic()-tick_started)*1000)),self.tick)
    def update_matrix(self,state):
        ids=state['ids']
        if ids!=self.matrix_ids:
            self.matrix_ids=list(ids);self.matrix.configure(columns=['node']+[str(n) for n in ids])
            self.heatmap.delete('all');self.heat_cells={};cell=300/max(1,len(ids))
            self.heatmap.create_text(370,35,anchor='nw',fill='#d8e8ee',text='Distance heatmap · metres\nDark = missing / stale\nBlue → cyan = near → far\nClick a cell for link history')
            for i,id in enumerate(ids):
                if i%max(1,len(ids)//10)==0:
                    self.heatmap.create_text(28,30+(i+.5)*cell,text=f'{id:04X}',fill='#c7dbea',anchor='e')
                    self.heatmap.create_text(35+(i+.5)*cell,18,text=f'{id:04X}',fill='#c7dbea',angle=45)
                for j in range(len(ids)):
                    self.heat_cells[i,j]=self.heatmap.create_rectangle(35+j*cell,30+i*cell,35+(j+1)*cell,30+(i+1)*cell,outline='#233849')
            for col in self.matrix['columns']:self.matrix.heading(col,text='Node' if col=='node' else f'{int(col):04X}');self.matrix.column(col,width=65,stretch=False)
            for row in self.matrix.get_children():self.matrix.delete(row)
            for id in ids:self.matrix.insert('','end',iid=str(id))
        for i,id in enumerate(ids):self.matrix.item(str(id),values=[f'{id:04X}']+['—' if d is None else f'{d:.2f}' for d in state['matrix'][i]])
        ceiling=max((d for row in state['matrix'] for d in row if d is not None),default=1) or 1
        for (i,j),item in (self.heat_cells.items() if self.tabs.select()==str(self.matrix_page) else ()):
            d=state['matrix'][i][j];level=0 if d is None else min(1,d/ceiling)
            color='#18222d' if d is None else '#505967' if i==j else f'#{int(35+25*level):02x}{int(60+155*level):02x}{int(120+75*level):02x}'
            self.heatmap.itemconfigure(item,fill=color)
        for r in state['edges']:
            key=tuple(sorted((r['a'],r['b'])));history=self.link_samples.setdefault(key,deque(maxlen=100))
            if not history or history[-1][0]!=r['effective']:history.append((r['effective'],r['distance']))
    def link_detail(self,e):
        row=self.matrix.identify_row(e.y);column=self.matrix.identify_column(e.x)
        if not row or not column:return
        index=int(column[1:])-2
        if not 0<=index<len(self.matrix_ids):return
        self.show_link(int(row),self.matrix_ids[index])
    def heatmap_detail(self,e):
        if not self.matrix_ids:return
        cell=300/len(self.matrix_ids);i=int((e.y-30)//cell);j=int((e.x-35)//cell)
        if 0<=i<len(self.matrix_ids) and 0<=j<len(self.matrix_ids):self.show_link(self.matrix_ids[i],self.matrix_ids[j])
    def show_link(self,a,b):
        r=next((r for r in self.last_snapshot['edges'] if {r['a'],r['b']}=={a,b}),None)
        self.link_text.set(f'{a:04X} ↔ {b:04X}: '+(f"{r['distance']:.4f}m · age {time.monotonic()-r['effective']:.3f}s · quality {r['quality']} · NLOS {r['nlos']}" if r else 'missing / stale'))
        self.history.delete('all');data=self.link_samples.get(tuple(sorted((a,b))),[])
        if len(data)>1:
            ds=[d for _,d in data];low=min(ds);span=max(.01,max(ds)-low);width=max(100,self.history.winfo_width())
            self.history.create_line(*[v for i,d in enumerate(ds) for v in (10+i*(width-20)/(len(ds)-1),90-80*(d-low)/span)],fill='#3cdbc1',width=2)
