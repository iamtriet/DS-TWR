import sys
from pathlib import Path
import unittest
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from multi_anchor import make_plan, MultiAnchorFleet

BOARDS = [dict(name=f'PCB_{i:08X}', address=str(i)) for i in range(6)]


class MultiAnchorTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduling_and_retryable_error(self):
        calls, events, connected = [], [], set()
        class Fake:
            def __init__(self, label, emit, address):
                self.label = label; self.peer = None
                self.closed = self.desynchronized = False
                self.client = SimpleNamespace(is_connected=True)
            async def open(self):
                connected.add(self.label)
            async def close(self): connected.discard(self.label)
            async def command(self, cmd):
                calls.append((self.label, cmd))
                if cmd == 'uwb status': return ['UWB role=tag id=0xb001 peer=0xa001']
                if cmd.startswith('uwb peer '): self.peer = cmd.split()[-1]
                if cmd.startswith('uwb range '):
                    self.peer = cmd.split()[-1]
                    if self.peer == 'B002': raise RuntimeError('uwb range: board returned rc=-116; timeout')
                    return [f'UWB,{self.peer},1.25,0']
                return []
        plan = make_plan(BOARDS, [b['name'] for b in BOARDS[:4]])
        fleet = MultiAnchorFleet(plan, lambda k, v: events.append((k, v)), Fake)
        await fleet.start(background=False)
        self.assertEqual(len(connected), 6)
        self.assertEqual(sum(cmd == 'led mode role' for _, cmd in calls), 6)
        self.assertEqual(sum(cmd == 'uwb continuous' for _, cmd in calls), 2)
        self.assertEqual(sum(cmd == 'uwb anchors A001 A002 A003 A004' for _, cmd in calls), 2)
        self.assertFalse(any(cmd == 'multitag start' for _, cmd in calls))
        await fleet.cycle(samples=2)
        self.assertEqual(sum(k == 'multi_sample' for k, _ in events), 24)
        for anchor in ('A001', 'A002', 'A003', 'A004'):
            self.assertEqual(sum(v == (anchor, 'B001', 1.25, 0) for _, v in events), 2)
            self.assertEqual(sum(v == (anchor, 'B002', None, -116) for _, v in events), 4)
        self.assertEqual(await fleet.stop(), [])
        self.assertFalse(connected)

    async def test_invalid_plans(self):
        for boards, anchors in ((BOARDS[:4], [b['name'] for b in BOARDS[:4]]),
                                (BOARDS, ['missing', 'a', 'b', 'c']),
                                (BOARDS, [BOARDS[0]['name']]*4),
                                (BOARDS+[BOARDS[0]], [b['name'] for b in BOARDS[:4]])):
            with self.assertRaises(ValueError): make_plan(boards, anchors)

    async def test_failed_setup_stops_configured_nodes(self):
        stopped = []
        class Fake:
            def __init__(self, label, emit, address):
                self.label = label; self.closed = self.desynchronized = False
                self.client = SimpleNamespace(is_connected=True)
            async def open(self): pass
            async def close(self): pass
            async def command(self, cmd):
                if cmd == 'uwb stop': stopped.append(self.label)
                if cmd == 'uwb status': return ['UWB role=tag id=0x0000 peer=0x0000']
                if self.label == BOARDS[2]['name'] and cmd == 'uwb init': raise RuntimeError('radio failed')
                return []
        fleet = MultiAnchorFleet(make_plan(BOARDS, [b['name'] for b in BOARDS[:4]]), lambda *a: None, Fake)
        with self.assertRaisesRegex(RuntimeError, 'radio failed'): await fleet.start(False)
        self.assertEqual(stopped[-3:], [b['name'] for b in BOARDS[:3]])
        self.assertEqual(fleet.started, [])
