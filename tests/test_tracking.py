from pathlib import Path
import sys
import unittest
import asyncio
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from tracking import fleet_plan, identity_commands, RangeHistory, Fleet

BOARDS = [{"name": f"PCB_{i:08X}", "address": f"address{i}"} for i in range(5)]


class TrackingTests(unittest.TestCase):
    def test_deterministic_unique_fleet(self):
        plan = fleet_plan(list(reversed(BOARDS)), "address0")
        self.assertEqual(plan["anchor"]["id"], "A001")
        self.assertEqual([b["id"] for b in plan["tags"]], ["B001", "B002", "B003", "B004"])
        self.assertEqual(plan, fleet_plan(BOARDS, "address0"))
        for boards, anchor in ((BOARDS[:1], "address0"), (BOARDS, "absent"), (BOARDS+[BOARDS[1]], "address0")):
            with self.assertRaises(ValueError): fleet_plan(boards, anchor)

    def test_id_role_swap_does_not_hit_current_peer(self):
        cmds = identity_commands("A001", "B001", "B001", "A001")
        own, peer = "B001", "A001"
        for command in cmds:
            _, kind, value = command.split()
            if kind == "id":
                self.assertNotEqual(value, peer); own = value
            else:
                self.assertNotEqual(value, own); peer = value
        self.assertEqual((own, peer), ("A001", "B001"))

    def test_history_waiting_stale_and_wrong_source(self):
        now = [100.0]
        model = RangeHistory(fleet_plan(BOARDS, "address0"), lambda: now[0])
        self.assertEqual(model.state("B001"), "WAITING")
        model.feed("wrong-anchor", "UWB,B001,1.25,0")
        self.assertIsNone(model.rows["B001"]["last"])
        model.feed("PCB_00000000", "UWB,B001,1.25,500")
        self.assertEqual(model.state("B001"), "LIVE")
        model.feed("PCB_00000000", "TAG b001 attempts=12 success=10 failed=2 timeout=2 packet_error=0")
        self.assertEqual(model.rows["B001"]["success"], 10)
        now[0] += 3
        self.assertEqual(model.state("B001"), "STALE")
        now[0] += 5
        self.assertEqual(model.rate("B001"), 0)


class FleetTests(unittest.IsolatedAsyncioTestCase):
    async def test_sequential_tags_then_anchor_and_stop(self):
        calls = []; active = set(); maximum = [0]
        class Fake:
            def __init__(self, label, emit, address):
                self.label = label; self.closed = False; self.desynchronized = False
                self.client = SimpleNamespace(is_connected=False)
            async def open(self):
                calls.append((self.label, "open")); active.add(self.label)
                maximum[0] = max(maximum[0], len(active)); self.client.is_connected = True
            async def command(self, text):
                calls.append((self.label, text))
                return ["UWB role=tag id=0x0000 peer=0x0000"]
            async def close(self):
                calls.append((self.label, "close")); active.discard(self.label)
                self.closed = True; self.client.is_connected = False
        fleet = Fleet(fleet_plan(BOARDS, "address0"), lambda *a: None, Fake)
        await fleet.start()
        self.assertEqual(maximum[0], 1)
        self.assertEqual(active, {"PCB_00000000"})
        self.assertIn(("PCB_00000000", "multitag add B004"), calls)
        self.assertLess(calls.index(("PCB_00000004", "close")), calls.index(("PCB_00000000", "open")))
        self.assertEqual(await fleet.stop(), [])
        self.assertFalse(active)
        for board in BOARDS:
            self.assertIn((board["name"], "uwb stop"), calls)

    async def test_partial_start_cleans_up_tags(self):
        calls = []
        class Fake:
            def __init__(self, label, emit, address): self.label = label
            async def open(self): pass
            async def command(self, text):
                calls.append((self.label, text))
                if self.label == "PCB_00000002" and text == "uwb init": raise RuntimeError("calibration failure")
                return ["UWB role=tag id=0x0000 peer=0x0000"]
            async def close(self): pass
        fleet = Fleet(fleet_plan(BOARDS, "address0"), lambda *a: None, Fake)
        with self.assertRaisesRegex(RuntimeError, "calibration failure"): await fleet.start()
        self.assertEqual(calls[-1], ("PCB_00000002", "uwb stop"))
        self.assertNotIn(("PCB_00000000", "multitag start"), calls)
        self.assertEqual(fleet.started, [])


if __name__ == "__main__": unittest.main()
