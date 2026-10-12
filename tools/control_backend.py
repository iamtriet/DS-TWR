"""Desktop control transports. No hardware is opened at import time."""
import asyncio
import re
import time

CONTROL_WRITE = "8d53dc21-1db7-4cd3-868b-8a527460aa84"
CONTROL_NOTIFY = "8d53dc22-1db7-4cd3-868b-8a527460aa84"


def radio_id(value):
    if not re.fullmatch(r"(?:0[xX])?[0-9a-fA-F]{1,4}", value.strip()):
        raise ValueError("IDs must be hexadecimal, 0001 through FFFE")
    number = int(value, 16)
    if not 0 < number < 65535:
        raise ValueError("IDs 0000 and FFFF are reserved")
    return f"{number:04X}"


def tracking_commands(mode, own, peers):
    own = radio_id(own)
    tags = [radio_id(x) for x in re.split(r"[,\s]+", peers.strip()) if x]
    if not tags or own in tags or len(set(tags)) != len(tags):
        raise ValueError("Supply unique peer IDs different from the local ID")
    if mode not in ("Tag", "Single anchor", "Multi anchor"):
        raise ValueError("Unknown tracking mode")
    if len(tags) > (8 if mode == "Multi anchor" else 1):
        raise ValueError("Use one peer for tag/single mode; at most eight tags for multi mode")
    # Firmware rejects conflicting existing IDs; never silently change identity.
    commands = ["uwb stop", f"uwb role {'tag' if mode == 'Tag' else 'anchor'}",
                "led mode role",
                f"uwb id {own}", f"uwb peer {tags[0]}", "uwb init", "uwb output csv"]
    if mode == "Multi anchor":
        commands += ["multitag clear"] + [f"multitag add {tag}" for tag in tags] + ["multitag start"]
    else:
        commands += ["uwb continuous"]
    return commands


class Session:
    def __init__(self, label, emit, *, address=None, port=None):
        self.label, self.emit = label, emit
        self.address, self.port = address, port
        self.client = self.serial = self.reader = None
        self.lines = asyncio.Queue()
        self.buffer = bytearray()
        self.lock = asyncio.Lock()
        self.closed = False
        self.desynchronized = False

    def feed(self, data):
        self.buffer.extend(data)
        while b"\n" in self.buffer:
            raw, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            line = raw.decode("utf-8", errors="replace").rstrip("\r")
            self.emit("line", (self.label, line, time.monotonic()))
            if self.lines.qsize() < 2048:
                self.lines.put_nowait(line)
        if len(self.buffer) > 8192:
            self.buffer.clear()
            self.emit("line", (self.label, "ERROR oversized telemetry line dropped", time.monotonic()))

    async def open(self):
        try:
            if self.address:
                from bleak import BleakClient
                self.client = BleakClient(self.address, timeout=20,
                    winrt={"use_cached_services": False},
                    disconnected_callback=lambda _: self.emit("disconnected", self.label))
                await self.client.connect()
                name = bytes(await self.client.read_gatt_char("00002a00-0000-1000-8000-00805f9b34fb")).decode()
                if name != self.label:
                    raise ValueError(f"Expected {self.label}, received {name}")
                if not self.client.services.get_characteristic(CONTROL_WRITE):
                    raise ValueError("Wireless control requires firmware 1.0.5 or newer; install the supplied OTA image")
                await self.client.start_notify(CONTROL_NOTIFY, lambda _, data: self.feed(data))
                await self.client.write_gatt_char(CONTROL_WRITE, b"MODE CONTROL", response=True)
            else:
                import serial
                self.serial = serial.Serial(self.port, 115200, timeout=0, write_timeout=2,
                                            rtscts=False, dsrdtr=False)
                self.reader = asyncio.create_task(self.read_serial())
        except BaseException:
            await self.close()
            raise

    async def read_serial(self):
        try:
            while not self.closed:
                self.feed(self.serial.read(4096))
                await asyncio.sleep(.02)
        except Exception as exc:
            self.emit("line", (self.label, f"Connection lost: {exc}", time.monotonic()))
            self.emit("disconnected", self.label)

    async def command(self, command):
        if not command or len(command) >= 160 or not command.isascii() or any(ord(c)<32 or ord(c)>126 for c in command):
            raise ValueError("Command must be one printable ASCII line under 160 characters")
        if command.split()[0] in ("ota", "reset"):
            raise ValueError("Use the OTA page for firmware management")
        async with self.lock:
            if self.closed or self.desynchronized:
                raise RuntimeError("Reconnect before sending more commands")
            while not self.lines.empty():
                self.lines.get_nowait()
            logged="ref keys [REDACTED]" if command.startswith("ref keys ") else command
            self.emit("line", (self.label, "> " + logged, time.monotonic()))
            try:
                if self.client:
                    await self.client.write_gatt_char(CONTROL_WRITE, command.encode(), response=True)
                else:
                    await asyncio.to_thread(self.serial.write, (command+"\n").encode())
            except BaseException:
                # The board may have accepted a cancelled/failed write. A late
                # ACK must never be interpreted as a later stop command's ACK.
                self.desynchronized = True
                raise
            # UART's original protocol has no terminator for informational commands.
            acknowledged = self.client or command.split()[0] in ("uwb", "multitag", "led") or command in ("imu init", "imu reset", "imu read")
            if not acknowledged:
                await asyncio.sleep(5)
                return
            deadline = time.monotonic()+30
            response = []
            try:
                while True:
                    line = await asyncio.wait_for(self.lines.get(), max(.001, deadline-time.monotonic()))
                    response.append(line)
                    match = re.fullmatch(r"@DONE (-?\d+)", line) if self.client else re.fullmatch(
                        r"\["+re.escape(command.split()[0])+r"\] .* rc=(-?\d+)", line)
                    if match:
                        if int(match[1]):
                            raise RuntimeError(f"{command}: board returned rc={match[1]}; inspect log (calibration/peer may be required)")
                        return response
            except asyncio.TimeoutError:
                self.desynchronized = True
                raise RuntimeError(f"No acknowledgement for {command}; reconnect before retrying")
            except asyncio.CancelledError:
                self.desynchronized = True
                raise

    async def configure(self, mode, own, peers):
        commands = tracking_commands(mode, own, peers)
        try:
            await self.command("uwb stop")
            status = await self.command("uwb status")
            if any("allowed_anchor_count=" in line for line in (status or [])):
                await self.command("uwb anchors clear")
            for command in commands[1:]:
                await self.command(command)
        except Exception:
            if not self.desynchronized:
                try:
                    await self.command("uwb stop")
                except Exception:
                    pass
            raise

    async def close(self):
        self.closed = True
        if self.reader:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        if self.serial:
            self.serial.close()
        if self.client:
            await self.client.disconnect()


async def discover():
    from bleak import BleakScanner
    from ota_update import SERVICE
    found = await BleakScanner.discover(timeout=8, return_adv=True)
    return [{"name": adv.local_name or dev.name, "address": dev.address, "rssi": adv.rssi}
            for dev, adv in found.values()
            if re.fullmatch(r"PCB_[0-9A-F]{8}", adv.local_name or dev.name or "") and SERVICE in adv.service_uuids]
