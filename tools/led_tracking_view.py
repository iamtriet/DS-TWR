"""Dedicated manual LED page sharing the existing multi-tag tracking solution."""
import tkinter as tk
import time
import asyncio
from tkinter import ttk
from manual_led_view import ManualLEDControls
from uwb3d_view import Scene

class LEDTrackingView(ttk.Frame):
    def __init__(self,parent,app):
        super().__init__(parent,padding=10);self.app=app;self.fitted=False;self.detected_once=False;self.autodetect_ready=False
        self.after(1500,lambda:setattr(self,'autodetect_ready',True))
        ttk.Label(self,text='UV / RGB Control',font=('Segoe UI',18,'bold')).pack(anchor='w')
        ttk.Label(self,text='Live multi-tag tracking · click node markers to select / deselect · white rings = selected nodes').pack(anchor='w',pady=4)
        self.controls=ManualLEDControls(self,app);self.controls.pack(fill='x',pady=6)
        bar=ttk.Frame(self);bar.pack(fill='x')
        ttk.Label(bar,text='Multi-tag tracking · UART').pack(side='left',padx=(0,4))
        from serial.tools.list_ports import comports
        self.port_box=ttk.Combobox(bar,textvariable=app.uwb3d.port,values=[p.device for p in comports()],width=8)
        self.port_box.pack(side='left')
        ttk.Button(bar,text='Detect USB boards',command=self.detect_usb).pack(side='left',padx=3)
        ttk.Button(bar,text='Start UWB tracking',command=self.start_tracking).pack(side='left',padx=4)
        ttk.Button(bar,text='Stop tracking',command=lambda:app.uwb3d.submit(app.uwb3d.runtime.stop())).pack(side='left',padx=3)
        ttk.Button(bar,text='Select all visible',command=self.select_all).pack(side='left')
        ttk.Button(bar,text='Fit map',command=lambda:self.scene.fit()).pack(side='left',padx=4)
        ttk.Button(bar,text='Tracking setup',command=lambda:parent.select(app.uwb3d)).pack(side='left')
        self.status=tk.StringVar(value='Start tracking to see nodes here')
        ttk.Label(self,textvariable=self.status,wraplength=1150).pack(anchor='w')
        self.usb_status=tk.StringVar(value='Detect USB boards to identify the board on each COM port.')
        ttk.Label(self,textvariable=self.usb_status,wraplength=1150).pack(anchor='w',pady=3)
        self.scene=Scene(self);self.scene.projection='XY'
        self.scene.selected_nodes=app.uwb3d.scene.selected_nodes
        self.scene.pack(fill='both',expand=True,pady=8)
        self.idle_hint=self.scene.create_text(24,60,anchor='nw',fill='#d0dfe8',font=('Segoe UI',12),text='Multi-tag tracking — waiting for UWB data.\nSelect the tracking boards and sniffer, choose its UART port, then Start UWB tracking.\nWhen tracking is running, click markers to select nodes for UV / RGB control.')
        ttk.Label(self,text='LED commands affect only selected nodes. Quiet lock and jump filtering are shared with Tracking; the UWB solver is unchanged.').pack(anchor='w')
        self.after(200,self.tick)
        self.discovery_future=None;self.discovery_generation=None;self.next_discovery=0.
        self.after(1000,self.watch_boards)
    def watch_boards(self):
        if self.app.closing:return
        view=self.app.uwb3d;runtime=view.runtime;now=time.monotonic()
        if self.discovery_future and self.discovery_future.done():
            future=self.discovery_future;self.discovery_future=None
            try:
                found=future.result()
                if (runtime.source=='swarm' and runtime.gateway and
                        runtime.generation==self.discovery_generation and not self.app.busy):
                    from swarm_membership import membership_plan
                    plan=membership_plan(runtime.gateway.boards,runtime.gateway.origin,
                                         runtime.snapshot() or {},found,now)
                    if plan:
                        self.usb_status.set('New board detected: '+', '.join(b['name'] for b in plan['additions'])+
                                            ' — updating TDMA group; tracking will briefly restart.')
                        self.next_discovery=now+30
                        def done(_):
                            self.app.set_devices(plan['boards'])
                            self.app.boards.selection_set(self.app.boards.get_children())
                            self.scene.selected_nodes.clear();self.scene.trails.clear();self.fitted=False
                            self.usb_status.set(f"{view.port.get()}: {len(plan['boards'])} boards enrolled · automatic BLE discovery every 10 s")
                        port=view.port.get()
                        self.app.run('Adding new UWB boards…',lambda:runtime.reconfigure_swarm(plan,port),done,preserve_tracking=True)
            except Exception as exc:self.usb_status.set('Background discovery: '+str(exc))
        if (not self.discovery_future and now>=self.next_discovery and not self.app.busy
                and runtime.source=='swarm' and getattr(runtime,'swarm_configured',False)):
            from control_backend import discover
            self.discovery_generation=runtime.generation;self.next_discovery=now+10
            self.discovery_future=asyncio.run_coroutine_threadsafe(discover(timeout=3),self.app.loop)
        self.after(1000,self.watch_boards)
    def detect_usb(self):
        self.detected_once=True
        if self.app.uwb3d.runtime.tasks:
            self.usb_status.set('Stop tracking before probing its UART port.');return
        from uart_discovery import discover_uart_boards
        excluded=[s.port for s in self.app.sessions.values() if getattr(s,'port',None)]
        def done(result):
            boards=result['boards'];self.port_box['values']=[b['port'] for b in boards]
            self.usb_status.set(' | '.join(f"{b['port']} → {b['name']} · firmware {b['version']}" for b in boards) or 'No PCB responded. '+('; '.join(result['errors'])))
            if len(boards)==1:self.app.uwb3d.port.set(boards[0]['port'])
            elif boards:self.usb_status.set(self.usb_status.get()+' · Choose the COM port to use as sniffer.')
        self.app.run('Identifying USB/UART boards…',lambda:discover_uart_boards(excluded),done,preserve_tracking=True)
    def start_tracking(self):
        indices=self.controls.board_list.curselection()
        if indices:
            self.app.boards.selection_set([self.controls.board_keys[i] for i in indices])
        self.app.uwb3d.source.set('swarm')
        self.app.uwb3d.sniffer_origin.set(True)
        self.app.uwb3d.geometry.set('Tabletop 2D')
        self.app.uwb3d.age.set(.3)
        self.app.uwb3d.start()
    def select_all(self):
        self.scene.selected_nodes.update(int(n) for n in (self.scene.state or {}).get('positions',{}))
        self.controls.refresh_label();self.scene.render(self.scene.state)
    def tick(self):
        started=time.monotonic()
        if self.app.closing:return
        if self.autodetect_ready and not self.detected_once and self.winfo_viewable() and not self.app.busy and not self.app.uwb3d.runtime.tasks:
            self.detect_usb()
        state=self.app.uwb3d.filtered_snapshot()
        if state:
            self.scene.itemconfigure(self.idle_hint,state='hidden')
            if self.master.select()==str(self):self.scene.render(state)
            if not self.fitted and state.get('positions') and self.winfo_viewable():
                self.scene.render(state);self.scene.fit();self.fitted=True
            metrics=state.get('metrics',{});age=state.get('age')
            self.status.set(f"{len(state.get('positions',{}))} nodes · {len(state.get('edges',[]))} UWB links · {metrics.get('localization_hz',0)}/s localization · "+
                            (f"range age {age*1000:.0f} ms · " if age is not None else '')+
                            f"{len(state.get('stationary_held',[]))} quiet locks · {len(state.get('position_rejections',{}))} held/outliers")
        else:self.status.set(self.app.uwb3d.message or 'Start tracking to see nodes here')
        self.after(max(1,40-round((time.monotonic()-started)*1000)),self.tick)
