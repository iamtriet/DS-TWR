#!/usr/bin/env python3
"""
DS-TWR Live Localization — Real-time 2D position tracking from sniffer UART.

Connects to COM10 (DWM3001C sniffer in DS-TWR mode), parses broadcast
frames, assembles DS-TWR distance matrix, and runs the full localization
pipeline: SMACOF → Kabsch → EKF.

Usage:
    python tools/dstwr_live.py                     # COM10, 115200
    python tools/dstwr_live.py --port COM12        # different port
    python tools/dstwr_live.py --baud 1000000      # 1 Mbps
    python tools/dstwr_live.py --no-gui            # headless, text only
"""
import argparse
import struct
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

# ── Import localization pipeline from host_localization_engine ──
sys.path.insert(0, str(Path(__file__).parent))
from host_localization_engine import (
    WeightedSMACOFSolver,
    OpportunisticKabschAligner,
    SwarmEKFTracker,
    N_NODES,
    SPEED_OF_LIGHT,
    DW_TICK_PERIOD,
    NLOS_THRESHOLD_DB,
)

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

SYNC_BYTE = 0xAA
REPORT_TYPE_BROADCAST = 0x01
MASK40 = 0xFFFFFFFFFF
MAX_FRAME = 512


def crc32_compute(data: bytes) -> int:
    """Firmware-compatible CRC32 (ISO HDLC polynomial)."""
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 & (-(crc & 1))) & 0xFFFFFFFF
    return crc ^ 0xFFFFFFFF


# ═══════════════════════════════════════════════════════════════════════════════
# SNIFFER UART PARSER
#
# Binary report format (from dstwr_tdma.c sniffer_report):
#   [0xAA] [len_lo len_hi] [payload...] [CRC32]
#
# Payload:
#   [type=0x01] [src_id] [src_tx_ts×5] [sniff_rx_ts×5]
#   [sf_counter×4] [n_replies]
#   [reply_entry×6 × n_replies]
#     reply_entry = [peer_id] [reply_time×4] [nlos_metric]
#
# ASCII log lines (from out()) terminate with \r\n and do NOT start with 0xAA.
# We use 0xAA as the frame sync to distinguish binary from text.
# ═══════════════════════════════════════════════════════════════════════════════

