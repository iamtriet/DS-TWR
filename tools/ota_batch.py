"""Enroll a fixed fleet and update with reused BLE sessions and bounded concurrency."""
import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from ota_update import SMP, SERVICE, CHARACTERISTIC, upload
from sign_image import ROOT, image_info, imgtool


def active_image(state):
    images = [i for i in state.get("images", []) if i.get("active")]
    if len(images) != 1:
        raise ValueError("Expected exactly one active image")
    return images[0]


def validate_roster(data):
    boards = data.get("boards", [])
    if not boards or data.get("expected_count") != len(boards):
        raise ValueError("Roster count does not match expected_count")
    names, addresses = set(), set()
    for b in boards:
        if not re.fullmatch(r"PCB_[0-9A-F]{8}", b.get("name", "")) or not b.get("address"):
            raise ValueError("Invalid board name/address in roster")
        if b["name"] in names or b["address"].upper() in addresses:
            raise ValueError("Duplicate board identity/address")
        names.add(b["name"]); addresses.add(b["address"].upper())
    return boards


@asynccontextmanager
async def connection(board, device=None):
    from bleak import BleakClient
    # Rediscover GATT after a firmware reboot instead of using Windows' cache.
    async with BleakClient(device or board["address"], timeout=15,
                           winrt={"use_cached_services": False}) as client:
        name = bytes(await client.read_gatt_char("00002a00-0000-1000-8000-00805f9b34fb")).decode()
        if name != board["name"]:
            raise ValueError(f"Identity mismatch: expected {board['name']}, received {name}")
        smp = SMP(client)
        await client.start_notify(CHARACTERISTIC, smp.notify)
        await asyncio.sleep(1)
        yield smp


class FleetBoard:
    def __init__(self, board, device=None):
        self.board = board
        self.device = device
        self._context = None
        self._smp = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        context = self._context
        self._context = self._smp = None
        if context is not None:
            await context.__aexit__(None, None, None)

    async def session(self):
        if self._smp is not None and self._smp.client.is_connected:
            return self._smp
        await self.close()
        context = connection(self.board, self.device)
        smp = await context.__aenter__()
        self._context, self._smp = context, smp
        return smp

    async def request(self, op, group, command, body):
        try:
            smp = await self.session()
            return await smp.request(op, group, command, body)
        except BaseException:
            await self.close()
            raise

    async def state(self):
        return await self.request(0, 1, 0, {})

    async def send(self, command, body):
        return await self.request(2, 1, command, body)

    async def transfer(self, data):
        smp = await self.session()
        last_bucket = -1
        def progress(offset, total):
            nonlocal last_bucket
            bucket = offset*10//total
            if bucket != last_bucket:
                last_bucket = bucket
                print(f"{self.board['name']}: upload {offset*100//total}%", flush=True)
        await upload(smp, data, progress=progress)

    async def reset(self):
        # Close only at a reboot boundary. Never mistake a missing reset ACK
        # for success; the outer retry resumes from observed image state.
        try:
            await self.request(2, 0, 5, {})
        finally:
            await self.close()

    async def wait_active(self, digest, confirmed=False):
        deadline = time.monotonic()+100
        last = "board not yet reachable"
        await asyncio.sleep(5)
        while time.monotonic() < deadline:
            try:
                current = active_image(await self.state())
                if current.get("hash") == digest and (not confirmed or current.get("confirmed")):
                    return current
                last = f"active version={current.get('version')}, confirmed={current.get('confirmed')}"
            except Exception as exc:
                last = str(exc)
            await asyncio.sleep(3)
        raise TimeoutError(f"Expected image did not become active: {last}")

    async def healthy_trial(self, digest):
        await asyncio.sleep(5)
        current = active_image(await self.state())
        if current.get("hash") != digest:
            raise ValueError("Trial image changed/disappeared before confirmation")


async def update_board(target, data, digest):
    state = await target.state()
    current = active_image(state)
    if current.get("hash") == digest and current.get("confirmed"):
        return "ALREADY_CURRENT"
    pending = [i for i in state.get("images", []) if i.get("pending")]
    if any(i.get("hash") != digest for i in pending):
        raise ValueError("Another image is pending; resolve it before batch update")
    if current.get("hash") != digest:
        if not current.get("confirmed"):
            raise ValueError("Existing unrelated trial is unconfirmed; inspect this board first")
        staged = [i for i in state.get("images", []) if i.get("hash") == digest and not i.get("active")]
        if not staged:
            await target.transfer(data)
            state = await target.state()
            staged = [i for i in state.get("images", []) if i.get("hash") == digest and not i.get("active")]
            if len(staged) != 1:
                raise ValueError("Uploaded image hash missing from secondary slot")
        await target.send(0, {"hash": digest, "confirm": False})
        await target.reset()
        await target.wait_active(digest)
    # Explicit batch policy: confirm only the intended running image after two
    # successful management observations. This is boot health, not sensor/RF QA.
    await target.healthy_trial(digest)
    await target.send(0, {"hash": digest, "confirm": True})
    current = active_image(await target.state())
    if current.get("hash") != digest or not current.get("confirmed"):
        raise ValueError("Image confirmation did not persist")
    await target.reset()
    await target.wait_active(digest, confirmed=True)
    return "UPDATED"


