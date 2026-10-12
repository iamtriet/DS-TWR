"""Tk multi-tag range dashboard; every plotted point is received telemetry."""
import math
import time
import tkinter as tk
from tkinter import ttk
from tracking import RangeHistory

COLORS = ("#16a6c5", "#d28016", "#875ce0", "#21a174", "#cc5b85", "#4578d2", "#9a8c23", "#a65b35")


class TrackingView(ttk.Frame):
    def __init__(self, parent, prepare, start, stop):
        super().__init__(parent, padding=12)
        self.model = None
        self.running = False
        self.last_draw = 0
        self.anchor = tk.StringVar()
        self.indicator = tk.BooleanVar(value=True)
        self.summary = tk.StringVar(value="Select boards in Boards & OTA, then load them here.")
        bar = ttk.Frame(self); bar.pack(fill="x")
        ttk.Button(bar, text="Use selected boards", command=prepare).pack(side="left")
        ttk.Label(bar, text="Anchor:").pack(side="left", padx=(12, 4))
        self.anchor_box = ttk.Combobox(bar, textvariable=self.anchor, state="readonly", width=21)
        self.anchor_box.pack(side="left")
        self.anchor_box.bind("<<ComboboxSelected>>", lambda _: prepare(reuse=True))
        ttk.Button(bar, text="Start fleet", command=start).pack(side="left", padx=8)
        ttk.Button(bar, text="Stop fleet", command=stop).pack(side="left")
        self.indicator_box = ttk.Label(self, text="Automatic LED3: solid for anchors, blinking for tags (LED2 is charger-controlled)")
        self.indicator_box.pack(anchor="w", pady=(6, 0))
        ttk.Label(self, textvariable=self.summary, font=("Segoe UI", 11, "bold"), wraplength=990).pack(anchor="w", pady=10)
        ttk.Label(self, text="One anchor • Measured distances, not XY positions • 60-second history • STALE after 3 seconds").pack(anchor="w")
        cols = ("board", "id", "distance", "age", "rate", "success", "timeouts", "state")
        self.table = ttk.Treeview(self, columns=cols, show="headings", height=6)
        for col, title, width in zip(cols, ("Tag board", "UWB ID", "Range (m)", "Age (s)", "Samples/s¹", "Success²", "Timeouts²", "State"), (185, 65, 95, 65, 90, 85, 80, 90)):
            self.table.heading(col, text=title); self.table.column(col, width=width, stretch=True)
        self.table.pack(fill="x", pady=10)
        plots = ttk.Frame(self); plots.pack(fill="both", expand=True)
        self.lanes = tk.Canvas(plots, height=210, bg="#142331", highlightthickness=0)
        self.history = tk.Canvas(plots, height=210, bg="#142331", highlightthickness=0)
        self.lanes.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.history.pack(side="left", fill="both", expand=True, padx=(6, 0))
        ttk.Label(self, text="¹ Received samples over the last 5 seconds.  ² Firmware counters since fleet start; refreshed every 5 seconds. CSV is saved in Console & Logs.", wraplength=990).pack(anchor="w", pady=(8, 0))

    def set_plan(self, plan):
        self.model = RangeHistory(plan)
        self.table.delete(*self.table.get_children())
        for i, (tag, row) in enumerate(self.model.rows.items()):
            self.table.tag_configure(tag, foreground=COLORS[i])
            self.table.insert("", "end", iid=tag, tags=(tag,), values=(row["board"], tag, "—", "—", "0.0", "—", "—", "WAITING"))
        self.running = False
        self.refresh(force=True)

    def feed(self, board, line, received):
        if self.model: self.model.feed(board, line, received)

    def refresh(self, force=False):
        now = time.monotonic()
        if not force and now-self.last_draw < .25: return
        self.last_draw = now
        if not self.model: return
        live = 0
        for tag, row in self.model.rows.items():
            state = self.model.state(tag)
            live += state == "LIVE"
            distance = f"{row['samples'][-1][1]:.3f}" if row["samples"] else "—"
            age = f"{max(0, now-row['last']):.1f}" if row["last"] is not None else "—"
            success = f"{100*row['success']/row['attempts']:.1f}%" if row["attempts"] else "—"
            self.table.item(tag, values=(row["board"], tag, distance, age, f"{self.model.rate(tag):.1f}", success,
                                        row["timeouts"] if row["timeouts"] is not None else "—", state if self.running else "STOPPED"))
        anchor = self.model.plan["anchor"]
        self.summary.set(f"{anchor['name']} / {anchor['id']}   •   {live if self.running else 0}/{len(self.model.rows)} tags live   •   {'Tracking' if self.running else 'Ready / stopped'}")
        samples = [d for row in self.model.rows.values() for t, d in row["samples"] if now-t <= 60]
        ceiling = max(1.0, math.ceil(max(samples, default=0)*1.15*2)/2)
        for canvas in (self.lanes, self.history): canvas.delete("all")
        w, h = max(200, self.lanes.winfo_width()), max(120, self.lanes.winfo_height())
        self.lanes.create_text(12, 14, text="LATEST RANGE · metres", fill="#dce7ef", anchor="w")
        left, right = 74, w-30
        for i, (tag, row) in enumerate(self.model.rows.items()):
            y = 42+i*max(19, (h-68)/len(self.model.rows))
            color = COLORS[i]
            self.lanes.create_text(12, y, text=tag, fill=color, anchor="w")
            self.lanes.create_line(left, y, right, y, fill="#354653")
            if row["samples"]:
                d = row["samples"][-1][1]
                x = left+min(1, d/ceiling)*(right-left)
                self.lanes.create_oval(x-4, y-4, x+4, y+4, fill=color if self.running and self.model.state(tag)=="LIVE" else "#78828a", outline="")
        self.lanes.create_text(left, h-13, text="0", fill="#a9bac6")
        self.lanes.create_text(right, h-13, text=f"{ceiling:g} m", fill="#a9bac6")
        w, h = max(200, self.history.winfo_width()), max(120, self.history.winfo_height())
        self.history.create_text(12, 14, text="RANGE HISTORY · last 60 seconds", fill="#dce7ef", anchor="w")
        left, right, top, bottom = 45, w-16, 34, h-26
        for n in range(5):
            y = bottom-(bottom-top)*n/4
            self.history.create_line(left, y, right, y, fill="#354653")
            self.history.create_text(left-6, y, text=f"{ceiling*n/4:g}", fill="#a9bac6", anchor="e")
        self.history.create_text(left, h-12, text="−60s", fill="#a9bac6")
        self.history.create_text(right, h-12, text="now", fill="#a9bac6", anchor="e")
        for i, row in enumerate(self.model.rows.values()):
            points = []; previous = None
            def flush():
                if len(points) >= 4: self.history.create_line(*points, fill=COLORS[i], width=2)
            for t, d in row["samples"]:
                if now-t > 60: continue
                if previous is not None and t-previous > 3:
                    flush(); points = []
                points += [left+(1-(now-t)/60)*(right-left), bottom-d/ceiling*(bottom-top)]
                previous = t
            flush()
