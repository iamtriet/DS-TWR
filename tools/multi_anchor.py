"""Host scheduled DS-TWR: four anchors, up to eight tags, one transmitter at a time."""
import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from control_backend import Session
from tracking import identity_commands
from uart_monitor import parse_uwb

# Firmware uses Zephyr/newlib errno values, not the host OS's errno table.
RETRYABLE_RANGE_CODES = frozenset((-116, -77, -122, -34))


def make_plan(boards, anchor_names):
    if len(anchor_names) != 4 or len(set(anchor_names)) != 4:
        raise ValueError("Choose four different anchors")
    if not 5 <= len(boards) <= 12:
        raise ValueError("Select four anchors and 1–8 tags")
    if len({b['name'] for b in boards}) != len(boards) or len({b['address'] for b in boards}) != len(boards):
        raise ValueError("Duplicate board identity")
    by_name = {b['name']: b for b in boards}
    if not set(anchor_names) <= by_name.keys():
        raise ValueError("Anchors must be in the selected boards")
    return dict(anchors=[dict(by_name[name], id=f'A{i:03X}') for i, name in enumerate(anchor_names, 1)],
                tags=[dict(b, id=f'B{i:03X}') for i, b in enumerate(sorted(
                    (b for b in boards if b['name'] not in anchor_names), key=lambda b: b['name']), 1)])


class MultiAnchorFleet:
    def __init__(self, plan, emit, factory=Session):
        self.plan, self.emit, self.factory = plan, emit, factory
        self.started = []
        self.monitor = None
        self.active = None
        self.stopping = False
        self.connections = {}

    def session(self, board):
        return self.factory(board['name'], self.emit, address=board['address'])

    async def configure(self, board, is_tag):
        session = self.session(board)
        if board not in self.started: self.started.append(board)
        try:
            await session.open()
            await session.command('uwb stop')
            # Also acts as a firmware capability check (requires 1.0.9).
            await session.command('uwb anchors clear')
            lines = await session.command('uwb status')
            match = next((m for line in lines if (m := re.search(
                r'UWB role=\w+ id=0x([\da-fA-F]+) peer=0x([\da-fA-F]+)', line))), None)
            if not match: raise RuntimeError('Cannot read radio identity')
            peer = self.plan['anchors'][0]['id'] if is_tag else self.plan['tags'][0]['id']
            commands = [f"uwb role {'tag' if is_tag else 'anchor'}"]
            commands += ['led mode role']
            commands += identity_commands(board['id'], peer, match[1].zfill(4), match[2].zfill(4))
            commands += ['uwb init', 'uwb output csv']
            if is_tag:
                commands += ['uwb anchors ' + ' '.join(b['id'] for b in self.plan['anchors']), 'uwb continuous']
            for command in commands: await session.command(command)
            self.connections[board['name']] = session
            try:
                await session.command('imu init')
                await session.command('imu stream')
            except RuntimeError as exc:
                if session.desynchronized: raise
                self.emit('fleet', f"{board['name']}: IMU unavailable ({exc}); raw UWB remains available")
        finally:
            if board['name'] not in self.connections: await session.close()

    async def start(self, background=True):
        try:
            # Stop/configure all potential transmitters before any tag starts.
            for board in self.plan['anchors']: await self.configure(board, False)
            for board in self.plan['tags']: await self.configure(board, True)
            if background: self.monitor = asyncio.create_task(self.poll())
        except BaseException:
            await self.stop()
            raise

    async def cycle(self, samples=1):
        for name, connection in self.connections.items():
            if not connection.client.is_connected:
                raise RuntimeError(f'Persistent BLE link lost: {name}; stop and restart the fleet')
        for anchor in self.plan['anchors']:
            if self.stopping: return
            session = self.connections[anchor['name']]
            self.active = session
            try:
                for _ in range(samples):
                    for tag in self.plan['tags']:
                        if self.stopping: return
                        for attempt in range(2):
                            if self.stopping: return
                            try:
                                started = time.monotonic()
                                lines = await session.command(f"uwb range {tag['id']}")
                            except RuntimeError as exc:
                                match = re.search(r'board returned rc=(-?\d+);', str(exc))
                                if not match or int(match[1]) not in RETRYABLE_RANGE_CODES: raise
                                self.emit('multi_sample', (anchor['id'], tag['id'], None, int(match[1])))
                                if attempt == 0: await asyncio.sleep(.025)
                            else:
                                values = [p for line in lines if (p := parse_uwb(line)) and p[0] == tag['id']]
                                if len(values) != 1: raise RuntimeError('Range acknowledgement missing measured sample')
                                self.emit('multi_sample', (anchor['id'], tag['id'], values[0][1], 0))
                                self.emit('range_timing', 1000*(time.monotonic()-started))
                                break
            finally:
                self.active = None

    async def poll(self):
        try:
            while not self.stopping: await self.cycle()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.emit('multi_failed', str(exc))

    async def stop(self):
        self.stopping = True
        if self.monitor:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
            self.monitor = None
        failed, remaining = [], []
        for board in self.started:
            session = self.connections.pop(board['name'], None)
            try:
                if session is None or session.closed or session.desynchronized or not session.client.is_connected:
                    if session is not None: await session.close()
                    session = self.session(board)
                    await session.open()
                await session.command('uwb stop')
                await session.command('imu stop')
            except Exception as exc:
                failed.append(f"{board['name']}: {exc}"); remaining.append(board)
            finally:
                if session is not None: await session.close()
        self.started = remaining
        return failed


