"""Four-anchor measured distance matrix for the Control Center."""
import time
import json
from pathlib import Path
from collections import deque
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tracking_estimation import TrackingEstimate, validate_profile
from topdown_map import TopDownMap, TAG_COLORS


class MultiAnchorView(ttk.Frame):
    def __init__(self, parent, prepare, start, stop, automatic=False):
        super().__init__(parent, padding=12)
        self.automatic = automatic
        self.plan = None
        self.rows = {}
        self.model = None
        self.profile = None
        self.roundtrip = deque(maxlen=40)
        self.is_running = False
        self.last_draw = 0
        self.tag_labels = {}
        self.available_boards = []
        self.state = tk.StringVar(value='Select boards, assign anchors, then start tracking.')
        ttk.Label(self, text='Auto-anchor / Relative top-down' if automatic else 'Tracking / Top-down', font=('Segoe UI', 18, 'bold')).pack(anchor='w')
        ttk.Label(self, text='Squares = anchors (solid LED3)    Circles = tags (blinking LED3)    |    Position accuracy not yet verified').pack(anchor='w', pady=(3,8))
        bar = ttk.Frame(self); bar.pack(fill='x')
        ttk.Button(bar, text='Load selected boards', command=prepare).pack(side='left')
        ttk.Button(bar, text='Start tracking', command=start).pack(side='left', padx=6)
        ttk.Button(bar, text='Stop', command=stop).pack(side='left')
        if not automatic:
            ttk.Button(bar, text='Set anchor positions', command=self.edit_geometry).pack(side='left', padx=6)
            ttk.Button(bar, text='Load survey', command=self.load_profile).pack(side='left')
        else:
            self.auto_mode = tk.StringVar(value='3D / anchors at different heights')
            self.mode_box = ttk.Combobox(bar,textvariable=self.auto_mode,state='readonly',width=35,
                values=('3D / anchors at different heights','2D / ALL boards in the same plane'))
            self.mode_box.pack(side='left',padx=8)
            self.mode_box.bind('<<ComboboxSelected>>',lambda _:self.selection_changed())
        ttk.Button(bar, text='Measurement details', command=self.toggle_details).pack(side='right')
        choices = ttk.Frame(self); choices.pack(fill='x', pady=8)
        self.anchors, self.boxes = [], []
        for i in range(4):
            ttk.Label(choices, text=f'A{i+1:03X}').grid(row=0, column=i, sticky='w', padx=4)
            variable = tk.StringVar(); self.anchors.append(variable)
            box = ttk.Combobox(choices, textvariable=variable, state='readonly', width=22)
            box.grid(row=1, column=i, padx=4); self.boxes.append(box)
            box.bind('<<ComboboxSelected>>',lambda _:self.selection_changed())
        self.mapping = tk.StringVar()
        self.map_area = ttk.Frame(self); self.map_area.pack(fill='both', expand=True, pady=(2,5))
        map_column = ttk.Frame(self.map_area); map_column.pack(side='left',fill='both',expand=True)
        self.canvas = TopDownMap(map_column); self.canvas.pack(fill='both',expand=True)
        controls = ttk.Frame(map_column); controls.pack(fill='x')
        self.frame_label = ttk.Label(controls,text='A1 = origin · A2 = +X · A3 = +Y plane · A4 = +Z' if automatic else 'XY floor plan / height shown separately')
        self.frame_label.pack(side='left')
        ttk.Button(controls,text='Fit anchors',command=self.canvas.reset).pack(side='right')
        ttk.Button(controls,text='+',width=3,command=lambda:self.canvas.zoom(.8)).pack(side='right')
        ttk.Button(controls,text='-',width=3,command=lambda:self.canvas.zoom(1.25)).pack(side='right')
        self.tag_panel = tk.Frame(self.map_area,bg='#162938',width=220)
        self.tag_panel.pack(side='right',fill='y',padx=(8,0)); self.tag_panel.pack_propagate(False)
        tk.Label(self.tag_panel,text='TAGS',bg='#162938',fg='#c6d6e4',font=('Segoe UI',11,'bold')).pack(anchor='w',padx=14,pady=12)
        self.details = ttk.Frame(self)
        columns = ('anchor', 'tag', 'distance', 'filtered', 'mode', 'age', 'rate', 'success', 'failed')
        self.table = ttk.Treeview(self.details, columns=columns, show='headings', height=4)
        for col, title in zip(columns, ('Anchor', 'Tag', 'Raw (m)', 'Display (m)', 'Source', 'Age (s)', 'Hz', 'Success', 'Failed')):
            self.table.heading(col, text=title); self.table.column(col, width=95)
        self.table.pack(fill='x')
        self.imu_text = tk.StringVar(value='IMU: waiting for fresh sensor reports')
        ttk.Label(self.details, textvariable=self.imu_text, wraplength=1030).pack(anchor='w', pady=3)
        self.timing = tk.StringVar()
        ttk.Label(self.details, textvariable=self.timing).pack(anchor='w')
        self.status_label = ttk.Label(self,textvariable=self.state,wraplength=1030)
        self.status_label.pack(anchor='w',pady=4)
        if automatic:
            self.edge_text = tk.StringVar(value='Six anchor distances: waiting')
            ttk.Label(self,textvariable=self.edge_text,wraplength=1030).pack(anchor='w')
            ttk.Label(self,text='Relative frame, not a building map. In 2D, ALL anchors and tags must share one plane. Rubble can bias even consistent ranges.',wraplength=1030).pack(anchor='w')

    def toggle_details(self):
        if self.details.winfo_manager(): self.details.pack_forget()
        else: self.details.pack(fill='x',before=self.status_label)

    def edit_geometry(self):
        if not self.is_running and not self.selection_changed(): return
        if not self.plan:
            messagebox.showinfo('Anchor positions', 'Load the selected boards first.'); return
        window = tk.Toplevel(self); window.title('Measured anchor positions'); window.transient(self.winfo_toplevel())
        frame = ttk.Frame(window,padding=18); frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Enter measured coordinates in metres. Use the UWB antenna reference point.').grid(row=0,column=0,columnspan=4,sticky='w',pady=(0,10))
        for i,title in enumerate(('Anchor / board','X','Y','Height Z')): ttk.Label(frame,text=title).grid(row=1,column=i,sticky='w')
        variables = {}
        for row,a in enumerate(self.plan['anchors'],2):
            ttk.Label(frame,text=a['id']+' / '+a['name']).grid(row=row,column=0,sticky='w',padx=(0,12),pady=6)
            xyz = (self.profile or {}).get('anchors',{}).get(a['name'],['','',''])
            variables[a['name']] = [tk.StringVar(value=v) for v in xyz]
            for col,v in enumerate(variables[a['name']],1): ttk.Entry(frame,textvariable=v,width=10).grid(row=row,column=col,padx=4)
        mode = tk.StringVar(value=(self.profile or {}).get('mode','xy'))
        ttk.Label(frame,text='Calculation mode').grid(row=6,column=0,sticky='w',pady=10)
        ttk.Combobox(frame,textvariable=mode,values=('xy','xyz'),state='readonly',width=8).grid(row=6,column=1)
        height = tk.StringVar(value=(self.profile or {}).get('tag_height_m') if (self.profile or {}).get('tag_height_m') is not None else '')
        ttk.Label(frame,text='XY mode: fixed tag height (m)').grid(row=7,column=0,sticky='w')
        ttk.Entry(frame,textvariable=height,width=10).grid(row=7,column=1)
        ttk.Label(frame,text='XY: tags stay at the entered height. XYZ: anchors must not all share one plane.\nMoving anchors require a new survey. Blank coordinates are never guessed.',wraplength=570).grid(row=8,column=0,columnspan=4,sticky='w',pady=12)
        def apply(save=False):
            try:
                profile = dict(mode=mode.get(),anchors={name:[float(v.get()) for v in xyz] for name,xyz in variables.items()},
                               range_bias_m=(self.profile or {}).get('range_bias_m',{}))
                if mode.get()=='xy': profile['tag_height_m']=float(height.get())
                self.profile = self.model.profile = validate_profile(profile,self.plan)
                self.canvas.reset()
                if save: self.save_profile()
                window.destroy()
            except Exception as exc: messagebox.showerror('Anchor positions',str(exc),parent=window)
        ttk.Button(frame,text='Apply measured positions',command=apply).grid(row=9,column=0,columnspan=2,sticky='w')
        ttk.Button(frame,text='Apply and save',command=lambda:apply(True)).grid(row=9,column=3)

    def save_profile(self):
        if not self.profile:
            messagebox.showinfo('Survey','Apply measured positions before saving.');return
        name=filedialog.asksaveasfilename(defaultextension='.json',initialfile='surveyed-anchors.json')
        if name: Path(name).write_text(json.dumps(self.profile,indent=2),encoding='utf-8')

    def load(self, boards):
        self.available_boards = boards
        names = sorted(b['name'] for b in boards)
        for i, (variable, box) in enumerate(zip(self.anchors, self.boxes)):
            box['values'] = names
            variable.set(names[i])
        self.state.set('Choose four anchors above. All remaining selected boards become tags.')
        self.selection_changed()

    def selection_changed(self):
        if self.is_running: return True
        from multi_anchor import make_plan
        try: plan = make_plan(self.available_boards,[v.get() for v in self.anchors])
        except ValueError as exc:
            self.state.set(str(exc)); return False
        self.set_plan(plan)
        return True

    def set_plan(self, plan):
        self.plan = plan; self.rows = {}
        self.canvas.reset()
        for widget in list(self.tag_labels.values()): widget.destroy()
        self.tag_labels = {}
        for index,tag in enumerate(plan['tags']):
            label = tk.Label(self.tag_panel,bg='#162938',fg=TAG_COLORS[index%len(TAG_COLORS)],justify='left',anchor='w',font=('Segoe UI',10),wraplength=192)
            label.pack(fill='x',padx=14,pady=6)
            self.tag_labels[tag['id']] = label
        self.model = TrackingEstimate(plan)
        if self.automatic:
            from auto_anchor import AutoAnchorEstimate
            self.model = AutoAnchorEstimate(plan,mode='xy' if self.auto_mode.get().startswith('2D') else 'xyz')
            self.frame_label.configure(text='A1 = origin · A2 = +X · A3 = +Y · same-plane Z = 0' if self.model.mode=='xy' else
                                       'A1 = origin · A2 = +X · A3 = +Y plane · A4 = +Z')
        if self.profile:
            try: self.model.profile = validate_profile(self.profile, plan)
            except ValueError: self.profile = None
        for variable, anchor in zip(self.anchors, plan['anchors']): variable.set(anchor['name'])
        for row in self.table.get_children(): self.table.delete(row)
        self.mapping.set('Tags: ' + ', '.join(f"{t['id']} = {t['name']}" for t in plan['tags']))
        for a in plan['anchors']:
            for t in plan['tags']:
                key = (a['id'], t['id'])
                self.rows[key] = dict(distance=None, filtered=None, mode='WAITING', last=None,
                                     times=deque(maxlen=100), success=0, failed=0, tag=t['name'])
                self.table.insert('', 'end', iid='/'.join(key))
        self.refresh()

    def sample(self, value):
        anchor, tag, distance, rc = value
        row = self.rows.get((anchor, tag))
        if row is None: return
        if rc: row['failed'] += 1
        else:
            row.update(distance=distance, last=time.monotonic(), success=row['success']+1)
            row['times'].append(row['last'])
            row['filtered'], row['mode'] = self.model.update(anchor, tag, distance)

    def feed(self, board, line, received):
        if self.model: self.model.feed(board, line, received)

    def load_profile(self):
        if not self.model:
            messagebox.showinfo('Geometry', 'Load the selected boards first, then load their measured geometry.'); return
        name = filedialog.askopenfilename(filetypes=[('Surveyed geometry', '*.json')])
        if not name: return
        try:
            profile = validate_profile(json.loads(Path(name).read_text(encoding='utf-8')), self.plan)
            self.model.profile = self.profile = profile
        except Exception as exc: messagebox.showerror('Geometry', str(exc))

    def template(self):
        names = [v.get() for v in self.anchors]
        if len(set(names)) != 4 or not all(names):
            messagebox.showinfo('Geometry', 'Select four different anchor boards first.'); return
        name = filedialog.asksaveasfilename(defaultextension='.json', initialfile='surveyed-anchors.json')
        if name:
            Path(name).write_text(json.dumps(dict(mode='xyz', anchors={n:[None,None,None] for n in names},
                tag_height_m=None, range_bias_m={}), indent=2), encoding='utf-8')

    def refresh(self):
        if self.automatic and self.model:
            self.model.expire()
            if self.is_running: self.state.set(self.model.reason)
        for key, row in self.rows.items():
            self.table.item('/'.join(key), values=(key[0], key[1],
                '—' if row['distance'] is None else f"{row['distance']:.4f}",
                '—' if row['filtered'] is None else f"{row['filtered']:.4f}", row['mode'],
                '—' if row['last'] is None else f"{time.monotonic()-row['last']:.2f}",
                f"{sum(time.monotonic()-t<5 for t in row['times'])/5:.1f}", row['success'], row['failed']))
        if self.model:
            self.imu_text.set('IMU: ' + '  |  '.join(b['id']+': '+self.model.motion[b['name']].state(time.monotonic())
                for b in self.plan['anchors']+self.plan['tags']))
        if self.roundtrip: self.timing.set(f'Mean command round trip: {sum(self.roundtrip)/len(self.roundtrip):.0f} ms. Range age includes time between turns.')
        self.draw_position()

    def draw_position(self):
        if time.monotonic()-self.last_draw<.08: return
        self.last_draw=time.monotonic()
        shown = self.model.display() if self.automatic and self.model else self.model
        found = self.canvas.render(shown,self.is_running) or {}
        if not self.plan: return
        now=time.monotonic()
        for tag in self.plan['tags']:
            rows=[v for (a,t),v in self.rows.items() if t==tag['id']]
            live=sum(r['last'] is not None and now-r['last']<(5 if self.automatic else 2) for r in rows)
            xyz,error=found.get(tag['id'],(None,'Set anchor positions'))
            position = f'X {xyz[0]:.2f}   Y {xyz[1]:.2f}\n{"Relative Z" if self.automatic else "Height"} {xyz[2]:.2f} m' if xyz else error
            if self.automatic and xyz and tag['id'] in shown.results:
                position += f"\nRange fit RMS {100*shown.results[tag['id']][1]:.1f} cm\n(not position accuracy)"
                stamps=[shown.ranges[a['id'],tag['id']][1] for a in self.plan['anchors']]
                position += f'\nPosition age {max(0.,now-min(stamps)):.1f}s'
                if getattr(shown,'held',False): position += '\nHELD'
            motion=self.model.motion[tag['name']].state(now)
            self.tag_labels[tag['id']].configure(text=f"● {tag['id']}\n{tag['name']}\n{live}/4 links fresh · {motion}\n{position}")

    def running(self, value):
        self.is_running = value
        if self.automatic: self.mode_box.configure(state='disabled' if value else 'readonly')
        for box in self.boxes: box.configure(state='disabled' if value else 'readonly')
        self.state.set('Running: all BLE links stay open; anchors range in turn. Failed attempts remain visible.' if value else 'Stopped')
        if not value and self.automatic and self.model:
            self.model.invalidate('Stopped')
            self.model.last_epoch = None

    def epoch(self, value):
        self.model.accept(value)
        pairs = ('A1–A2','A1–A3','A1–A4','A2–A3','A2–A4','A3–A4')
        def metres(v): return 'miss' if v is None else f'{v:.3f}'
        self.edge_text.set('Anchor ranges before / after (m): ' + '  |  '.join(
            f'{pair}: {metres(b[0])} / {metres(a[0])}'
            for pair,b,a in zip(pairs,value['before'],value['after'])) + f"   Cycle: {value['end']-value['start']:.2f}s")
        self.record_tags(value['tags'])

    def record_tags(self, values):
        for anchor,tag,distance,stamp in values:
            row = self.rows.get((anchor,tag))
            if row is None: continue
            if row.get('last_attempt') == stamp: continue
            row['last_attempt'] = stamp
            if distance is None: row['failed'] += 1
            else:
                row.update(distance=distance,filtered=distance,mode='RAW / AUTO',last=stamp,success=row['success']+1)
                row['times'].append(stamp)

    def tag_update(self, rows):
        if not self.model: return False
        self.record_tags(rows)
        return self.model.update_tag(rows)