async def enroll(a):
    from bleak import BleakScanner
    if a.roster.exists():
        raise ValueError("Roster already exists; preserve it or choose another --roster")
    found = {}
    for _ in range(3):
        devices = await BleakScanner.discover(timeout=10, return_adv=True)
        for device, adv in devices.values():
            name = adv.local_name or device.name or ""
            if re.fullmatch(r"PCB_[0-9A-F]{8}", name) and SERVICE in adv.service_uuids:
                found[device.address.upper()] = {"name": name, "address": device.address}
    boards = sorted(found.values(), key=lambda b: b["name"])
    for b in boards:
        print(b["name"], b["address"])
    if len(boards) != a.count:
        raise ValueError(f"Found {len(boards)} boards; expected {a.count}. No roster saved. Power only the intended fleet and rescan.")
    roster = {"expected_count": a.count, "boards": boards}
    validate_roster(roster)
    # Verify names and readable SMP before enrolling. No flash writes here.
    for b in boards:
        async with FleetBoard(b) as target:
            current = active_image(await target.state())
        print(f"Verified {b['name']}: {current.get('version')}")
    a.roster.parent.mkdir(parents=True, exist_ok=True)
    a.roster.write_text(json.dumps(roster, indent=2)+"\n")
    print(f"Enrolled {len(boards)} boards in {a.roster}. No firmware changed.")


async def run_jobs(boards, worker, parallel):
    """Bound concurrency, and await cleanup of siblings on cancellation/error."""
    semaphore = asyncio.Semaphore(parallel)
    async def limited(index, board):
        async with semaphore:
            return await worker(index, board)
    tasks = [asyncio.create_task(limited(i, b)) for i, b in enumerate(boards, 1)]
    try:
        return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def batch(a):
    from bleak import BleakScanner
    boards = validate_roster(json.loads(a.roster.read_text()))
    data = a.image.read_bytes()
    info = image_info(data)
    imgtool("verify", "--key", a.key, a.image)
    digest = bytes.fromhex(info["image_hash"])
    started = time.monotonic()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = ROOT / "build/batch-ota" / stamp
    out.mkdir(parents=True)
    report = {"image": info, "started_utc": stamp, "parallel": a.parallel, "boards": []}
    path = out / "results.json"
    def save():
        report["elapsed_seconds"] = round(time.monotonic()-started, 2)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(report, indent=2)+"\n")
        temp.replace(path)
    # Passing discovered devices avoids an implicit scan at every connection,
    # which is particularly problematic with concurrent Windows connections.
    print("Discovering enrolled boards once before connecting...", flush=True)
    devices = {d.address.upper(): d for d in await BleakScanner.discover(timeout=8)}
    async def worker(index, b):
        print(f"[{index}/{len(boards)}] {b['name']} {b['address']}", flush=True)
        row = {**b, "status": "FAILED", "attempts": []}
        board_started = time.monotonic()
        device = devices.get(b["address"].upper())
        if device is None:
            row["attempts"].append({"error": "Not found in BLE scan; power board and retry"})
        else:
            for attempt in range(1, a.attempts+1):
                attempt_started = time.monotonic()
                try:
                    async with FleetBoard(b, device) as target:
                        row["status"] = await update_board(target, data, digest)
                    row["attempts"].append({"attempt": attempt, "result": row["status"],
                        "elapsed_seconds": round(time.monotonic()-attempt_started, 2)})
                    break
                except Exception as exc:
                    row["status"] = "FAILED"
                    row["attempts"].append({"attempt": attempt, "error": str(exc),
                        "elapsed_seconds": round(time.monotonic()-attempt_started, 2)})
                    print(f"{b['name']}: attempt {attempt} failed: {exc}", flush=True)
        row["elapsed_seconds"] = round(time.monotonic()-board_started, 2)
        report["boards"].append(row)
        # No await in save(): concurrent tasks cannot interleave file replacement.
        save()
        print(f"{b['name']}: {row['status']} ({row['elapsed_seconds']} s)", flush=True)
    await run_jobs(boards, worker, a.parallel)
    save()
    failed = sum(b["status"] == "FAILED" for b in report["boards"])
    print(f"\n{len(boards)-failed}/{len(boards)} successful; {failed} failed; {report['elapsed_seconds']} s. Report: {path}")
    return 1 if failed else 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["enroll", "update"])
    p.add_argument("--roster", type=Path, default=ROOT / "boards_ota.json")
    p.add_argument("--count", type=int, default=30)
    p.add_argument("--image", type=Path, default=ROOT / "build/images/validation_b.signed.bin")
    p.add_argument("--key", type=Path, default=ROOT / "keys/development.pem")
    p.add_argument("--attempts", type=int, choices=[1, 2, 3], default=2)
    p.add_argument("--parallel", type=int, choices=[1, 2], default=1, help="Maximum simultaneous boards; use 1 if adapter is unreliable with 2")
    a = p.parse_args()
    try:
        return asyncio.run(enroll(a) if a.action == "enroll" else batch(a)) or 0
    except KeyboardInterrupt:
        print("Stopped. Re-run to inspect actual device states and resume.")
        return 130
    except Exception as exc:
        p.exit(1, f"Batch OTA failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