async def test(plan, output, cycles=20):
    from tracking_estimation import Motion
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    rows, timings, sample_times = [], [], []
    motion = {b['name']: Motion() for b in plan['anchors']+plan['tags']}
    motion_counts = {name:0 for name in motion}
    with (output / 'console.log').open('w', encoding='utf-8') as log:
        def emit(kind, value):
            log.write(json.dumps([kind, value]) + '\n'); log.flush()
            if kind == 'multi_sample':
                rows.append(value)
                sample_times.append([time.monotonic(), *value])
                print(value, flush=True)
            if kind == 'range_timing': timings.append(value)
            if kind == 'line' and value[0] in motion:
                if motion[value[0]].feed(value[1], value[2]): motion_counts[value[0]] += 1
        fleet = MultiAnchorFleet(plan, emit)
        started = time.monotonic()
        result = dict(plan=plan, samples=rows, passed=False, started_utc=datetime.now(timezone.utc).isoformat(), cycles=[])
        try:
            await fleet.start(background=False)
            for _ in range(cycles):
                offset = len(rows)
                await fleet.cycle()
                result['cycles'].append({f"{a['id']}/{t['id']}": sum(
                    r[0]==a['id'] and r[1]==t['id'] and r[3]==0 for r in rows[offset:])
                    for a in plan['anchors'] for t in plan['tags']})
            result['links'] = {f"{a['id']}/{t['id']}": dict(
                success=sum(r[0]==a['id'] and r[1]==t['id'] and r[3]==0 for r in rows),
                failed=sum(r[0]==a['id'] and r[1]==t['id'] and r[3]!=0 for r in rows))
                for a in plan['anchors'] for t in plan['tags']}
            result['passed'] = all(n >= 1 for cycle in result['cycles'] for n in cycle.values())
            result['imu'] = {name:dict(reports=motion_counts[name],state=m.state(time.monotonic()),
                                     accuracy=m.quality,linear_acceleration=m.acc,angular_speed=m.gyro)
                             for name,m in motion.items()}
            result['imu_status'] = {name:await session.command('imu status') for name,session in fleet.connections.items()}
        except Exception as exc:
            result['error'] = str(exc)
        finally:
            result['stop_errors'] = await fleet.stop()
            result['passed'] = result['passed'] and not result['stop_errors']
            result['elapsed_seconds'] = round(time.monotonic()-started, 2)
            result['command_roundtrip_ms'] = timings
            result['sample_times'] = sample_times
            (output / 'result.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    asyncio.run(test(json.loads(Path(args.plan).read_text()), args.output))
