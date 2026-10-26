"""Dedicated SAR mission tab consuming the existing tracking snapshot."""
import time,math
from pathlib import Path
import tkinter as tk
from tkinter import ttk,messagebox,filedialog,simpledialog
from virtual_geofencing import Mission,BioProfile
from cockroach_mapping import InferenceConfig
from live_trails import LiveTrails
from region_overlay import render_regions
from PIL import ImageTk

class VirtualGeofencingView(ttk.Frame):
    def __init__(self,parent,app):
        super().__init__(parent,padding=10);self.app=app;self.mission=Mission();self.scale=100.;self.corner=None;self.drawing=False;self.saved=0.;self.error=''
        ttk.Label(self,text='Virtual Geofencing Finding',font=('Segoe UI',18,'bold')).pack(anchor='w')
        ttk.Label(self,text='Multi-tag exploration • observed passages • behavioral obstacle hypotheses • sniffer origin').pack(anchor='w')
        bar=ttk.Frame(self);bar.pack(fill='x',pady=6)
        for title,command in [('Tracking',lambda:parent.select(app.uwb3d)),('Draw fence',self.draw_fence),('Fence coordinates',self.coordinates),('Start mission',self.start),('Pause',self.pause),('New mission',self.new),('Fit',self.fit),('Open mission',self.open_mission),('Export',self.export),('Clear trajectories',self.clear_trajectories)]:
            ttk.Button(bar,text=title,command=command).pack(side='left',padx=2)
        self.message=tk.StringVar(value=self.mission.note);ttk.Label(self,textvariable=self.message,wraplength=1150).pack(anchor='w')
        self.canvas=tk.Canvas(self,bg='#102331',height=350,highlightthickness=0);self.canvas.pack(fill='both',expand=True,pady=6)
        self.canvas.bind('<Button-1>',self.click);self.canvas.bind('<MouseWheel>',self.zoom)
        self.canvas.bind('<Configure>',lambda e:self.render())
        self.auto_enabled=tk.BooleanVar(value=True)
        ttk.Checkbutton(self,text='Automatic fences — observed-area boundary + suspected-obstacle warnings',variable=self.auto_enabled,command=self.toggle_auto).pack(anchor='w')
        ttk.Label(self,text='Post-UWB outlier filter: 0.60 m/s hard limit; held + verified reacquisition; source localization unchanged').pack(anchor='w')
        self.fence_info=tk.StringVar();ttk.Label(self,textvariable=self.fence_info).pack(anchor='w')
        self.map_visible=tk.BooleanVar(value=True)
        legend=ttk.Frame(self);legend.pack(fill='x')
        ttk.Button(legend,text='Inference settings',command=self.inference_settings).pack(side='right')
        ttk.Button(legend,text='Evidence',command=self.evidence).pack(side='right',padx=3)
        ttk.Checkbutton(legend,text='Behavior map',variable=self.map_visible,command=self.render).pack(side='left')
        ttk.Label(legend,text='White / black outline: observed area · Red outline: suspected obstacle').pack(side='left')
        ttk.Label(self,text='Solid trails: accepted paths · Dotted trails: live estimates · Dark background: unknown').pack(anchor='w')
        self.map_info=tk.StringVar(value='Click a map cell to inspect supporting observations.');ttk.Label(self,textvariable=self.map_info,wraplength=1150).pack(anchor='w')
        bottom=ttk.Frame(self);bottom.pack(fill='x')
        self.node=tk.StringVar();self.pick=ttk.Combobox(bottom,textvariable=self.node,width=8,state='readonly');self.pick.pack(side='left')
        ttk.Label(bottom,text='Tracking only · biological model OFF').pack(side='left',padx=4)
        ttk.Button(bottom,text='Mark suspected victim',command=lambda:self.marker(False)).pack(side='left')
        ttk.Button(bottom,text='Mark confirmed victim',command=lambda:self.marker(True)).pack(side='left',padx=4)
        self.route=tk.StringVar(value='All traces');self.routes=ttk.Combobox(bottom,textvariable=self.route,values=('All traces',),state='readonly',width=18);self.routes.pack(side='left',padx=5)
        self.route_info=tk.StringVar();ttk.Label(self,textvariable=self.route_info,wraplength=1100).pack(anchor='w')
        self.table=ttk.Treeview(self,columns=('tag','fence','bio','behavior','guidance','speed','trace'),show='headings',height=5)
        for c in self.table['columns']:self.table.heading(c,text='Motion' if c=='bio' else c.title());self.table.column(c,width=140)
        self.table.pack(fill='x',pady=4)
        ttk.Label(self,text='Original UWB + IMU tracking. Biological speed limits and activity/rest models are disabled.\nAn insect trace does not establish human passability. Tracking heading and uncertainty remain relative.',wraplength=1100).pack(anchor='w')
        self.region_cache=None;self.region_photo=None;self.region_labels=[]
        self.snapshot={};self.position_gate=self.app.uwb3d.position_gate;self.live_trails=LiveTrails();self.after(100,self.tick)
    def protect(self,fn):
        try:fn();self.error=''
        except Exception as exc:messagebox.showerror('Virtual Geofencing',str(exc),parent=self)
    def inference_settings(self):
        def edit():
            m=self.mission
            if m.running or m.tracks:raise ValueError('Set inference parameters in a new, not-yet-recorded mission to preserve experiment reproducibility.')
            cfg=m.behavior_map.config
            value=simpledialog.askstring('Behavior inference settings',
                'Half-life (s), activation score, release score, maximum position sigma (m).\nExperimental thresholds; score is NOT a probability. At least 3 turn episodes required.',
                initialvalue=f'{cfg.half_life_s},{cfg.activate_score},{cfg.release_score},{cfg.max_sigma_m}',parent=self)
            if value is not None:m.behavior_map.config=InferenceConfig(*map(float,value.split(','))).validate()
        self.protect(edit)
    def evidence(self):
        dialog=tk.Toplevel(self);dialog.title('Behavioral fence evidence');dialog.geometry('950x450')
        ttk.Label(dialog,text='Evidence score decays with age; it is not a calibrated probability. Repeated turns infer a barrier; later traversal retracts it.',wraplength=920).pack(anchor='w',padx=8,pady=8)
        table=ttk.Treeview(dialog,columns=('cell','state','score','episodes','nodes','age'),show='headings')
        for col in table['columns']:table.heading(col,text=col.title());table.column(col,width=140)
        table.pack(fill='both',expand=True,padx=8)
        def refresh():
            for item in table.get_children():table.delete(item)
            for row in self.mission.behavior_map.cells():
                table.insert('','end',values=(str(row['cell']),row['state'],f"{row.get('score',0):.2f}",row.get('episodes',row.get('visits',0)),','.join(f'{n:04X}' for n in row['nodes']),f"{row.get('age_s',0):.1f}s"))
            for key,h in self.mission.behavior_map.hypotheses.items():
                if h['contradicted']:table.insert('','end',values=(str(key),'RETRACTED / TRAVERSED','0',0,'',''))
        ttk.Button(dialog,text='Refresh evidence',command=refresh).pack(pady=6);refresh()
    def toggle_auto(self):
        if self.mission.running:
            self.auto_enabled.set(self.mission.auto_fence)
            messagebox.showinfo("Fence", "Pause mission before switching fence mode.",parent=self);return
        self.mission.auto_fence=self.auto_enabled.get();self.render()
    def start(self):
        self.drawing=False;self.corner=None
        self.protect(lambda:self.mission.start(self.app.uwb3d.runtime.snapshot() or {},self.app.uwb3d.runtime.generation,time.monotonic()))
    def pause(self):self.mission.pause(time.monotonic());self.autosave()
    def new(self):
        if self.mission.tracks or self.mission.markers:self.autosave()
        self.mission=Mission();self.saved=0.;self.corner=None;self.drawing=False
        self.live_trails=LiveTrails();self.app.uwb3d.scene.trails.clear()
        if hasattr(self.app,'led_tracking'):self.app.led_tracking.scene.trails.clear()
        self.route.set('All traces')
        for item in self.table.get_children():self.table.delete(item)
    def clear_trajectories(self):
        def clear():
            # Keep a separate recoverable snapshot; normal autosave must not overwrite it.
            if self.mission.tracks or self.mission.markers:
                name=f'before-trajectory-reset-{time.time_ns()}.json'
                self.mission.save(self.app.output/'geofencing'/name)
            self.mission.clear_trajectories(time.monotonic())
            self.live_trails=LiveTrails();self.app.uwb3d.scene.trails.clear()
            if hasattr(self.app,'led_tracking'):self.app.led_tracking.scene.trails.clear()
            self.route.set('All traces');self.routes.configure(values=('All traces',))
            self.route_info.set('');self.autosave();self.render()
        self.protect(clear)
    def draw_fence(self):
        if self.mission.running:messagebox.showinfo('Fence','Pause mission before editing its boundary.',parent=self);return
        self.drawing=True;self.corner=None;self.message.set('Click two opposite corners of the search rectangle. Coordinates are metres from sniffer.')
    def coordinates(self):
        value=simpledialog.askstring('Search fence','Enter x_min,y_min,x_max,y_max in metres from sniffer',initialvalue='-2,-2,2,2',parent=self)
        if value is not None:self.protect(lambda:self.mission.set_rectangle(*map(float,value.split(','))))
    def project(self,p):return self.canvas.winfo_width()/2+p[0]*self.scale,self.canvas.winfo_height()/2-p[1]*self.scale
    def click(self,event):
        p=((event.x-self.canvas.winfo_width()/2)/self.scale,(self.canvas.winfo_height()/2-event.y)/self.scale)
        if not self.drawing:
            candidates=[(math.hypot(self.project(pos)[0]-event.x,self.project(pos)[1]-event.y),int(n)) for n,pos in self.snapshot.get('positions',{}).items()]
            if candidates and min(candidates)[0]<=16:
                n=min(candidates)[1];selected=self.app.uwb3d.scene.selected_nodes
                if n in selected:selected.remove(n)
                else:selected.add(n)
                self.render();return
            key=list(self.mission.behavior_map.key(p));cell=next((r for r in reversed(self.mission.behavior_map.cells()) if r['cell']==key),None)
            self.map_info.set(f"{cell['state']} · tags {cell['nodes']} · {cell.get('episodes',cell.get('visits',0))} observations · types {','.join(cell.get('evidence_types',[]))} · score {cell.get('score',0):.2f} · age {cell.get('age_s',0):.1f}s · heuristic score, not probability" if cell else 'UNKNOWN: no traversability evidence; absence of a path is not an obstacle.')
            return
        if self.corner is None:self.corner=p
        else:
            self.protect(lambda:self.mission.set_rectangle(*self.corner,*p));self.drawing=False;self.corner=None
        self.render()
    def zoom(self,event):self.scale=max(5,min(1500,self.scale*(1.15 if event.delta>0 else 1/1.15)));self.render()
    def fit(self):
        points=list(self.mission.polygon)+[p[:2] for p in self.snapshot.get('positions',{}).values()]+[r['xy'] for rows in self.mission.tracks.values() for r in rows]+[r['xy'] for rows in self.live_trails.rows.values() for r in rows]+[r['center'] for r in self.mission.behavior_map.cells()]
        if points:self.scale=max(5,min(1500,(min(self.canvas.winfo_width(),self.canvas.winfo_height())-80)/(2*max(.2,max(math.hypot(*p) for p in points)))))
        self.render()
    def selected(self):
        if not self.node.get():raise ValueError('Select a tracked tag')
        return int(self.node.get(),16)
    def profile(self):
        def edit():
            n=self.selected();p=self.mission.profiles.get(n,BioProfile())
            value=simpledialog.askstring('Individual biological profile','Maximum plausible speed (m/s): rejects mapping outliers. Active budget (s), quiet rest (s).\nThese values require validation for your individual animal.',initialvalue=f'{p.max_speed},{p.active_seconds},{p.rest_seconds}',parent=self)
            if value is not None:
                self.mission.profiles[n]=BioProfile(*map(float,value.split(','))).validate()
                self.mission.event('profile',n,time.monotonic(),value);self.autosave()
        self.protect(edit)
    def marker(self,confirmed):
        def mark():
            n=self.selected();label=simpledialog.askstring('Victim observation','Observation label / confirmation source (operator)',parent=self)
            if label is None:return
            marker=self.mission.mark(n,label,confirmed,now=time.monotonic());self.autosave()
            messagebox.showinfo('Observation saved',f"V{marker['id']}: {marker['route_status']}\nHuman passability has not been assessed.",parent=self)
        self.protect(mark)
    def export(self):
        path=filedialog.asksaveasfilename(parent=self,title='Export mission and observed routes',defaultextension='.json',filetypes=[('Mission JSON','*.json')])
        if path:self.protect(lambda:self.mission.export(path))
    def open_mission(self):
        path=filedialog.askopenfilename(parent=self,title='Review a recorded mission',filetypes=[('Mission JSON','*.json')])
        if path:
            def load():
                loaded=Mission.load(path)
                if self.mission.tracks:self.autosave()
                self.mission=loaded
                for item in self.table.get_children():self.table.delete(item)
                self.fit()
            self.protect(load)
    def autosave(self):
        name=self.mission.created.replace(':','').replace('+','_')
        try:self.mission.save(self.app.output/'geofencing'/f'{name}.json');self.error=''
        except OSError as exc:self.error=f'Mission save failed: {exc}'
        self.saved=time.monotonic()
    def render(self):
        c=self.canvas;c.delete('all');w=c.winfo_width();h=c.winfo_height()
        for v in range(-20,21):
            x,y=self.project((v,0));c.create_line(x,0,x,h,fill='#213b4d')
            x,y=self.project((0,v));c.create_line(0,y,w,y,fill='#213b4d')
        if self.map_visible.get():
            layers={}
            for row in self.mission.behavior_map.cells():layers.setdefault(row['state'],set()).add(tuple(row['cell']))
            if self.mission.auto_fence:
                layers['FRONTIER']=self.mission.automatic_fences.frontier
                layers['CAUTION']=self.mission.automatic_fences.caution
            layers['_OBSERVED_POINTS']={(round(r['xy'][0],2),round(r['xy'][1],2)) for rows in self.mission.tracks.values() for r in rows if r.get('sigma',1)<=self.mission.behavior_map.config.max_sigma_m}
            signature=(w,h,self.scale,self.mission.behavior_map.cell,tuple((k,tuple(sorted(v))) for k,v in sorted(layers.items())))
            if getattr(self,'region_cache',None)!=signature:
                overlay,self.region_labels=render_regions(max(1,w),max(1,h),self.scale,self.mission.behavior_map.cell,layers)
                self.region_photo=ImageTk.PhotoImage(overlay);self.region_cache=signature
            c.create_image(0,0,image=self.region_photo,anchor='nw')
        if not self.mission.auto_fence and self.mission.polygon:c.create_polygon(*[v for p in self.mission.polygon for v in self.project(p)],fill='',outline='#74c9ec',width=2,dash=(6,4))
        x,y=self.project((0,0));c.create_oval(x-7,y-7,x+7,y+7,fill='#ffffff');c.create_text(x+10,y-10,text='SNIFFER (0,0)',anchor='w',fill='white')
        colors=('#4dd6bb','#ffbf5f','#a991ff','#ff8193','#78bdff')
        selected=next((m for m in self.mission.markers if self.route.get()==f"V{m['id']} · {m['node']:04X}"),None)
        self.route_info.set(f"{selected['label']} · {selected['route_status']} · observed length {selected.get('observed_length_m',0):.2f} m · human passability not assessed" if selected else '')
        # Display live estimates even when mapping is held. These dotted lines
        # are strictly separate from accepted mission evidence.
        sources=[]
        if not self.mission.archive_review:sources.append((self.live_trails.rows,True))
        sources.append((self.mission.tracks,False))
        for tracks,preview in sources:
            for n,rows in tracks.items():
                groups={}
                for row in rows:groups.setdefault(row['segment'],[]).append(row['xy'])
                for points in groups.values():
                    if len(points)==1:
                        x,y=self.project(points[0]);c.create_oval(x-1,y-1,x+1,y+1,fill=colors[n%len(colors)],outline='')
                        continue
                    stride=max(1,len(points)//1500);show=points[::stride]
                    if show[-1]!=points[-1]:show.append(points[-1])
                    coords=[v for p in show for v in self.project(p)]
                    width=1 if preview else 3 if selected and selected['node']==n else 2
                    if not preview:c.create_line(*coords,fill='#102331',width=width+2)
                    c.create_line(*coords,fill=colors[n%len(colors)],width=width,dash=(3,5) if preview else (),arrow='last')
        for i,n in enumerate(sorted(set(self.live_trails.rows)|set(self.mission.tracks))):
            c.create_text(12+i*105,54,text=f'● Node {n:04X}',anchor='nw',fill=colors[n%len(colors)])
        for key,p in ({} if self.mission.archive_review else self.snapshot.get('positions',{})).items():
            n=int(key)
            if n==self.snapshot.get('metrics',{}).get('origin_node') or n in self.position_gate.rejected:continue
            x,y=self.project(p);status=self.mission.status.get(n,{});color=colors[n%len(colors)] if status.get('fence') not in ('UNKNOWN',None) else '#85919d'
            c.create_oval(x-5,y-5,x+5,y+5,fill=color,outline=color);c.create_text(x+9,y,text=f'{n:04X}'+(' · QUIET LOCK' if n in self.snapshot.get('stationary_held',[]) else ''),anchor='w',fill=color)
        if not self.mission.archive_review:
            for n,reason in self.position_gate.rejected.items():
                old=self.position_gate.last.get(n)
                if old:
                    x,y=self.project(old['xy']);c.create_oval(x-7,y-7,x+7,y+7,outline='#ffa650',width=2)
                    c.create_text(x+10,y,text=f'{n:04X} · last accepted',anchor='w',fill='#ffa650')
        for n,status in self.mission.status.items():
            if status.get('guidance')=='MAPPING HELD / CHECK TRACKING':
                p=self.snapshot.get('positions',{}).get(n,self.snapshot.get('positions',{}).get(str(n)))
                if p is not None:
                    x,y=self.project(p);c.create_oval(x-12,y-12,x+12,y+12,outline='#ff9e42',width=2)
                    c.create_text(x+14,y+15,text=status.get('behavior','SUSPECT'),anchor='w',fill='#ffb96c')
        for n,p in self.snapshot.get('positions',{}).items():
            if int(n) in self.app.uwb3d.scene.selected_nodes:
                x,y=self.project(p);c.create_oval(x-11,y-11,x+11,y+11,outline='white',width=3)
        for mark in self.mission.markers:
            x,y=self.project(mark['position']);c.create_rectangle(x-7,y-7,x+7,y+7,outline='#ff8193',width=2)
            c.create_text(x+10,y+12,text=f"V{mark['id']} {'confirmed' if mark['confirmed'] else 'suspected'}",anchor='w',fill='#ff8193')
        # Fixed corner legend, never labels scattered over the mapped area.
        lx=max(12,w-250);ly=12
        c.create_rectangle(lx,ly,lx+236,ly+134,fill='#172d3c',outline='#526777')
        c.create_text(lx+12,ly+14,text='MAP LEGEND',anchor='w',fill='white',font=('Segoe UI',10,'bold'))
        for i,(fill,edge,label) in enumerate((('white','#101318','Observed area'),('#172d3c','#ffbf45','Preliminary evidence'),('#172d3c','#ff334f','Suspected obstacle'),('#102331','#526777','Unknown / not observed'))):
            yy=ly+34+i*23
            c.create_oval(lx+12,yy-6,lx+26,yy+6,fill=fill,outline=edge,width=2)
            c.create_text(lx+36,yy,text=label,anchor='w',fill='#e0e9ef',font=('Segoe UI',9))
        counts={}
        for cell in self.mission.behavior_map.cells():counts[cell['state']]=counts.get(cell['state'],0)+1
        c.create_text(12,32,text=' · '.join(f'{k}: {v}' for k,v in counts.items()) or 'Learning: waiting for accepted behavior observations',anchor='nw',fill='#bbceda')
        c.create_text(12,12,text='XY metres · observed traces only · wheel to zoom',anchor='nw',fill='white')
    def tick(self):
        if self.app.closing:return
        self.auto_enabled.set(self.mission.auto_fence)
        f=self.mission.automatic_fences
        self.fence_info.set(f'Automatic: {len(f.explored)} observed cells · {len(f.exclusions)} tentative exclusion cells · Scan-style observed footprints; not a physical LiDAR scan' if self.mission.auto_fence else 'Manual operational boundary')
        now=time.monotonic();runtime=self.app.uwb3d.runtime;self.snapshot=self.app.uwb3d.filtered_snapshot()
        if not self.mission.archive_review:self.live_trails.update(self.snapshot,runtime.generation,now)
        if not self.mission.archive_review:
            self.mission.post_position_filter=dict(method='post-UWB rejection only',max_speed_mps=self.position_gate.max_speed)
            if self.mission.running:
                for node in self.snapshot.get('nodes',[]):
                    n=int(node['id'])
                    self.mission.event('position_filter',n,now,self.position_gate.rejected.get(n,'accepted / no rejection'))
        was_running=self.mission.running
        try:self.mission.update(self.snapshot,runtime.generation,now)
        except Exception as exc:self.mission.pause(now,f'Mission processing error: {exc}')
        if was_running and not self.mission.running:self.autosave()
        if self.mission.running and now-self.saved>=5:self.autosave()
        ids=sorted(int(k) for k in self.snapshot.get('positions',{}) if int(k)!=self.snapshot.get('metrics',{}).get('origin_node'))
        self.pick['values']=[f'{n:04X}' for n in ids]
        self.routes['values']=['All traces']+[f"V{m['id']} · {m['node']:04X}" for m in self.mission.markers]
        if ids and self.node.get() not in self.pick['values']:self.node.set(f'{ids[0]:04X}')
        for n,status in self.mission.status.items():
            if n in self.position_gate.rejected:status=dict(status,behavior=self.position_gate.rejected[n],guidance='OUTLIER FILTERED; LAST ACCEPTED SHOWN')
            values=(f'{n:04X}',status['fence'],status['bio'],status.get('behavior','UNKNOWN'),status.get('guidance','WAIT FOR POSITION'),f"{status.get('speed') or 0:.2f} m/s",f"{len(self.mission.tracks.get(n,[]))} points")
            if self.table.exists(str(n)):self.table.item(str(n),values=values)
            else:self.table.insert('','end',iid=str(n),values=values)
        if not self.drawing:self.message.set(('HELD · ' if self.mission.running and self.mission.origin_motion_hold else 'RECORDING · ' if self.mission.running else 'PAUSED · ')+self.mission.note+(' · '+self.error if self.error else ''))
        # Hidden tabs still log, but avoid redrawing their canvas.
        if self.master.select()==str(self):self.render()
        self.after(100,self.tick)
