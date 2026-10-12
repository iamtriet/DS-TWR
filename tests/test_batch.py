import asyncio
import copy
from pathlib import Path
import sys
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ota_batch import update_board, validate_roster, FleetBoard, run_jobs

OLD = bytes([1])*32
NEW = bytes([2])*32


class Board:
    def __init__(self, current=OLD, confirmed=True, reject=False):
        self.images = [{"hash": current, "active": True, "confirmed": confirmed}]
        self.calls = []
        self.reject = reject

    async def state(self):
        return {"images": copy.deepcopy(self.images)}

    async def transfer(self, data):
        self.calls.append("upload")
        self.images.append({"hash": NEW, "active": False, "confirmed": False})

    async def send(self, cmd, body):
        self.calls.append("confirm" if body["confirm"] else "test")
        for image in self.images:
            if image["hash"] == body["hash"]:
                if body["confirm"]:
                    image["confirmed"] = True
                else:
                    image["pending"] = True

    async def reset(self):
        self.calls.append("reset")
        for image in self.images:
            if image.get("pending"):
                if self.reject:
                    image["pending"] = False
                    return
                for other in self.images:
                    other["active"] = False
                image["active"] = True
                image["pending"] = False

    async def wait_active(self, digest, confirmed=False):
        active = next(i for i in self.images if i["active"])
        if active["hash"] != digest or (confirmed and not active["confirmed"]):
            raise TimeoutError("image did not boot/confirm")

    async def healthy_trial(self, digest):
        self.calls.append("health")
        await self.wait_active(digest)


class BatchTests(unittest.TestCase):
    def test_complete_sequence(self):
        b = Board()
        self.assertEqual(asyncio.run(update_board(b, b"image", NEW)), "UPDATED")
        self.assertEqual(b.calls, ["upload", "test", "reset", "health", "confirm", "reset"])

    def test_already_current_no_write(self):
        b = Board(NEW)
        self.assertEqual(asyncio.run(update_board(b, b"image", NEW)), "ALREADY_CURRENT")
        self.assertEqual(b.calls, [])

    def test_resume_trial(self):
        b = Board(NEW, confirmed=False)
        asyncio.run(update_board(b, b"image", NEW))
        self.assertEqual(b.calls, ["health", "confirm", "reset"])

    def test_rejected_image_never_confirmed(self):
        b = Board(reject=True)
        with self.assertRaises(TimeoutError):
            asyncio.run(update_board(b, b"image", NEW))
        self.assertNotIn("confirm", b.calls)

    def test_unrelated_trial_never_confirmed(self):
        b = Board(confirmed=False)
        with self.assertRaises(ValueError):
            asyncio.run(update_board(b, b"image", NEW))
        self.assertFalse(b.calls)

    def test_unrelated_pending_refused(self):
        b = Board()
        b.images.append({"hash": bytes(32), "pending": True})
        with self.assertRaises(ValueError):
            asyncio.run(update_board(b, b"image", NEW))
        self.assertFalse(b.calls)

    def test_enrollment_rejects_missing_duplicate(self):
        board = {"name": "PCB_76A10829", "address": "D1:3E:A9:6B:F8:8A"}
        self.assertEqual(validate_roster({"expected_count": 1, "boards": [board]}), [board])
        for data in [{"expected_count": 30, "boards": [board]}, {"expected_count": 0, "boards": []},
                     {"expected_count": 2, "boards": [board, board]}]:
            with self.assertRaises(ValueError):
                validate_roster(data)


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_connection_until_reset(self):
        opened, closed = [], []
        class Session:
            client = SimpleNamespace(is_connected=True)
            async def request(self, *args):
                return {"images": [{"hash": NEW, "active": True, "confirmed": True}]}
        @asynccontextmanager
        async def connect(board, device):
            opened.append(board)
            try:
                yield Session()
            finally:
                closed.append(board)
        with patch("ota_batch.connection", connect):
            async with FleetBoard({"name": "PCB_12345678"}) as b:
                await b.state(); await b.state(); await b.send(0, {})
                self.assertEqual(len(opened), 1)
                await b.reset()
                self.assertEqual(len(closed), 1)
                await b.state(); await b.send(0, {})
                await b.reset(); await b.state()
                self.assertEqual(len(opened), 3)
            self.assertEqual(len(closed), 3)

    async def test_failed_request_closes_session(self):
        closed = []
        class Session:
            client = SimpleNamespace(is_connected=True)
            async def request(self, *args):
                raise TimeoutError("lost response")
        @asynccontextmanager
        async def connect(board, device):
            try:
                yield Session()
            finally:
                closed.append(True)
        with patch("ota_batch.connection", connect):
            async with FleetBoard({}) as b:
                with self.assertRaises(TimeoutError):
                    await b.state()
            self.assertEqual(closed, [True])

    async def test_parallelism_is_bounded(self):
        active, peak = 0, 0
        async def worker(index, board):
            nonlocal active, peak
            active += 1; peak = max(peak, active)
            try:
                await asyncio.sleep(0.005)
                return board
            finally:
                active -= 1
        self.assertEqual(await run_jobs(list(range(6)), worker, 2), list(range(6)))
        self.assertEqual(peak, 2)
        self.assertEqual(active, 0)
        peak = 0
        await run_jobs(list(range(3)), worker, 1)
        self.assertEqual(peak, 1)

    async def test_error_cleans_up_siblings(self):
        cleanup = []
        async def worker(index, board):
            try:
                await asyncio.sleep(0)
                if index == 1:
                    raise RuntimeError("test failure")
                await asyncio.sleep(30)
            finally:
                cleanup.append(index)
        with self.assertRaises(RuntimeError):
            await run_jobs([1, 2], worker, 2)
        self.assertEqual(sorted(cleanup), [1, 2])


if __name__ == "__main__":
    unittest.main()