class SnifferParser:
    """Parse interleaved binary sniffer reports + ASCII logs from UART."""

    def __init__(self):
        self._buf = bytearray()
        self._text_buf = bytearray()
        self.reports = deque(maxlen=256)
        self.text_lines = deque(maxlen=64)
        self.stats = {'frames': 0, 'crc_errors': 0, 'parse_errors': 0, 'text_lines': 0}

    def feed(self, data: bytes):
        """Feed raw UART bytes."""
        for b in data:
            if b == 0xAA and len(self._buf) == 0:
                # Flush any pending text
                self._flush_text()
                # Start binary frame
                self._buf.append(b)
            elif len(self._buf) > 0:
                # Accumulating binary frame
                self._buf.append(b)
                self._try_parse_binary()
            else:
                # ASCII text accumulation
                if b == 0x0A:  # \n
                    self._flush_text()
                elif b != 0x0D:  # skip \r
                    self._text_buf.append(b)
                    if len(self._text_buf) > 1024:
                        self._text_buf.clear()

    def _flush_text(self):
        if self._text_buf:
            try:
                line = self._text_buf.decode('utf-8', errors='replace').strip()
                if line:
                    self.text_lines.append(line)
                    self.stats['text_lines'] += 1
            except Exception:
                pass
            self._text_buf.clear()

    def _try_parse_binary(self):
        buf = self._buf

        # Need at least: sync(1) + len(2) = 3 bytes to know payload length
        if len(buf) < 3:
            return

        payload_len = buf[1] | (buf[2] << 8)
        total_len = 3 + payload_len + 4  # sync + len + payload + CRC32

        if total_len > MAX_FRAME:
            # Corrupt — discard and look for next sync
            self._buf.clear()
            self.stats['parse_errors'] += 1
            return

        if len(buf) < total_len:
            return  # Need more data

        # We have a complete frame — extract and validate
        frame = bytes(buf[:total_len])
        self._buf.clear()

        # CRC check
        payload_data = frame[3:3 + payload_len]
        expected_crc = struct.unpack_from('<I', frame, 3 + payload_len)[0]
        actual_crc = crc32_compute(payload_data)

        if actual_crc != expected_crc:
            self.stats['crc_errors'] += 1
            return

        # Parse report
        report = self._decode_report(payload_data)
        if report:
            self.reports.append(report)
            self.stats['frames'] += 1
        else:
            self.stats['parse_errors'] += 1

    def _decode_report(self, data: bytes) -> dict:
        """Decode broadcast capture report payload."""
        if len(data) < 17:
            return None
        if data[0] != REPORT_TYPE_BROADCAST:
            return None

        source_id = data[1]
        source_tx_ts = int.from_bytes(data[2:7], 'little')
        sniffer_rx_ts = int.from_bytes(data[7:12], 'little')
        sf_counter = struct.unpack_from('<I', data, 12)[0]
        n_replies = data[16]

        expected_len = 17 + n_replies * 6
        if len(data) < expected_len:
            return None

        replies = []
        offset = 17
        for _ in range(n_replies):
            peer_id = data[offset]
            reply_time = struct.unpack_from('<I', data, offset + 1)[0]
            nlos_metric = data[offset + 5]
            replies.append((peer_id, reply_time, nlos_metric))
            offset += 6

        return {
            'source_id': source_id,
            'source_tx_ts': source_tx_ts,
            'sniffer_rx_ts': sniffer_rx_ts,
            'sf_counter': sf_counter,
            'replies': replies,
            'time': time.monotonic(),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE DS-TWR ASSEMBLER
#
# Assembles distance matrix from sequential broadcast frames.
# Uses the same DS-TWR math as the simulation assembler but optimized for
# streaming: buffers broadcasts by (sf_counter, node_id).
# ═══════════════════════════════════════════════════════════════════════════════

class LiveDSTWRAssembler:
    """Assemble distance matrix from live sniffer broadcast reports."""

    def __init__(self, n_nodes: int, ant_delay_bias_m: float = 0.0):
        self.n = n_nodes
        self.ant_bias = ant_delay_bias_m
        # Buffer: (sf, node) → {tx_ts, replies:{peer:(reply_ticks, nlos)}}
        self._buf = {}
        self._last_sf = -1
        self._active_nodes = set()

    def feed(self, report: dict):
        """Feed a decoded sniffer report."""
        src = report['source_id']
        sf = report['sf_counter']
        tx_ts = report['source_tx_ts']

        key = (sf, src)
        entry = {'tx_ts': tx_ts, 'replies': {}}
        for peer_id, reply_compressed, nlos_metric in report['replies']:
            entry['replies'][peer_id] = (reply_compressed << 8, nlos_metric)
        self._buf[key] = entry
        self._active_nodes.add(src)

        # GC old superframes (keep last 5)
        if sf > self._last_sf + 20:
            old = [k for k in self._buf if k[0] < sf - 5]
            for k in old:
                del self._buf[k]
        self._last_sf = max(self._last_sf, sf)

    @property
    def active_node_count(self):
        return len(self._active_nodes)

    @property
    def active_node_ids(self):
        return sorted(self._active_nodes)

    def assemble(self, sf: int):
        """Assemble D, W matrices for superframe sf using sf-1 and sf data.

        Returns:
            D: (n×n) distance matrix (NaN where unavailable)
            W: (n×n) weight matrix
            pairs: number of valid pairs computed
        """
        n = self.n
        D = np.full((n, n), np.nan)
        W = np.zeros((n, n))
        np.fill_diagonal(D, 0.0)
        pairs = 0

        sf_prev = sf - 1

        for i in sorted(self._active_nodes):
            for j in sorted(self._active_nodes):
                if j <= i:
                    continue
                dist, weight = self._compute_pair(i, j, sf_prev, sf)
                if dist is not None and weight > 0:
                    D[i-1, j-1] = dist
                    D[j-1, i-1] = dist
                    W[i-1, j-1] = weight
                    W[j-1, i-1] = weight
                    pairs += 1

        return D, W, pairs

    def _compute_pair(self, i, j, sf_prev, sf_curr):
        """Compute DS-TWR distance between nodes i and j."""
        for key in [(sf_prev, i), (sf_curr, i), (sf_prev, j), (sf_curr, j)]:
            if key not in self._buf:
                return None, 0.0

        d_ip = self._buf[(sf_prev, i)]
        d_ic = self._buf[(sf_curr, i)]
        d_jp = self._buf[(sf_prev, j)]
        d_jc = self._buf[(sf_curr, j)]

        # Need reply times
        if j not in d_ic['replies']:
            return None, 0.0
        if i not in d_jp['replies']:
            return None, 0.0
        if i not in d_jc['replies']:
            return None, 0.0

        reply2, nlos_i = d_ic['replies'][j]
        reply1, nlos_jp = d_jp['replies'][i]
        reply1_curr, nlos_jc = d_jc['replies'][i]

        # TX timestamps
        tx_ip = d_ip['tx_ts']
        tx_ic = d_ic['tx_ts']
        tx_jp = d_jp['tx_ts']
        tx_jc = d_jc['tx_ts']

        delta_tx_i = (tx_ic - tx_ip) & MASK40
        delta_tx_j = (tx_jc - tx_jp) & MASK40

        # Round1 = ΔTX_i - Reply2
        round1 = (delta_tx_i - reply2) & MASK40
        # Round2 = ΔTX_j - Reply1_curr
        round2 = (delta_tx_j - reply1_curr) & MASK40

        # Sanity: each < 0.5s
        limit = int(0.5 / DW_TICK_PERIOD)
        for val in [round1, reply1, round2, reply2]:
            if val == 0 or val > limit:
                return None, 0.0

        r1, d1, r2, d2 = float(round1), float(reply1), float(round2), float(reply2)
        denom = r1 + r2 + d1 + d2
        if denom <= 0:
            return None, 0.0

        tof = (r1 * r2 - d1 * d2) / denom
        distance = tof * DW_TICK_PERIOD * SPEED_OF_LIGHT - self.ant_bias

        if not np.isfinite(distance) or distance < 0 or distance > 100:
            return None, 0.0

        # Weight from NLOS
        nlos_max = max(nlos_i, nlos_jp, nlos_jc)
        nlos_db = nlos_max * (20.0 / 255.0)
        if nlos_db <= NLOS_THRESHOLD_DB:
            weight = 1.0
        else:
            weight = np.exp(-(nlos_db - NLOS_THRESHOLD_DB) / 3.0)
        weight = max(0.01, min(1.0, weight))

        return distance, weight


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

class LivePipeline:
    """Complete live localization pipeline."""

    def __init__(self, n_nodes: int = N_NODES, arena_size: float = 5.0):
        self.n = n_nodes
        self.arena = arena_size
        self.assembler = LiveDSTWRAssembler(n_nodes)
        self.smacof = WeightedSMACOFSolver(n_nodes, dim=2, max_iter=10, tol=1e-4)
        self.aligner = OpportunisticKabschAligner(n_nodes)
        self.ekf = SwarmEKFTracker(n_nodes, dt=1.0/25.6)

        self.positions = np.full((n_nodes, 2), np.nan)
        self.last_sf = -1
        self.frame_count = 0
        self.pair_count = 0
        self.last_update = 0.0

    def process_report(self, report: dict):
        """Process a single sniffer report and optionally produce new positions."""
        self.assembler.feed(report)
        sf = report['sf_counter']

        # Only run pipeline at superframe boundaries (new sf_counter)
        if sf <= self.last_sf:
            return False

        self.last_sf = sf

        # Need at least 2 superframes of data
        if sf < 2:
            return False

        # Assemble distance matrix
        D, W, pairs = self.assembler.assemble(sf)
        self.pair_count = pairs

        if pairs < 3:
            # Not enough data for MDS
            return False

        # SMACOF
        X_mds = self.smacof.solve(D, W)

        # Kabsch alignment
        X_aligned = self.aligner.align(X_mds, D, W)

        # EKF
        self.ekf.predict()
        self.ekf.update(X_aligned)

        self.positions = self.ekf.get_positions()
        self.frame_count += 1
        self.last_update = time.monotonic()
        return True


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE VISUALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

def run_gui(pipeline: LivePipeline, parser: SnifferParser, stop_event: threading.Event):
    """Matplotlib animation showing live node positions."""
    import matplotlib
    matplotlib.use('TkAgg')
    import matplotlib.pyplot as plt
    import matplotlib.animation as anim

    fig, ax = plt.subplots(figsize=(10, 10))
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_facecolor('#16213e')
    ax.set_xlim(-0.5, pipeline.arena + 0.5)
    ax.set_ylim(-0.5, pipeline.arena + 0.5)
    ax.set_aspect('equal')
    ax.set_title('DS-TWR Live Localization', color='white', fontsize=16, fontweight='bold')
    ax.tick_params(colors='#888888')
    ax.grid(True, alpha=0.15, color='white')
    for spine in ax.spines.values():
        spine.set_color('#333333')

    colors = plt.cm.tab20(np.linspace(0, 1, pipeline.n))
    scatter = ax.scatter([], [], s=120, c=colors, zorder=5,
                         edgecolors='white', linewidth=1.0)

    # Node ID labels
    labels = []
    for i in range(pipeline.n):
        lbl = ax.text(0, 0, '', fontsize=8, color='white', ha='center', va='bottom',
                      fontweight='bold')
        labels.append(lbl)

    # Status text
    status_text = ax.text(0.02, 0.98, '', transform=ax.transAxes,
                          fontsize=10, color='#00ff88', fontfamily='monospace',
                          verticalalignment='top')

    def update(_frame):
        pos = pipeline.positions
        valid = ~np.isnan(pos[:, 0])

        if np.any(valid):
            scatter.set_offsets(pos[valid])
            # Update colors for valid nodes only
            scatter.set_facecolors(colors[valid])
        else:
            scatter.set_offsets(np.empty((0, 2)))

        # Update labels
        for i in range(pipeline.n):
            if valid[i]:
                labels[i].set_position((pos[i, 0], pos[i, 1] + 0.08))
                labels[i].set_text(str(i + 1))
            else:
                labels[i].set_text('')

        # Status
        nodes = pipeline.assembler.active_node_ids
        node_str = ','.join(str(n) for n in nodes) if nodes else 'none'
        status_text.set_text(
            f'Nodes: {node_str}\n'
            f'Pairs: {pipeline.pair_count}\n'
            f'Frames: {pipeline.frame_count}\n'
            f'UART: {parser.stats["frames"]} ok, '
            f'{parser.stats["crc_errors"]} crc, '
            f'{parser.stats["text_lines"]} log'
        )

        return scatter,

    ani = anim.FuncAnimation(fig, update, interval=100, blit=False, cache_frame_data=False)

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

    ap = argparse.ArgumentParser(description='DS-TWR Live Localization from sniffer UART')
    ap.add_argument('--port', default='COM10', help='Sniffer COM port (default: COM10)')
    ap.add_argument('--baud', type=int, default=115200, help='Baud rate (default: 115200)')
    ap.add_argument('--nodes', type=int, default=N_NODES, help='Max node count (default: 30)')
    ap.add_argument('--arena', type=float, default=3.0, help='Arena size in meters')
    ap.add_argument('--no-gui', action='store_true', help='Text-only mode (no matplotlib)')
    ap.add_argument('--log', type=Path, default=None, help='Log file for UART text output')
    ap.add_argument('--bias', type=float, default=0.0, help='Antenna delay bias (meters)')
    args = ap.parse_args()

    print(f'DS-TWR Live Localization')
    print(f'  Port:  {args.port} @ {args.baud} baud')
    print(f'  Nodes: up to {args.nodes}')
    print(f'  Arena: {args.arena} m')
    print()

    # Open serial
    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.05,
                            rtscts=False, dsrdtr=False)
        print(f'  Connected to {args.port}')
    except serial.SerialException as e:
        print(f'ERROR: Cannot open {args.port}: {e}')
        sys.exit(1)

    parser = SnifferParser()
    pipeline = LivePipeline(n_nodes=args.nodes, arena_size=args.arena)
    pipeline.assembler.ant_bias = args.bias
    stop = threading.Event()

    log_file = None
    if args.log:
        log_file = open(args.log, 'a', encoding='utf-8')

    def uart_thread():
        """Background thread reading UART and feeding parser + pipeline."""
        while not stop.is_set():
            try:
                data = ser.read(ser.in_waiting or 1)
                if not data:
                    continue
                parser.feed(data)

                # Process any decoded reports
                while parser.reports:
                    report = parser.reports.popleft()
                    updated = pipeline.process_report(report)

                    if updated and args.no_gui:
                        # Print positions in headless mode
                        pos = pipeline.positions
                        valid = ~np.isnan(pos[:, 0])
                        parts = []
                        for i in range(args.nodes):
                            if valid[i]:
                                parts.append(f'{i+1}:({pos[i,0]:.2f},{pos[i,1]:.2f})')
                        if parts:
                            print(f'SF={pipeline.last_sf} pairs={pipeline.pair_count} | '
                                  + ' '.join(parts))

                # Drain text lines
                while parser.text_lines:
                    line = parser.text_lines.popleft()
                    if log_file:
                        log_file.write(f'{time.strftime("%H:%M:%S")} {line}\n')
                        log_file.flush()
                    if args.no_gui:
                        print(f'[UART] {line}')

            except serial.SerialException:
                print('Serial disconnected')
                stop.set()
                break
            except Exception as e:
                print(f'Error: {e}')

    # Start UART reader thread
    reader = threading.Thread(target=uart_thread, daemon=True)
    reader.start()

    if args.no_gui:
        # Headless — just run until Ctrl+C
        print('Running in headless mode. Press Ctrl+C to stop.')
        try:
            while not stop.is_set():
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
    else:
        # GUI mode — matplotlib in main thread
        print('Starting visualization...')
        run_gui(pipeline, parser, stop)

    # Cleanup
    stop.set()
    reader.join(timeout=2)
    ser.close()
    if log_file:
        log_file.close()

    print(f'\nSession summary:')
    print(f'  Frames decoded: {parser.stats["frames"]}')
    print(f'  CRC errors:     {parser.stats["crc_errors"]}')
    print(f'  Parse errors:   {parser.stats["parse_errors"]}')
    print(f'  Pipeline runs:  {pipeline.frame_count}')


if __name__ == '__main__':
    main()
