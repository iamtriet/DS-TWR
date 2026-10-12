"""Windows desktop board control: python tools/control_app.py."""

import asyncio

import csv

from datetime import datetime, timezone

import json

from pathlib import Path

import queue

import sys

import threading

import time

import tkinter as tk

from tkinter import ttk, filedialog, messagebox



from control_backend import Session, discover

from uart_monitor import parse_uwb

from sign_image import ROOT, image_info





class ControlApp:

    def __init__(self, root):

        self.root = root

        root.title("Purple PCB • Board Control")

        root.geometry("1120x800")

        root.minsize(920, 680)

        self.events = queue.Queue()

        self.loop = asyncio.new_event_loop()

        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)

        self.thread.start()

        self.sessions = {}

        self.devices = {}

        self.busy = False

        self.ota_running = False

        self.closing = False

        self.ranges = {}

        self.fleet = None

        self.fleet_boards = []

        self.fleet_plan = None

        self.multi_boards = []

        self.status = tk.StringVar(value="Ready • Scan for boards or connect a USB debug port")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")

        self.output = ROOT / "build/control-sessions" / stamp

        self.output.mkdir(parents=True)

        self.log_file = (self.output / "console.log").open("w", encoding="utf-8")

        self.csv_file = (self.output / "ranges.csv").open("w", newline="", encoding="utf-8")

        self.csv_writer = csv.writer(self.csv_file)

        self.csv_writer.writerow(["host_utc", "board", "tag_hex", "distance_m", "age_ms"])

        style = ttk.Style()

        if "vista" in style.theme_names():

            style.theme_use("vista")

        style.configure("Title.TLabel", font=("Segoe UI", 19, "bold"))

        style.configure("TButton", padding=(10, 6))

        header = ttk.Frame(root, padding=16); header.pack(fill="x")

        ttk.Label(header, text="Purple PCB  /  Control Center", style="Title.TLabel").pack(anchor="w")

        ttk.Label(header, text="Discover • Update • Configure • Measure", foreground="#586675").pack(anchor="w", pady=(4, 0))

        tabs = ttk.Notebook(root); tabs.pack(fill="both", expand=True, padx=16)

        fleet = ttk.Frame(tabs, padding=12); tabs.add(fleet, text="Boards & OTA")

        provision = ttk.Frame(tabs, padding=12); tabs.add(provision, text="New Board / ST-Link")

        tracking = ttk.Frame(tabs, padding=12); tabs.add(tracking, text="Tracking & Sensors")

        from tracking_view import TrackingView

        self.tracking_view = TrackingView(tabs, self.prepare_fleet, self.start_fleet, self.stop_fleet)

        tabs.add(self.tracking_view, text="Single-anchor ranges")

        from multi_anchor_view import MultiAnchorView

        self.multi_view = MultiAnchorView(tabs, self.prepare_multi, self.start_multi, self.stop_fleet)

        tabs.add(self.multi_view, text="Four-anchor Tracking")
        from uwb3d_view import UWB3DView
        self.uwb3d = UWB3DView(tabs,self)
        self.auto_view = self.uwb3d.legacy
        tabs.add(self.uwb3d, text="UWB 3D Tracking")
        self.active_multi_view = self.multi_view

        console = ttk.Frame(tabs, padding=12); tabs.add(console, text="Console & Logs")

        self.make_fleet(fleet)

        self.make_provision(provision)

        self.make_tracking(tracking)

        self.make_console(console)

        ttk.Label(root, textvariable=self.status, padding=12, wraplength=1050).pack(fill="x")

        root.protocol("WM_DELETE_WINDOW", self.close)

        self.refresh_ports()

        root.after(25, self.pump)



    def emit(self, kind, value):

        self.events.put((kind, value))



    def prepare_fleet(self, reuse=False):

        if self.busy: return

        if self.fleet is not None:

            messagebox.showerror("Tracking", "Stop the current fleet before changing its setup"); return

        try:

            from tracking import fleet_plan

            boards = self.fleet_boards if reuse else [dict(b) for b in self.selected()]

            if not 2 <= len(boards) <= 9:

                raise ValueError("Select one anchor and 1–8 tags on Boards & OTA")

            names = [b["name"] for b in boards]

            selected = self.tracking_view.anchor.get()

            if selected not in names:

                selected = "PCB_76A10829" if "PCB_76A10829" in names else names[0]

            anchor = next(b for b in boards if b["name"] == selected)

            self.fleet_plan = fleet_plan(boards, anchor["address"])

            self.fleet_plan["anchor_indicator"] = self.tracking_view.indicator.get()

            self.fleet_boards = boards

            self.tracking_view.anchor_box["values"] = names

            self.tracking_view.anchor.set(selected)

            self.tracking_view.set_plan(self.fleet_plan)

        except Exception as exc:

            messagebox.showerror("Fleet setup", str(exc))



    def start_fleet(self):

        if self.busy: return

        if not self.fleet_plan:

            messagebox.showerror("Tracking", "Use selected boards to prepare the fleet first"); return

        if self.fleet is not None:

            messagebox.showerror("Tracking", "Stop the current fleet before restarting"); return

        plan = self.fleet_plan

        mapping = "\n".join(f"{tag['name']} → {tag['id']}" for tag in plan["tags"])

        if not messagebox.askyesno("Start multi-tag tracking", f"Anchor: {plan['anchor']['name']} / A001\n\nTags:\n{mapping}\n\nApply these radio roles/IDs and start ranging? Existing control connections will close."):

            return

        self.tracking_view.set_plan(plan)

        self.tracking_view.anchor_box.configure(state="disabled")

        self.tracking_view.indicator_box.configure(state="disabled")

        (self.output / "tracking-fleet.json").write_text(json.dumps(plan, indent=2)+"\n")

        async def job():

            from tracking import Fleet

            for session in list(self.sessions.values()):

                await session.close(); self.emit("disconnected", session.label)

            self.fleet = Fleet(plan, self.emit)

            return await self.fleet.start()

        def done(anchor):

            self.sessions[anchor.label] = anchor

            self.target_box["values"] = list(self.sessions)

            self.target.set(anchor.label)

            self.tracking_view.running = True

        self.run("Configuring tags, then starting the anchor…", job, done)



    def prepare_multi(self, automatic=False):
        if self.busy or self.fleet is not None: return
        try:
            boards = [dict(b) for b in self.selected()]
        except ValueError as exc:
            messagebox.showerror("Four-anchor tracking", str(exc)); return
        if not 5 <= len(boards) <= 12:

            messagebox.showerror("Four-anchor tracking", "Select four anchors and 1–8 tags on Boards & OTA")

            return

        self.multi_boards = boards

        (self.auto_view if automatic else self.multi_view).load(boards)



    def start_multi(self, automatic=False):
        view = self.auto_view if automatic else self.multi_view

        if self.busy or self.fleet is not None: return

        try:

            from multi_anchor import make_plan, MultiAnchorFleet

            plan = make_plan(view.available_boards, [v.get() for v in view.anchors])

        except Exception as exc:

            messagebox.showerror("Four-anchor tracking", str(exc)); return

        view.set_plan(plan)
        self.active_multi_view = view
        view.running(True)

        if automatic:
            (self.output / "cooperative-plan.json").write_text(json.dumps(dict(plan,mode=view.model.mode),indent=2))
        else:
            (self.output / "four-anchor-plan.json").write_text(json.dumps(plan, indent=2))

        async def job():

            for session in list(self.sessions.values()):

                await session.close(); self.emit("disconnected", session.label)

            from cooperative_tracking import CooperativeFleet
            self.fleet = CooperativeFleet(plan, self.emit,mode=view.model.mode) if automatic else MultiAnchorFleet(plan,self.emit)

            await self.fleet.start()

        self.run("Configuring four anchors and tags…", job)



    async def stop_tracking(self):

        if self.uwb3d.runtime.gateway is not None or self.uwb3d.runtime.source=='uart':
            await self.uwb3d.runtime.stop()

        if self.fleet is not None:

            failed = await self.fleet.stop()

            if failed:

                raise RuntimeError("Some boards could not be stopped; keep the fleet and retry: " + "; ".join(failed))

            self.fleet = None

        self.emit("fleet_stopped", None)



    def stop_fleet(self):

        self.run("Stopping anchor and tags…", self.stop_tracking)



    def run(self, label, factory, done=None):

        if self.busy or self.closing:

            self.status.set("Wait for the current operation to finish.")

            return

        self.busy = True

        self.status.set(label)

        async def work():

            try:

                if self.uwb3d.runtime.gateway is not None:
                    await self.uwb3d.runtime.stop()

                result = await factory()

                self.emit("done", (label, result, done, None))

            except Exception as exc:

                self.emit("done", (label, None, done, str(exc)))

        asyncio.run_coroutine_threadsafe(work(), self.loop)



    def make_fleet(self, page):

        bar = ttk.Frame(page); bar.pack(fill="x")

        ttk.Button(bar, text="Scan BLE boards", command=self.scan).pack(side="left")

        ttk.Button(bar, text="Select all", command=lambda: self.boards.selection_set(self.boards.get_children())).pack(side="left", padx=6)

        ttk.Button(bar, text="Load roster", command=self.load_roster).pack(side="left")

        ttk.Button(bar, text="Save selected roster", command=self.save_roster).pack(side="left", padx=6)

        self.boards = ttk.Treeview(page, columns=("name", "address", "signal"), show="headings", height=10, selectmode="extended")

        for col, title, width in (("name", "Board", 250), ("address", "BLE address", 300), ("signal", "Signal (dBm)", 120)):

            self.boards.heading(col, text=title); self.boards.column(col, width=width)

        self.boards.pack(fill="both", expand=True, pady=10)

        connection = ttk.LabelFrame(page, text="Connect for tracking and diagnostics", padding=10); connection.pack(fill="x")

        ttk.Button(connection, text="Connect selected BLE board", command=self.connect_ble).pack(side="left")

        self.port = tk.StringVar()

        self.port_box = ttk.Combobox(connection, textvariable=self.port, width=12, state="readonly"); self.port_box.pack(side="left", padx=12)

        ttk.Button(connection, text="Refresh ports", command=self.refresh_ports).pack(side="left")

        ttk.Button(connection, text="Connect USB", command=self.connect_usb).pack(side="left", padx=8)

        ota = ttk.LabelFrame(page, text="Signed firmware update", padding=10); ota.pack(fill="x", pady=12)

        self.image_path = tk.StringVar(value=str(ROOT / "build/images/validation_b.signed.bin"))

        ttk.Entry(ota, textvariable=self.image_path).pack(fill="x")

        buttons = ttk.Frame(ota); buttons.pack(fill="x", pady=(8, 0))

        ttk.Button(buttons, text="Choose signed .bin", command=self.choose_image).pack(side="left")

        ttk.Button(buttons, text="Update selected boards", command=self.ota).pack(side="left", padx=8)

        self.parallel = tk.IntVar(value=2)

        ttk.Label(buttons, text="Concurrent boards:").pack(side="left")

        ttk.Combobox(buttons, textvariable=self.parallel, values=(1, 2), state="readonly", width=3).pack(side="left", padx=6)

        ttk.Label(ota, text="Update progress appears below and in Console. Includes signature, trial, confirmation and reboot checks.", wraplength=1000).pack(anchor="w", pady=6)



    def make_provision(self, page):

        ttk.Label(page, text="Initial programming for a new board", style="Title.TLabel").pack(anchor="w")

        ttk.Label(page, text="Connect ST-Link SWDIO, SWCLK, ground and target power. Select its USB port below. ST-Link programming uses a USB probe, not a COM port.", wraplength=970).pack(anchor="w", pady=12)

        form = ttk.LabelFrame(page, text="ST-Link selection", padding=12); form.pack(fill="x")

        self.probe = tk.StringVar()

        self.probes = {}

        self.probe_box = ttk.Combobox(form, textvariable=self.probe, state="readonly", width=65)

        self.probe_box.pack(side="left", fill="x", expand=True)

        ttk.Button(form, text="Refresh ST-Link ports", command=self.refresh_probes).pack(side="left", padx=8)

        config = ttk.LabelFrame(page, text="Programmer tools", padding=12); config.pack(fill="x", pady=12)

        from stlink import default_tools

        try:

            exe, scripts = default_tools()

        except FileNotFoundError:

            exe = scripts = ""

        self.openocd_path = tk.StringVar(value=str(exe))

        self.scripts_path = tk.StringVar(value=str(scripts))

        for row, (label, variable, directory) in enumerate((("OpenOCD", self.openocd_path, False), ("OpenOCD scripts", self.scripts_path, True))):

            ttk.Label(config, text=label).grid(row=row, column=0, sticky="w", pady=4)

            ttk.Entry(config, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=8)

            def browse(v=variable, d=directory):

                result = filedialog.askdirectory() if d else filedialog.askopenfilename(filetypes=[("OpenOCD", "*.exe")])

                if result: v.set(result)

            ttk.Button(config, text="Browse", command=browse).grid(row=row, column=2)

        config.columnconfigure(1, weight=1)

        image = ttk.LabelFrame(page, text="Signed application (shared with OTA selection)", padding=12); image.pack(fill="x")

        ttk.Entry(image, textvariable=self.image_path).pack(fill="x")

        ttk.Button(image, text="Choose signed .bin", command=self.choose_image).pack(anchor="w", pady=8)

        ttk.Button(page, text="Program new board", command=self.provision).pack(anchor="w", pady=16)

        ttk.Label(page, text="This installs the trusted MCUboot bootloader plus the selected signed application. It replaces existing boot/application firmware and clears OTA swap slots. A full flash/UICR backup is saved first; storage and UICR are checked afterward. Keep the probe on the same board until completion.", wraplength=970).pack(anchor="w")

        ttk.Label(page, text="After programming: use Scan BLE boards, then select the new board for control or future OTA updates. Programming logs appear in Console & Logs.", wraplength=970).pack(anchor="w", pady=12)



    def refresh_probes(self):

        from stlink import list_probes

        exe = self.openocd_path.get()

        def done(probes):

            self.probes = {p["label"]: p["location"] for p in probes}

            self.probe_box["values"] = list(self.probes)

            self.probe.set("")

            self.status.set(f"Found {len(probes)} ST-Link probe(s); select the intended USB port")

        self.run("Finding ST-Link USB ports…", lambda: asyncio.to_thread(list_probes, exe), done)



    def provision(self):

        if self.busy: return

        try:

            location = self.probes[self.probe.get()]

            exe = Path(self.openocd_path.get()).resolve()

            scripts = Path(self.scripts_path.get()).resolve()

            path = Path(self.image_path.get()).resolve()

            info = image_info(path.read_bytes())

            if not exe.is_file() or not (scripts / "interface/stlink.cfg").is_file():

                raise ValueError("Select valid OpenOCD tools")

        except KeyError:

            messagebox.showerror("ST-Link", "Refresh and select the ST-Link USB port first"); return

        except Exception as exc:

            messagebox.showerror("Initial programming", str(exc)); return

        if not messagebox.askyesno("Program new board", f"Install MCUboot and firmware {info['version']} through ST-Link USB {location}?\n\nExisting application and OTA swap slots will be replaced after backup. Storage/UICR are preserved. Keep this probe connected to the intended board."):

            return

        async def job():

            await self.stop_tracking()

            for session in list(self.sessions.values()):

                await session.close(); self.emit("disconnected", session.label)

            proc = await asyncio.create_subprocess_exec(sys.executable, "-u", str(ROOT / "tools/first_flash.py"),

                "--flash", "--openocd", str(exe), "--scripts", str(scripts),

                "--adapter-location", location, "--image", str(path), cwd=str(ROOT),

                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)

            async for line in proc.stdout:

                self.emit("program", line.decode(errors="replace").rstrip())

            if await proc.wait():

                raise RuntimeError("ST-Link programming failed; inspect Console and the saved backup/report")

        self.run(f"Programming ST-Link USB {location}…", job)



    def make_tracking(self, page):

        top = ttk.Frame(page); top.pack(fill="x")

        ttk.Label(top, text="Connected board:").pack(side="left")

        self.target = tk.StringVar()

        self.target_box = ttk.Combobox(top, textvariable=self.target, state="readonly", width=30); self.target_box.pack(side="left", padx=8)

        ttk.Button(top, text="Disconnect", command=self.disconnect).pack(side="left")

        ttk.Button(top, text="Read board info", command=lambda: self.command("info")).pack(side="left", padx=6)

        form = ttk.LabelFrame(page, text="Ranging setup", padding=12); form.pack(fill="x", pady=12)

        self.mode = tk.StringVar(value="Tag"); self.own = tk.StringVar(); self.peers = tk.StringVar()

        for row, (label, variable, values) in enumerate((("Role / mode", self.mode, ("Tag", "Single anchor", "Multi anchor")), ("Local ID (hex)", self.own, None), ("Peer ID / tag roster (hex, comma separated)", self.peers, None))):

            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=5)

            entry = ttk.Combobox(form, textvariable=variable, values=values, state="readonly") if values else ttk.Entry(form, textvariable=variable)

            entry.grid(row=row, column=1, sticky="ew", padx=12)

        form.columnconfigure(1, weight=1)

        actions = ttk.Frame(form); actions.grid(row=3, column=0, columnspan=2, sticky="w", pady=8)

        ttk.Button(actions, text="Configure & start", command=self.start_tracking).pack(side="left")

        ttk.Button(actions, text="Stop ranging", command=lambda: self.command("uwb stop")).pack(side="left", padx=8)

        ttk.Button(actions, text="UWB status", command=lambda: self.command("uwb status")).pack(side="left")

        ttk.Button(actions, text="Multi-tag stats", command=lambda: self.command("multitag stats")).pack(side="left", padx=8)

        ttk.Label(form, text="Configure/start each tag first, then the anchor. Use unique IDs. Up to 8 tags per anchor. Calibration must already be valid.", wraplength=990).grid(row=4, column=0, columnspan=2, sticky="w")

        ttk.Label(page, text="Live distances • Anchor-to-tag range, not XY position • Readings turn STALE after 3 seconds").pack(anchor="w")

        self.measurements = ttk.Treeview(page, columns=("board", "tag", "distance", "age", "state"), show="headings", height=8)

        for col, title in zip(self.measurements["columns"], ("Reporting board", "Tag ID", "Distance (m)", "Age (s)", "State")):

            self.measurements.heading(col, text=title); self.measurements.column(col, width=155)

        self.measurements.pack(fill="both", expand=True, pady=8)

        sensors = ttk.Frame(page); sensors.pack(fill="x")

        for label, command in (("IMU initialize", "imu init"), ("IMU read", "imu read"), ("IMU stream", "imu stream"), ("IMU stop", "imu stop"), ("Diagnostics", "diag")):

            ttk.Button(sensors, text=label, command=lambda c=command: self.command(c)).pack(side="left", padx=(0, 6))



    def make_console(self, page):

        self.console = tk.Text(page, wrap="word", bg="#142331", fg="#dae7ef", font=("Consolas", 10), state="disabled")

        scroll = ttk.Scrollbar(page, command=self.console.yview); self.console.configure(yscrollcommand=scroll.set)

        scroll.pack(side="right", fill="y"); self.console.pack(fill="both", expand=True)

        bar = ttk.Frame(page); bar.pack(fill="x", pady=8)

        self.raw = tk.StringVar()

        entry = ttk.Entry(bar, textvariable=self.raw); entry.pack(side="left", fill="x", expand=True)

        entry.bind("<Return>", lambda _: self.command(self.raw.get()))

        ttk.Button(bar, text="Send to selected connected board", command=lambda: self.command(self.raw.get())).pack(side="left", padx=8)

        ttk.Label(page, text=f"Logs and range CSV: {self.output}", wraplength=980).pack(anchor="w")



    def selected(self):

        selected = [self.devices[k] for k in self.boards.selection()]

        if not selected:

            raise ValueError("Select at least one board in the list")

        return selected



    def set_devices(self, devices):

        self.devices = {d["address"]: d for d in devices}

        self.boards.delete(*self.boards.get_children())

        for address, d in self.devices.items():

            self.boards.insert("", "end", iid=address, values=(d["name"], address, d.get("rssi", "Loaded roster")))



    def scan(self):

        self.run("Scanning BLE for 8 seconds…", discover, lambda result: self.set_devices(result))



    def refresh_ports(self):

        from serial.tools import list_ports

        self.port_box["values"] = [p.device for p in list_ports.comports()]



    def load_roster(self):

        name = filedialog.askopenfilename(filetypes=[("Board roster", "*.json")])

        if name:

            try:

                from ota_batch import validate_roster

                self.set_devices(validate_roster(json.loads(Path(name).read_text())))

            except Exception as exc:

                messagebox.showerror("Roster", str(exc))



    def save_roster(self):

        try:

            boards = [{k: b[k] for k in ("name", "address")} for b in self.selected()]

        except Exception as exc:

            messagebox.showerror("Roster", str(exc)); return

        name = filedialog.asksaveasfilename(defaultextension=".json", initialfile="boards_ota.json", filetypes=[("Board roster", "*.json")])

        if name:

            Path(name).write_text(json.dumps({"expected_count": len(boards), "boards": boards}, indent=2)+"\n")



    def choose_image(self):

        name = filedialog.askopenfilename(filetypes=[("Signed firmware", "*.bin")])

        if name:

            self.image_path.set(name)



    def connect_ble(self):

        try:

            boards = self.selected()

            if len(boards) != 1:

                raise ValueError("Select one board to connect for control")

            b = boards[0]

            if sum(s.address is not None for s in self.sessions.values()) >= 2:

                raise ValueError("Disconnect a BLE control session first (two simultaneous links supported)")

            self.connect(b["name"], address=b["address"])

        except Exception as exc:

            messagebox.showerror("Connection", str(exc))



    def connect_usb(self):

        if self.port.get():

            self.connect(self.port.get(), port=self.port.get())



    def connect(self, label, **kwargs):

        if label in self.sessions:

            self.target.set(label); return

        if self.fleet is not None:

            messagebox.showerror("Tracking", "Stop the fleet before opening another control connection"); return

        async def job():

            session = Session(label, self.emit, **kwargs)

            await session.open()

            return session

        def done(session):

            self.sessions[label] = session

            self.target_box["values"] = list(self.sessions)

            self.target.set(label)

        self.run(f"Connecting {label}…", job, done)



    def get_target(self):

        session = self.sessions.get(self.target.get())

        if not session:

            raise ValueError("Connect and select a board first")

        return session



    def disconnect(self):

        try:

            session = self.get_target()

            if self.fleet is not None:

                self.stop_fleet(); return

            def done(_):

                self.remove_session(session.label)

            self.run("Disconnecting (configured ranging continues)…", session.close, done)

        except Exception as exc:

            messagebox.showerror("Connection", str(exc))



    def remove_session(self, label):

        session = self.sessions.pop(label, None)

        if session and not session.closed:

            asyncio.run_coroutine_threadsafe(session.close(), self.loop)

        self.target_box["values"] = list(self.sessions)

        if self.target.get() == label:

            self.target.set(next(iter(self.sessions), ""))



    def command(self, command):

        try:

            session = self.get_target()

            if self.fleet is not None:

                if command in ("uwb stop", "multitag stop"):

                    self.stop_fleet(); return

                if command not in ("info", "help", "uwb status", "multitag stats", "multitag status", "imu status", "imu read", "imu stream", "imu stop"):

                    raise ValueError("Stop the fleet before changing radio configuration or running diagnostics")

            self.run(f"{session.label}: {command}", lambda: session.command(command))

        except Exception as exc:

            messagebox.showerror("Command", str(exc))



    def start_tracking(self):

        try:

            if self.fleet is not None:

                raise ValueError("Stop the fleet before using individual-board setup")

            from control_backend import tracking_commands

            session = self.get_target()

            args = self.mode.get(), self.own.get(), self.peers.get()

            tracking_commands(*args)

            self.run(f"Configuring {session.label}…", lambda: session.configure(*args))

        except Exception as exc:

            messagebox.showerror("Tracking setup", str(exc))



    def ota(self):

        if self.busy:

            return

        try:

            boards = [{k: b[k] for k in ("name", "address")} for b in self.selected()]

            path = Path(self.image_path.get()).resolve()

            info = image_info(path.read_bytes())

            parallel = self.parallel.get()

        except Exception as exc:

            messagebox.showerror("Firmware", str(exc)); return

        if not messagebox.askyesno("Update selected boards", f"Install {info['version']} on these {len(boards)} boards?\n\n" + "\n".join(b["name"] for b in boards) + "\n\nControl connections will close. Ranging must be configured again after reboot."):

            return

        async def job():

            self.ota_running = True

            try:

                await self.stop_tracking()

                for session in list(self.sessions.values()):

                    await session.close()

                    self.emit("disconnected", session.label)

                roster = self.output / "ota-roster.json"

                roster.write_text(json.dumps({"expected_count": len(boards), "boards": boards}))

                # Separate process isolates the uploader and streams its exact results.

                proc = await asyncio.create_subprocess_exec(sys.executable, "-u", str(ROOT / "tools/ota_batch.py"),

                    "update", "--roster", str(roster), "--image", str(path), "--parallel", str(parallel),

                    cwd=str(ROOT), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)

                async for line in proc.stdout:

                    self.emit("ota", line.decode(errors="replace").rstrip())

                if await proc.wait():

                    raise RuntimeError("OTA incomplete; inspect the per-board report in Console before retrying")

            finally:

                self.ota_running = False

        self.run(f"Updating {len(boards)} boards to {info['version']}…", job)



    def log(self, label, line):

        stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

        text = f"{stamp} [{label}] {line}\n"

        self.log_file.write(text); self.log_file.flush()

        self.console.configure(state="normal"); self.console.insert("end", text)

        if int(self.console.index("end-1c").split(".")[0]) > 3000:

            self.console.delete("1.0", "501.0")

        self.console.see("end"); self.console.configure(state="disabled")



    def pump(self):

        if self.closing:

            return

        for _ in range(300):

            try:

                kind, value = self.events.get_nowait()

            except queue.Empty:

                break

            if kind == "done":

                label, result, callback, error = value

                self.busy = False

                self.status.set(f"Failed: {error}" if error else f"Complete: {label}")

                self.log("APP", self.status.get())

                if not error and callback:

                    callback(result)

            elif kind == "disconnected":

                self.remove_session(value)

                self.log(value, "Disconnected; reconnect to send commands")

            elif kind == "fleet":

                self.log("TRACKING", value); self.status.set(value)

            elif kind == "fleet_stopped":

                self.multi_view.running(False)
                self.auto_view.running(False)

                self.tracking_view.running = False

                self.tracking_view.anchor_box.configure(state="readonly")

                self.tracking_view.indicator_box.configure(state="normal")

            elif kind == "coop_slot":
                self.auto_view.slot(value)
                self.uwb3d.runtime.legacy(int(value['a'],16),int(value['b'],16),value['distance'],value['stamp'])
                self.log('COOP-SLOT',json.dumps(value))
            elif kind == "coop_frame":
                self.auto_view.frame(value)
                self.log('COOP-FRAME',json.dumps(value))
            elif kind == "auto_begin":
                if self.auto_view.model: self.auto_view.model.begin(value)
            elif kind == "auto_reconnecting":
                if self.auto_view.model: self.auto_view.model.invalidate(value)
                self.log('AUTO-RECONNECT',value)
            elif kind == "auto_tag":
                accepted = self.auto_view.tag_update(value)
                self.log('AUTO-TAG',json.dumps(dict(rows=value,accepted=accepted)))
            elif kind == "auto_epoch":
                self.auto_view.epoch(value)
                self.log("AUTO-ANCHOR", json.dumps(value))
            elif kind == "multi_sample":
                self.multi_view.sample(value)

                self.log("FOUR-ANCHOR", str(value))

            elif kind == "range_timing":
                self.active_multi_view.roundtrip.append(value)
            elif kind == "multi_failed":

                self.active_multi_view.state.set("Ranging stopped: " + value + "; use Stop fleet before restarting")
                if self.active_multi_view.automatic:
                    self.active_multi_view.is_running = False
                    self.active_multi_view.model.invalidate('Ranging stopped: ' + value)
                    self.active_multi_view.model.last_epoch = None

                self.log("FOUR-ANCHOR", value)

            elif kind in ("ota", "program"):

                self.log("OTA" if kind == "ota" else "ST-LINK", value); self.status.set(value)

            elif kind == "line":

                label, line, received = value

                self.log(label, line)

                self.tracking_view.feed(label, line, received)

                self.multi_view.feed(label, line, received)
                self.auto_view.feed(label, line, received)
                parsed = parse_uwb(line)

                if parsed:

                    tag, distance, age = parsed

                    key = label+"/"+tag

                    self.ranges[key] = (label, tag, distance, received, age)

                    self.csv_writer.writerow([datetime.now(timezone.utc).isoformat(), label, tag, distance, age])

                    self.csv_file.flush()

        for key, (label, tag, distance, received, source_age) in self.ranges.items():

            age = time.monotonic()-received + source_age/1000

            values = label, tag, f"{distance:.4f}", f"{age:.1f}", "LIVE" if age < 3 else "STALE"

            if self.measurements.exists(key):

                self.measurements.item(key, values=values)

            else:

                self.measurements.insert("", "end", iid=key, values=values)

        self.tracking_view.refresh()

        self.multi_view.refresh()
        self.auto_view.refresh()

        self.root.after(25, self.pump)



    def close(self):

        if self.busy:

            self.status.set("Wait for the current operation before closing; OTA must finish verification.")

            return

        if self.uwb3d.runtime.tasks or self.uwb3d.runtime.gateway is not None:
            self.run('Stopping 3D acquisition…',self.uwb3d.runtime.stop,lambda _:self.close())
            return

        if self.fleet is not None:

            self.run("Stopping fleet before closing…", self.stop_tracking, lambda _: self.close())

            return

        self.closing = True

        async def cleanup():

            await asyncio.gather(*(s.close() for s in list(self.sessions.values())), return_exceptions=True)
            await self.uwb3d.runtime.shutdown()

        future = asyncio.run_coroutine_threadsafe(cleanup(), self.loop)

        def finish():

            if not future.done():

                self.root.after(100, finish); return

            self.loop.call_soon_threadsafe(self.loop.stop)

            self.log_file.close(); self.csv_file.close(); self.root.destroy()

        finish()





if __name__ == "__main__":

    root = tk.Tk()

    app = ControlApp(root)

    if "--scan" in sys.argv:

        root.after(300, app.scan)

    root.mainloop()

