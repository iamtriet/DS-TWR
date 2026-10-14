#!/usr/bin/env python3
"""
Cooperative Localization Live — Real-time 2D relative position tracking.

Connects to COM10 gateway sniffer, uses the EXISTING U3D protocol and
uwb3d_core Engine to compute anchor-free relative positions for all
active nodes, then displays them in a live matplotlib plot.

This script:
  1. Opens COM10 at 115200 baud (the existing firmware baud rate)
  2. Sends CLI commands to configure the gateway TDMA network
  3. Reads U3D COBS packets containing RANGE measurements
  4. Feeds them into the existing uwb3d_core.Engine solver
  5. Displays live 2D node positions with matplotlib

Usage:
    python tools/cooperative_live.py                          # 6 nodes, COM10
    python tools/cooperative_live.py --port COM12 --nodes 4   # different port
    python tools/cooperative_live.py --no-gui                 # headless text
    python tools/cooperative_live.py --record session.jsonl   # record session
"""
import argparse
import asyncio
import json
import math
import struct
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from uwb3d_protocol import (
    Decoder, encode, decode_range,
    DISCOVERY, DISCOVERY_BODY, RANGE, HEARTBEAT, STATUS, NETWORK,
    TIMESTAMPS, Broadcast, BroadcastLedger,
)
from uwb3d_core import Config, Engine, Matrix, Range, sparse_edges, align


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE PIPELINE — wraps the existing Engine with a UART thread
# ═══════════════════════════════════════════════════════════════════════════════

class LiveCooperativePipeline:
    """Reads U3D COBS from COM port, feeds Engine, produces positions."""

    def __init__(self, port: str, baud: int = 115200,
                 n_nodes: int = 6, dimensions: int = 2,
                 superframe_ms: int = 500, slot_ms: int = 10):
        self.port_name = port
        self.baud = baud
        self.n_nodes = n_nodes

        config = Config(
            dimensions=dimensions,
            max_nodes=30,
            target_rate_hz=10.0,
            max_age=1.5,
            gateway_slot_ms=slot_ms,
            gateway_superframe_ms=superframe_ms,
        ).validate()

        self.config = config
        self.engine = Engine(config)
        self.decoder = Decoder()
        self.ledger = BroadcastLedger()
        self.offsets = deque(maxlen=100)

        # State
        self.positions = {}  # {node_id: [x, y, z]}
        self.measured = {}   # raw measured (before filter)
        self.state_str = 'initializing'
        self.edges = []
        self.error = ''
        self.network = {}

        # Stats
        self.ranges_count = 0
        self.discoveries = 0
        self.packets_ok = 0
        self.packets_err = 0
        self.solver_ms = 0.0
        self.rms_m = 0.0
        self.started = time.monotonic()
        self.last_solve_time = 0.0

        # Serial
        self.ser = None
        self.outgoing = deque(maxlen=32)

    def open_serial(self):
        """Open serial port."""
        import serial
        self.ser = serial.Serial(self.port_name, self.baud,
                                 timeout=0, rtscts=False, dsrdtr=False)

    def close_serial(self):
        if self.ser:
            self.ser.close()
            self.ser = None

    def send_command(self, cmd: str):
        """Queue a CLI command to send to the board."""
        self.outgoing.append(cmd)

    def _flush_commands(self):
        """Send pending commands to the board."""
        while self.outgoing and self.ser:
            cmd = self.outgoing.popleft()
            try:
                self.ser.write((cmd + '\n').encode('ascii'))
            except Exception:
                pass

    def read_and_process(self) -> bool:
        """Read available UART data and process packets. Returns True if new positions."""
        if not self.ser:
            return False

        self._flush_commands()

        data = self.ser.read(self.ser.in_waiting or 1)
        if not data:
            return False

        received = time.monotonic()
        new_solution = False

        for packet in self.decoder.feed(data):
            self.packets_ok += 1
            try:
                if packet.reset:
                    self.engine.matrix = Matrix(self.config)
                    self.engine.reset()
                    self.offsets.clear()

                sent = packet.timestamp_us / 1e6
                self.offsets.append(received - sent)
                offset = min(self.offsets)

                if packet.kind == DISCOVERY:
                    if len(packet.payload) != DISCOVERY_BODY.size:
                        continue
                    id, uid, major, minor, patch, caps = DISCOVERY_BODY.unpack(packet.payload)
                    self.engine.registry.register(
                        id, str(uid), received,
                        firmware=f'{major}.{minor}.{patch}',
                        capabilities=caps
                    )
                    self.discoveries += 1

                elif packet.kind == RANGE:
                    r = decode_range(packet.payload)
                    effective = r.pop('effective_us') / 1e6 + offset
                    rng = Range(**r, effective=effective, received=received)
                    if self.engine.ingest(rng):
                        self.ranges_count += 1

                elif packet.kind == TIMESTAMPS:
                    for a, b, seq, d in self.ledger.add(Broadcast.unpack(packet.payload)):
                        self.network['timestamp_ranges'] = self.network.get('timestamp_ranges', 0) + 1

                elif packet.kind == NETWORK:
                    if len(packet.payload) == 10:
                        sf, sync, mode, period = struct.unpack('<IBBI', packet.payload)
                        self.network = dict(superframe=sf, synchronized=bool(sync),
                                          mode=mode, period_us=period)

            except (ValueError, KeyError, struct.error):
                self.packets_err += 1

        # Run solver
        now = time.monotonic()
        state = self.engine.estimate(now)

        if state.get('positions'):
            self.positions = state['positions']
            self.measured = state.get('measured_positions', {})
            self.state_str = state.get('state', 'unknown')
            self.edges = state.get('edges', [])
            self.rms_m = state.get('metrics', {}).get('rms', 0.0) or 0.0
            self.solver_ms = state.get('metrics', {}).get('solver_ms', 0.0) or 0.0
            self.error = state.get('error', '')
            new_solution = True
        elif state.get('state'):
            self.state_str = state['state']
            self.error = state.get('error', '')

        return new_solution


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE VISUALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

