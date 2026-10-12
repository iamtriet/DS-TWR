"""BLE SMP client: upload, inspect, trial, confirm, or reset one explicit board."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import struct
import sys
import cbor2
from sign_image import image_info

SERVICE = "8d53dc1d-1db7-4cd3-868b-8a527460aa84"
CHARACTERISTIC = "da2e7828-fbce-4e01-ae9e-261174997c48"
MAX_PACKET = 512


def packet(op, group, command, seq, body):
    data = cbor2.dumps(body)
    if len(data)+8 > MAX_PACKET:
        raise ValueError("SMP request exceeds firmware buffer")
    return struct.pack(">BBHHBB", op, 0, len(data), group, seq, command) + data


class Reassembler:
    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf.extend(data)
        if len(self.buf) < 8:
            return None
        count = struct.unpack_from(">H", self.buf, 2)[0] + 8
        if count > MAX_PACKET or len(self.buf) > count:
            self.buf.clear()
            raise ValueError("Invalid SMP response length")
        if len(self.buf) == count:
            value = bytes(self.buf)
            self.buf.clear()
            return value
        return None


def decode_response(raw, op, group, command, seq):
    if len(raw) < 8:
        raise ValueError("Short SMP header")
    actual = struct.unpack_from(">BBHHBB", raw)
    if actual != (op+1, 0, len(raw)-8, group, seq, command):
        raise ValueError(f"Unexpected SMP response header {actual}")
    import io
    stream = io.BytesIO(raw[8:])
    body = cbor2.CBORDecoder(stream).decode()
    if stream.read() or not isinstance(body, dict):
        raise ValueError("Invalid SMP CBOR response")
    if body.get("rc", 0) or body.get("err"):
        raise RuntimeError(f"Board rejected request: {body}")
    return body


class SMP:
    def __init__(self, client):
        self.client = client
        self.seq = 0
        self.rx = Reassembler()
        self.responses = asyncio.Queue()

    @property
    def max_request_size(self):
        characteristic = self.client.services.get_characteristic(CHARACTERISTIC)
        return min(MAX_PACKET, characteristic.max_write_without_response_size)

    def notify(self, _sender, data):
        try:
            response = self.rx.feed(data)
            if response is not None:
                self.responses.put_nowait(response)
        except ValueError as e:
            self.responses.put_nowait(e)

    async def request(self, op, group, command, body):
        seq = self.seq
        self.seq = (self.seq+1) & 255
        raw = packet(op, group, command, seq, body)
        characteristic = self.client.services.get_characteristic(CHARACTERISTIC)
        # Zephyr 2.7's SMP BT receiver does not reassemble request fragments.
        # Fit a complete SMP packet in one negotiated ATT write.
        if len(raw) > self.max_request_size:
            raise ValueError("SMP request exceeds negotiated BLE write size; larger ATT MTU required")
        await self.client.write_gatt_char(characteristic, raw, response=False)
        response = await asyncio.wait_for(self.responses.get(), 30)
        if isinstance(response, Exception):
            raise response
        return decode_response(response, op, group, command, seq)


async def upload(smp, data, stop_after=0, progress=None):
    if stop_after and stop_after % 4:
        raise ValueError("--stop-after must be a multiple of the 4-byte flash write alignment")
    offset = 0
    digest = hashlib.sha256(data).digest()
    while offset < len(data):
        end = min(offset+256, len(data))
        if stop_after:
            end = min(end, stop_after)
        body = {"off": offset, "data": data[offset:end]}
        if not offset:
            body.update(len=len(data), sha=digest)
        limit = getattr(smp, "max_request_size", MAX_PACKET)
        while len(packet(2, 1, 1, 0, body)) > limit and end > offset:
            end -= 1
            body["data"] = data[offset:end]
        # Legacy image management rounds intermediate writes down to flash alignment.
        if end < len(data):
            end -= (end-offset) % 4
            body["data"] = data[offset:end]
        if end <= offset or (offset == 0 and end < 32):
            raise ValueError("Negotiated BLE MTU cannot fit the initial image header/upload metadata")
        response = await smp.request(2, 1, 1, body)
        new_offset = response.get("off")
        if type(new_offset) is not int or not 0 < new_offset <= len(data) or (new_offset != end and (offset != 0 or stop_after)):
            raise ValueError(f"Unexpected upload offset {new_offset}; expected {end}; restart upload")
        offset = new_offset
        if progress is None:
            print(f"Upload {offset}/{len(data)} ({offset*100//len(data)}%)", end="\r", flush=True)
        else:
            progress(offset, len(data))
        if stop_after and offset >= stop_after:
            print("\nINTENTIONALLY INTERRUPTED: no trial requested. Restart upload from offset zero.")
            return False
    if progress is None:
        print("\nUpload complete. No trial or confirmation requested.")
    return True


async def run(a):
    from bleak import BleakClient, BleakScanner
    if a.action == "scan":
        found = await BleakScanner.discover(timeout=8, return_adv=True)
        for device, adv in found.values():
            if SERVICE in adv.service_uuids or (adv.local_name or "").startswith("PCB_"):
                print(device.address, adv.local_name or device.name)
        return
    if not a.address:
        raise ValueError("Use scan, then select exactly one --address")
    data = None
    if a.action == "upload":
        if not a.image:
            raise ValueError("upload requires --image")
        data = a.image.read_bytes()
        info = image_info(data)  # Integrity and bounds; signature is verified by MCUboot.
        print(json.dumps(info, indent=2))
        if a.stop_after and not 0 < a.stop_after < len(data):
            raise ValueError("--stop-after must be within image data")
    async with BleakClient(a.address, timeout=20) as client:
        smp = SMP(client)
        await client.start_notify(CHARACTERISTIC, smp.notify)
        # Allow any pre-connection radio operation to finish before first erase.
        await asyncio.sleep(1)
        if a.action == "upload":
            complete = await upload(smp, data, a.stop_after)
            if not complete:
                return 2
        elif a.action == "state":
            result = await smp.request(0, 1, 0, {})
            print(json.dumps(result, indent=2, default=lambda b: b.hex()))
        elif a.action in ("test", "confirm"):
            if not a.hash:
                raise ValueError("test/confirm requires explicit --hash from signed image manifest/state")
            digest = bytes.fromhex(a.hash)
            if len(digest) != 32:
                raise ValueError("Expected 32-byte image hash")
            state = await smp.request(0, 1, 0, {})
            images = [i for i in state.get("images", []) if i.get("hash") == digest]
            if len(images) != 1:
                raise ValueError("Hash does not identify exactly one installed image")
            if bool(images[0].get("active")) != (a.action == "confirm"):
                raise ValueError("Only test an inactive image; only confirm the active trial image")
            result = await smp.request(2, 1, 0, {"hash": digest, "confirm": a.action == "confirm"})
            print(json.dumps(result, indent=2, default=lambda b: b.hex()))
        elif a.action == "reset":
            print(await smp.request(2, 0, 5, {}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["scan", "state", "upload", "test", "confirm", "reset"])
    p.add_argument("--address")
    p.add_argument("--image", type=Path)
    p.add_argument("--hash")
    p.add_argument("--stop-after", type=int, default=0, help="Deliberate interrupted-upload test")
    a = p.parse_args()
    try:
        sys.exit(asyncio.run(run(a)) or 0)
    except (ValueError, RuntimeError, OSError, asyncio.TimeoutError) as e:
        p.exit(1, f"OTA failed: {e}\n")


if __name__ == "__main__":
    main()
