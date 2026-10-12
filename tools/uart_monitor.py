"""Timestamp UART logs and validated UWB CSV; optionally send CLI commands."""
import argparse
import csv
from datetime import datetime, timezone
import math
from pathlib import Path
import queue
import threading
import time


def parse_uwb(line):
    parts = line.strip().split(",")
    if len(parts) != 4 or parts[0] != "UWB":
        return None
    try:
        tag = int(parts[1], 16)
        distance = float(parts[2])
        age = int(parts[3])
        if not 0 < tag < 0xffff or not math.isfinite(distance) or not 0 <= distance <= 1000 or not 0 <= age <= 0xffffffff:
            return None
        return f"{tag:04X}", distance, age
    except ValueError:
        return None


def main():
    import serial
    from serial.tools import list_ports
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port")
    p.add_argument("--list", action="store_true")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--log", type=Path, default=Path("uart.log"))
    p.add_argument("--csv", type=Path, default=Path("ranges.csv"))
    p.add_argument("--command", action="append", default=[])
    p.add_argument("--interactive", action="store_true")
    p.add_argument("--seconds", type=float, default=0, help="0 means until Ctrl+C")
    a = p.parse_args()
    if a.list:
        for port in list_ports.comports():
            print(port.device, port.description)
        return
    if not a.port:
        p.error("Select an explicit --port; no board is selected automatically")
    commands = queue.Queue()
    for cmd in a.command:
        commands.put(cmd)
    def read_input():
        try:
            while True:
                commands.put(input())
        except EOFError:
            return
    if a.interactive:
        threading.Thread(target=read_input, daemon=True).start()
    a.log.parent.mkdir(parents=True, exist_ok=True)
    a.csv.parent.mkdir(parents=True, exist_ok=True)
    fresh = not a.csv.exists() or a.csv.stat().st_size == 0
    with serial.Serial(a.port, a.baud, timeout=0.1, write_timeout=2, rtscts=False, dsrdtr=False) as port, a.log.open("a", encoding="utf-8") as log, a.csv.open("a", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        if fresh:
            writer.writerow(["host_utc", "tag_hex", "distance_m", "age_ms"])
        start = time.monotonic()
        next_command = start
        buf = bytearray()
        discard = False
        try:
            while not a.seconds or time.monotonic()-start < a.seconds:
                if time.monotonic() >= next_command:
                    try:
                        cmd = commands.get_nowait()
                    except queue.Empty:
                        cmd = None
                    if cmd is not None:
                        if not cmd.isascii() or len(cmd) >= 160 or "\n" in cmd or "\r" in cmd:
                            print("Rejected command: maximum 159 ASCII bytes, single line")
                        else:
                            port.write((cmd + "\n").encode("ascii", errors="strict"))
                            print(">", cmd)
                        # Long init/diagnostic calls can take seconds; avoid RX overflow.
                        next_command = time.monotonic()+5
                for value in port.read(512):
                    if value == 10:
                        if not discard:
                            line = buf.decode("utf-8", errors="replace").rstrip("\r")
                            stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                            text = f"{stamp} {line}"
                            print(text)
                            log.write(text + "\n"); log.flush()
                            row = parse_uwb(line)
                            if row:
                                writer.writerow((stamp, *row)); csvfile.flush()
                        buf.clear(); discard = False
                    elif not discard:
                        buf.append(value)
                        if len(buf) > 4096:
                            print("Dropped oversized unterminated UART line")
                            buf.clear(); discard = True
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