def run_gui(pipeline: LiveCooperativePipeline, stop_event: threading.Event):
    """Matplotlib live 2D plot of node positions."""
    import matplotlib
    matplotlib.use('TkAgg')
    import matplotlib.pyplot as plt
    import matplotlib.animation as anim

    fig, ax = plt.subplots(figsize=(10, 10))
    fig.patch.set_facecolor('#0f0f23')
    ax.set_facecolor('#1a1a2e')
    ax.set_xlim(-3.5, 3.5)
    ax.set_ylim(-3.5, 3.5)
    ax.set_aspect('equal')
    ax.set_title('Cooperative Localization — Live', color='#e0e0e0',
                 fontsize=18, fontweight='bold', pad=15)
    ax.set_xlabel('X (meters)', color='#888888', fontsize=12)
    ax.set_ylabel('Y (meters)', color='#888888', fontsize=12)
    ax.tick_params(colors='#666666')
    ax.grid(True, alpha=0.12, color='white', linestyle='--')
    for spine in ax.spines.values():
        spine.set_color('#333333')

    colors_map = plt.cm.Set2(np.linspace(0, 1, 8))
    scatter = ax.scatter([], [], s=200, zorder=5,
                         edgecolors='white', linewidth=1.5)

    # Node labels
    labels = {}
    # Edge lines
    edge_lines = []

    # Status text
    status_text = ax.text(0.02, 0.98, '', transform=ax.transAxes,
                          fontsize=10, color='#00ff88', fontfamily='monospace',
                          verticalalignment='top',
                          bbox=dict(boxstyle='round,pad=0.4',
                                    facecolor='#1a1a2e', edgecolor='#333',
                                    alpha=0.9))

    def update(_frame):
        pos = pipeline.positions

        # Clear old edge lines
        while edge_lines:
            edge_lines.pop().remove()

        if pos:
            ids = sorted(pos.keys())
            xy = np.array([pos[n][:2] for n in ids])
            node_colors = [colors_map[n % len(colors_map)] for n in ids]

            scatter.set_offsets(xy)
            scatter.set_facecolors(node_colors)

            # Draw edges
            for edge in pipeline.edges:
                a, b = edge.get('a', 0), edge.get('b', 0)
                if isinstance(edge, dict) and a in pos and b in pos:
                    pa, pb = pos[a][:2], pos[b][:2]
                    nlos = edge.get('nlos', False)
                    color = '#ff4444' if nlos else '#44ff4488'
                    line, = ax.plot([pa[0], pb[0]], [pa[1], pb[1]],
                                   color=color, alpha=0.3, linewidth=1, zorder=2)
                    edge_lines.append(line)

            # Update labels
            for n in ids:
                if n not in labels:
                    labels[n] = ax.text(0, 0, '', fontsize=11, color='white',
                                        ha='center', va='bottom', fontweight='bold',
                                        zorder=10)
                labels[n].set_position((pos[n][0], pos[n][1] + 0.15))
                labels[n].set_text(f'N{n}')

            # Hide labels for departed nodes
            for n in list(labels):
                if n not in pos:
                    labels[n].set_text('')

            # Auto-scale
            if len(xy) >= 2:
                margin = 0.5
                xmin, xmax = xy[:, 0].min() - margin, xy[:, 0].max() + margin
                ymin, ymax = xy[:, 1].min() - margin, xy[:, 1].max() + margin
                span = max(xmax - xmin, ymax - ymin, 1.0)
                cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
                ax.set_xlim(cx - span / 2, cx + span / 2)
                ax.set_ylim(cy - span / 2, cy + span / 2)
        else:
            scatter.set_offsets(np.empty((0, 2)))

        elapsed = time.monotonic() - pipeline.started
        status_text.set_text(
            f'State: {pipeline.state_str}\n'
            f'Nodes: {len(pos)} | Edges: {len(pipeline.edges)}\n'
            f'Ranges: {pipeline.ranges_count} | RMS: {pipeline.rms_m*100:.1f} cm\n'
            f'Solver: {pipeline.solver_ms:.1f} ms\n'
            f'Packets: {pipeline.packets_ok} ok, {pipeline.packets_err} err\n'
            f'Elapsed: {elapsed:.0f}s'
        )

        return scatter,

    ani = anim.FuncAnimation(fig, update, interval=200, blit=False, cache_frame_data=False)

    def on_close(evt):
        stop_event.set()

    fig.canvas.mpl_connect('close_event', on_close)

    plt.tight_layout()
    try:
        plt.show()
    except KeyboardInterrupt:
        stop_event.set()


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    import serial

    ap = argparse.ArgumentParser(description='Cooperative Localization — Live 2D Tracking')
    ap.add_argument('--port', default='COM10', help='Gateway/sniffer COM port (default: COM10)')
    ap.add_argument('--baud', type=int, default=115200, help='Baud rate (default: 115200)')
    ap.add_argument('--nodes', type=int, default=6, help='Expected node count')
    ap.add_argument('--slot-ms', type=int, default=10, help='TDMA slot duration (ms)')
    ap.add_argument('--superframe-ms', type=int, default=500, help='Superframe period (ms)')
    ap.add_argument('--no-gui', action='store_true', help='Text-only mode')
    ap.add_argument('--record', type=Path, default=None, help='Record session to JSONL')
    ap.add_argument('--dim', type=int, default=2, choices=[2, 3], help='Dimensions (2=planar, 3=3D)')
    ap.add_argument('--no-configure', action='store_true',
                    help='Skip sending net configure/start commands (boards already running)')
    args = ap.parse_args()

    print('╔══════════════════════════════════════════════╗')
    print('║  Cooperative Localization — Live Tracking    ║')
    print('╠══════════════════════════════════════════════╣')
    print(f'║  Port:       {args.port} @ {args.baud} baud')
    print(f'║  Nodes:      {args.nodes}')
    print(f'║  Dimensions: {args.dim}D')
    print(f'║  TDMA:       {args.slot_ms}ms slot, {args.superframe_ms}ms superframe')
    print('╚══════════════════════════════════════════════╝')
    print()

    pipeline = LiveCooperativePipeline(
        port=args.port,
        baud=args.baud,
        n_nodes=args.nodes,
        dimensions=args.dim,
        superframe_ms=args.superframe_ms,
        slot_ms=args.slot_ms,
    )

    try:
        pipeline.open_serial()
        print(f'  ✓ Connected to {args.port}')
    except serial.SerialException as e:
        print(f'  ✗ Cannot open {args.port}: {e}')
        sys.exit(1)

    # Configure the network if needed
    if not args.no_configure:
        print(f'  Configuring {args.nodes}-node TDMA network...')
        time.sleep(0.3)

        # Stop any existing activity
        pipeline.ser.write(b'uwb stop\n')
        time.sleep(0.5)

        # Initialize UWB
        pipeline.ser.write(b'uwb init\n')
        time.sleep(0.5)

        # Enable binary output for U3D protocol
        pipeline.ser.write(b'uwb output binary\n')
        time.sleep(0.2)

        # Drain startup messages
        pipeline.ser.read(pipeline.ser.in_waiting or 1)

        print(f'  ✓ UWB initialized, binary output enabled')
        print(f'  Note: Other boards must already be running with')
        print(f'        "net configure" and "net start experimental"')
        print()
    else:
        print('  Skipping configuration (--no-configure)')
        print()

    stop = threading.Event()
    record_file = None
    if args.record:
        record_file = open(args.record, 'w', encoding='utf-8')
        record_file.write(json.dumps({
            'type': 'metadata',
            'port': args.port,
            'nodes': args.nodes,
            'created': time.time(),
        }) + '\n')

    last_print = 0.0

    def uart_thread():
        """Background UART read + pipeline processing."""
        nonlocal last_print
        while not stop.is_set():
            try:
                updated = pipeline.read_and_process()
                now = time.monotonic()

                if args.no_gui and updated and now - last_print > 0.5:
                    last_print = now
                    pos = pipeline.positions
                    elapsed = now - pipeline.started
                    if pos:
                        parts = []
                        for n in sorted(pos.keys()):
                            p = pos[n]
                            parts.append(f'N{n}:({p[0]:+.2f},{p[1]:+.2f})')
                        print(f'[{elapsed:6.1f}s] {pipeline.state_str} | '
                              f'RMS={pipeline.rms_m*100:.1f}cm | '
                              + ' '.join(parts))
                    else:
                        print(f'[{elapsed:6.1f}s] {pipeline.state_str} | '
                              f'ranges={pipeline.ranges_count} '
                              f'discoveries={pipeline.discoveries}')

                if record_file and updated:
                    record_file.write(json.dumps({
                        'type': 'state',
                        'elapsed': time.monotonic() - pipeline.started,
                        'positions': pipeline.positions,
                        'state': pipeline.state_str,
                        'rms': pipeline.rms_m,
                        'edges': len(pipeline.edges),
                    }) + '\n')
                    record_file.flush()

                if not pipeline.ser.in_waiting:
                    time.sleep(0.005)

            except serial.SerialException:
                print('Serial disconnected')
                stop.set()
                break
            except Exception as e:
                print(f'Error: {e}')
                time.sleep(0.1)

    reader = threading.Thread(target=uart_thread, daemon=True)
    reader.start()

    if args.no_gui:
        print('Running in headless mode. Press Ctrl+C to stop.')
        try:
            while not stop.is_set():
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
    else:
        print('Starting visualization...')
        run_gui(pipeline, stop)

    # Cleanup
    stop.set()
    reader.join(timeout=2)
    pipeline.close_serial()
    if record_file:
        record_file.close()

    print(f'\n Session summary:')
    print(f'  Ranges:      {pipeline.ranges_count}')
    print(f'  Discoveries: {pipeline.discoveries}')
    print(f'  Packets OK:  {pipeline.packets_ok}')
    print(f'  Packets ERR: {pipeline.packets_err}')
    print(f'  Last RMS:    {pipeline.rms_m*100:.1f} cm')


if __name__ == '__main__':
    main()
