"""Fleet setup and measured range history; no inferred positions."""
import asyncio
from collections import deque
import re
import time
from control_backend import Session, radio_id
from uart_monitor import parse_uwb


def fleet_plan(boards, anchor_address):
    if not 2 <= len(boards) <= 9:
        raise ValueError("Select one anchor and 1–8 tags")
    if len({b["address"] for b in boards}) != len(boards) or len({b["name"] for b in boards}) != len(boards):
        raise ValueError("Duplicate board identity/address")
    anchor = next((b for b in boards if b["address"] == anchor_address), None)
    if not anchor:
        raise ValueError("Select an anchor from this fleet")
    tags = sorted((b for b in boards if b is not anchor), key=lambda b: b["name"])
    return {"anchor": {**anchor, "id": "A001"},
            "tags": [{**b, "id": f"B{i:03X}"} for i, b in enumerate(tags, 1)]}


def identity_commands(own, peer, current_own, current_peer):
    own, peer = radio_id(own), radio_id(peer)
    if own == peer: raise ValueError("Local and peer IDs must differ")
    commands = []
    if own == current_peer.upper():
        temporary = next(f"{i:04X}" for i in range(1, 65535)
                         if f"{i:04X}" not in {own, peer, current_own.upper()})
        commands.append(f"uwb peer {temporary}")
    return commands + [f"uwb id {own}", f"uwb peer {peer}"]


async def configure_node(session, own, peer, *, tags=None):
    await session.command("uwb stop")
    lines = await session.command("uwb status")
    match = next((re.search(r"UWB role=\w+ id=0x([\da-fA-F]+) peer=0x([\da-fA-F]+)", line)
                  for line in lines if "UWB role=" in line), None)
    if not match: raise RuntimeError("Cannot read existing radio identity; setup stopped")
    commands = ["uwb anchors clear"] if any("allowed_anchor_count=" in line for line in lines) else []
    commands += [f"uwb role {'anchor' if tags is not None else 'tag'}"]
    commands += ["led mode role"]
    commands += identity_commands(own, peer, match[1].zfill(4), match[2].zfill(4))
    commands += ["uwb init", "uwb output csv"]
    if tags is not None:
        commands += ["multitag clear"] + [f"multitag add {tag}" for tag in tags] + ["multitag start"]
    else:
        commands += ["uwb continuous"]
    for command in commands: await session.command(command)


class Fleet:
    def __init__(self, plan, emit, factory=Session):
        self.plan, self.emit, self.factory = plan, emit, factory
        self.anchor = None
        self.started = []
        self.monitor = None
        self.marker_applied = False

    def session(self, board):
        return self.factory(board["name"], self.emit, address=board["address"])

    async def start(self):
        try:
            # Probe/control connections are sequential; disconnected tags keep listening.
            for tag in self.plan["tags"]:
                session = self.session(tag)
                self.started.append(tag)
                try:
                    self.emit("fleet", f"Configuring {tag['name']} as tag {tag['id']}")
                    await session.open()
                    await configure_node(session, tag["id"], self.plan["anchor"]["id"])
                finally:
                    await session.close()
            self.anchor = self.session(self.plan["anchor"])
            await self.anchor.open()
            await configure_node(self.anchor, self.plan["anchor"]["id"], self.plan["tags"][0]["id"],
                                 tags=[t["id"] for t in self.plan["tags"]])
            await self.anchor.command("led mode role")
            self.monitor = asyncio.create_task(self.poll())
            self.emit("fleet", "Anchor running; waiting for measured ranges")
            return self.anchor
        except BaseException:
            await self.stop()
            raise

    async def poll(self):
        try:
            while True:
                await self.anchor.command("multitag stats")
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.emit("fleet", f"Statistics polling stopped: {exc}; use Stop fleet / restart")

    async def stop(self):
        if self.monitor:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
            self.monitor = None
        failed = []
        anchor = self.anchor
        self.anchor = None
        if anchor:
            try:
                if anchor.closed or anchor.desynchronized or not anchor.client.is_connected:
                    await anchor.close()
                    anchor = self.session(self.plan["anchor"])
                    await anchor.open()
                await anchor.command("uwb stop")
            except Exception as exc:
                failed.append(f"{anchor.label}: {exc}")
                self.anchor = anchor
            finally: await anchor.close()
        remaining = []
        for tag in self.started:
            session = self.session(tag)
            try:
                await session.open(); await session.command("uwb stop")
            except Exception as exc:
                remaining.append(tag); failed.append(f"{tag['name']}: {exc}")
            finally: await session.close()
        self.started = remaining
        if failed:
            self.emit("fleet", "Stop incomplete: " + "; ".join(failed))
        return failed


class RangeHistory:
    def __init__(self, plan, clock=time.monotonic):
        self.plan, self.clock = plan, clock
        self.rows = {tag["id"]: {"board": tag["name"], "samples": deque(maxlen=1200),
                               "last": None, "attempts": None, "success": None, "timeouts": None}
                     for tag in plan["tags"]}

    def feed(self, board, line, received=None):
        if board != self.plan["anchor"]["name"]: return
        now = self.clock() if received is None else received
        parsed = parse_uwb(line)
        if parsed and parsed[0] in self.rows:
            tag, distance, age = parsed
            row = self.rows[tag]
            row["last"] = now-age/1000
            row["samples"].append((now-age/1000, distance))
        match = re.search(r"TAG ([\da-fA-F]{4}) attempts=(\d+) success=(\d+) failed=\d+ timeout=(\d+)", line)
        if match and match[1].upper() in self.rows:
            row = self.rows[match[1].upper()]
            row.update(attempts=int(match[2]), success=int(match[3]), timeouts=int(match[4]))

    def state(self, tag):
        row = self.rows[tag]
        if row["last"] is None: return "WAITING"
        return "LIVE" if self.clock()-row["last"] < 3 else "STALE"

    def rate(self, tag):
        now = self.clock()
        samples = [t for t, _ in self.rows[tag]["samples"] if now-5 <= t <= now]
        return len(samples)/5.0
