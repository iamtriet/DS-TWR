import asyncio
import hashlib
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from uart_monitor import parse_uwb
from ota_update import packet, Reassembler, decode_response, upload
from sign_image import image_info, SLOT, TRAILER


class HostTests(unittest.TestCase):
    def test_csv(self):
        self.assertEqual(parse_uwb("UWB,B001,1.25,20"), ("B001", 1.25, 20))
        for line in ["UWB,B001,nan,0", "UWB,0,1,0", "UWB,FFFF,1,0", "UWB,B001,-1,0",
                     "UWB,B001,1001,0", "UWB,B001,1,-1", "UWB,B001,inf,0", "noise", "UWB,BB,1,0,extra"]:
            self.assertIsNone(parse_uwb(line), line)

    def test_smp_fragmentation(self):
        raw = packet(3, 1, 1, 255, {"off": 256})
        for split in range(1, len(raw)):
            rx = Reassembler()
            self.assertIsNone(rx.feed(raw[:split]))
            self.assertEqual(rx.feed(raw[split:]), raw)
            self.assertFalse(rx.buf)
        self.assertEqual(decode_response(raw, 2, 1, 1, 255), {"off": 256})

    def test_smp_rejects_wrong_response(self):
        raw = packet(3, 1, 1, 5, {"off": 0})
        for args in [(2, 1, 1, 6), (0, 1, 1, 5), (2, 0, 1, 5), (2, 1, 0, 5)]:
            with self.assertRaises(ValueError):
                decode_response(raw, *args)
        with self.assertRaises(RuntimeError):
            decode_response(packet(3, 1, 1, 5, {"rc": 1}), 2, 1, 1, 5)
        with self.assertRaises(ValueError):
            Reassembler().feed(struct.pack(">BBHHBB", 3, 0, 600, 1, 0, 1))
        with self.assertRaises(ValueError):
            Reassembler().feed(raw+b"extra")
        with self.assertRaises(ValueError):
            packet(2, 1, 1, 0, {"data": bytes(600)})

    def test_upload_offsets_and_interrupt(self):
        class Board:
            def __init__(self, bad=False):
                self.offset = 0; self.bad = bad
            async def request(self, op, group, cmd, body):
                self_test.assertEqual((op, group, cmd), (2, 1, 1))
                self_test.assertEqual(body["off"], self.offset)
                self.offset += len(body["data"])
                return {"off": self.offset+(1000 if self.bad else 0)}
        self_test = self
        good = Board()
        self.assertTrue(asyncio.run(upload(good, bytes(700))))
        self.assertEqual(good.offset, 700)
        partial = Board()
        self.assertFalse(asyncio.run(upload(partial, bytes(700), 400)))
        self.assertEqual(partial.offset, 400)
        with self.assertRaises(ValueError):
            asyncio.run(upload(Board(True), bytes(700)))

    def test_upload_fits_negotiated_att_write(self):
        class Board:
            max_request_size = 249
            async def request(inner, op, group, cmd, body):
                self.assertLessEqual(len(packet(op, group, cmd, 0, body)), inner.max_request_size)
                self.assertEqual(len(body["data"]) % 4, 0)
                if not body["off"]:
                    self.assertGreaterEqual(len(body["data"]), 32)
                return {"off": body["off"]+len(body["data"])}
        self.assertTrue(asyncio.run(upload(Board(), bytes(1000))))
        tiny = Board()
        tiny.max_request_size = 20
        with self.assertRaises(ValueError):
            asyncio.run(upload(tiny, bytes(1000)))

    @staticmethod
    def image():
        data = bytearray(512+64)
        struct.pack_into("<IIHHIIBBHI", data, 0, 0x96F3B83D, 0, 512, 0, 64, 0, 1, 0, 1, 0)
        return data + struct.pack("<HHBBH", 0x6907, 40, 0x10, 0, 32) + hashlib.sha256(data).digest()

    def test_image_header_and_hash(self):
        data = self.image()
        self.assertEqual(image_info(data)["version"], "1.0.1+0")
        for offset in [0, 8, 12, 16, 512, len(data)-1]:
            bad = bytearray(data); bad[offset] ^= 1
            with self.assertRaises(ValueError):
                image_info(bad)
        for bad in [data[:20], data[:-1], data+b"extra", data+bytes(SLOT)]:
            with self.assertRaises(ValueError):
                image_info(bad)


if __name__ == "__main__":
    unittest.main()
