import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from control_backend import Session, radio_id, tracking_commands


class ControlTests(unittest.IsolatedAsyncioTestCase):
    def test_ids_and_roster(self):
        self.assertEqual(radio_id("0xb001"), "B001")
        for bad in ("0000", "FFFF", "-1", "10000", "B001\nreset"):
            with self.assertRaises(ValueError): radio_id(bad)
        for peers in ("B001,B001", "A001", "", ",".join(f"B00{x}" for x in range(1, 10))):
            with self.assertRaises(ValueError): tracking_commands("Multi anchor", "A001", peers)
        commands = tracking_commands("Multi anchor", "A001", "B001,B002")
        self.assertEqual(commands[-3:], ["multitag add B001", "multitag add B002", "multitag start"])
        self.assertLess(commands.index("uwb init"), commands.index("multitag start"))

    def test_fragmented_telemetry(self):
        events = []
        session = Session("test", lambda *event: events.append(event))
        session.feed(b"UWB,B001,1.")
        self.assertEqual(events, [])
        session.feed(b"25,0\n@DONE 0\n")
        self.assertEqual([e[1][1] for e in events], ["UWB,B001,1.25,0", "@DONE 0"])

    async def test_wait_for_remote_completion_not_intermediate_pass(self):
        session = Session("test", lambda *args: None, address="test")
        session.client = AsyncMock()
        async def write(*args, **kwargs):
            session.feed(b"[uwb] PASS rc=0\n@DONE -13\n")
        session.client.write_gatt_char.side_effect = write
        with self.assertRaisesRegex(RuntimeError, "rc=-13"):
            await session.command("uwb init")

    async def test_setup_stops_on_error(self):
        session = Session("test", lambda *args: None)
        called = []
        async def command(text):
            called.append(text)
            if text == "uwb init": raise RuntimeError("Missing calibration")
        session.command = command
        with self.assertRaises(RuntimeError):
            await session.configure("Tag", "B001", "A001")
        self.assertNotIn("uwb continuous", called)
        self.assertEqual(called[-1], "uwb stop")

    async def test_no_flash_or_multiline_console_commands(self):
        session = Session("test", lambda *args: None)
        for cmd in ("ota confirm", "reset", "info\nreset", "x"*160):
            with self.assertRaises(ValueError): await session.command(cmd)

    async def test_single_peer_setup_clears_previous_anchor_allowlist(self):
        session = Session("test", lambda *args: None)
        called = []
        async def command(text):
            called.append(text)
            return ["UWB allowed_anchor_count=4 (zero uses peer)"] if text == "uwb status" else []
        session.command = command
        await session.configure("Tag", "B001", "A001")
        self.assertLess(called.index("uwb anchors clear"), called.index("uwb id B001"))

    async def test_cancelled_command_requires_reconnect(self):
        session = Session("test", lambda *args: None, address="test")
        session.client = AsyncMock()
        sent = asyncio.Event()
        async def write(*args, **kwargs): sent.set()
        session.client.write_gatt_char.side_effect = write
        task = asyncio.create_task(session.command("uwb status"))
        await sent.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertTrue(session.desynchronized)
        with self.assertRaisesRegex(RuntimeError, "Reconnect"):
            await session.command("uwb stop")

    async def test_cancel_during_write_requires_reconnect(self):
        session = Session("test", lambda *args: None, address="test")
        session.client = AsyncMock()
        writing = asyncio.Event()
        async def write(*args, **kwargs):
            writing.set()
            await asyncio.Event().wait()
        session.client.write_gatt_char.side_effect = write
        task = asyncio.create_task(session.command("uwb range B001"))
        await writing.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertTrue(session.desynchronized)
        with self.assertRaisesRegex(RuntimeError, "Reconnect"):
            await session.command("uwb stop")


if __name__ == "__main__": unittest.main()
