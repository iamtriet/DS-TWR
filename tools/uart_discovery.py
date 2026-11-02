"""Read-only UART identity discovery; never assign roles or reset boards."""
import asyncio
import re
import time


def parse_identity(data):
    text=data.decode('ascii',errors='ignore')
    uid=re.search(r'FICR ID=([0-9a-fA-F]{16})\b',text)
    version=re.search(r'PCB_VERSION=(\d+\.\d+\.\d+)\b',text)
    if not uid or not version:return None
    value=uid[1].upper()
    return dict(name='PCB_'+value[-8:],uid=value,version=version[1])


def probe_port(port,timeout=2.0):
    import serial
    errors=[]
    for baud in (1000000,115200):
        link=serial.Serial(port=None,baudrate=baud,timeout=.05,write_timeout=.5)
        link.dtr=False;link.rts=False;link.port=port
        try:
            link.open();link.reset_input_buffer()
            # The leading newline discards any partial CLI line from an earlier
            # session. Only 'info' is requested; LEDs/radio roles stay untouched.
            link.write(b'\ninfo\n');link.flush()
            deadline=time.monotonic()+timeout;data=bytearray()
            while time.monotonic()<deadline:
                data.extend(link.read(4096))
                found=parse_identity(data)
                if found:return dict(port=port,baud=baud,**found)
                if len(data)>65536:del data[:-32768]
        except (serial.SerialException,OSError) as exc:
            errors.append(str(exc))
            # A port owned by another application must not be stolen.
            if not link.is_open:break
        finally:link.close()
    raise RuntimeError(f'{port}: no verified PCB identity'+(': '+errors[-1] if errors else ' at 1M/115200 baud'))


async def discover_uart_boards(excluded=()):
    from serial.tools.list_ports import comports
    ports=[p.device for p in comports() if p.vid is not None and p.device not in excluded]
    rows=await asyncio.gather(*(asyncio.to_thread(probe_port,p) for p in ports),return_exceptions=True)
    return dict(boards=[r for r in rows if isinstance(r,dict)],errors=[str(r) for r in rows if isinstance(r,Exception)])
