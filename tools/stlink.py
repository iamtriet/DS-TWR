"""Enumerate ST-Link USB ports using the same libusb runtime as OpenOCD."""
import ctypes as C
from pathlib import Path
import re

PIDS = {0x3744, 0x3748, 0x374b, 0x374d, 0x374e, 0x374f, 0x3752, 0x3753, 0x3754}


def default_tools():
    base = Path.home() / ".platformio/packages/tool-openocd"
    exe, scripts = base / "bin/openocd.exe", base / "openocd/scripts"
    if not exe.is_file() or not (scripts / "interface/stlink.cfg").is_file():
        raise FileNotFoundError("OpenOCD not found; select the executable and scripts directory")
    return exe, scripts


def validate_location(location):
    if not re.fullmatch(r"[0-9]+-[0-9]+(?:\.[0-9]+)*", location):
        raise ValueError("Select an ST-Link USB location such as 1-5 (not a COM port)")
    return location


def list_probes(openocd=None):
    exe = Path(openocd) if openocd else default_tools()[0]
    lib = C.CDLL(str(exe.parent / "libusb-1.0.dll"))
    class Descriptor(C.Structure):
        _fields_ = [("length", C.c_uint8), ("type", C.c_uint8), ("usb", C.c_uint16),
                    ("klass", C.c_uint8), ("subclass", C.c_uint8), ("protocol", C.c_uint8),
                    ("packet", C.c_uint8), ("vid", C.c_uint16), ("pid", C.c_uint16),
                    ("device", C.c_uint16), ("manufacturer", C.c_uint8), ("product", C.c_uint8),
                    ("serial", C.c_uint8), ("configurations", C.c_uint8)]
    def bind(name, result, *args):
        fn = getattr(lib, name); fn.restype = result; fn.argtypes = list(args); return fn
    ptr = C.c_void_p
    init = bind("libusb_init", C.c_int, C.POINTER(ptr))
    get_list = bind("libusb_get_device_list", C.c_ssize_t, ptr, C.POINTER(C.POINTER(ptr)))
    get_desc = bind("libusb_get_device_descriptor", C.c_int, ptr, C.POINTER(Descriptor))
    get_bus = bind("libusb_get_bus_number", C.c_uint8, ptr)
    get_ports = bind("libusb_get_port_numbers", C.c_int, ptr, C.POINTER(C.c_uint8), C.c_int)
    free = bind("libusb_free_device_list", None, C.POINTER(ptr), C.c_int)
    end = bind("libusb_exit", None, ptr)
    ctx = ptr(); devices = C.POINTER(ptr)()
    if init(C.byref(ctx)):
        raise RuntimeError("Could not initialize USB discovery")
    try:
        count = get_list(ctx, C.byref(devices))
        if count < 0: raise RuntimeError("Could not enumerate USB devices")
        found = []
        for index in range(count):
            dev = devices[index]; desc = Descriptor()
            if get_desc(dev, C.byref(desc)) or desc.vid != 0x0483 or desc.pid not in PIDS:
                continue
            ports = (C.c_uint8 * 8)(); length = get_ports(dev, ports, 8)
            if length <= 0: continue
            location = f"{get_bus(dev)}-" + ".".join(str(ports[i]) for i in range(length))
            found.append({"location": location, "label": f"ST-Link USB {location} (0483:{desc.pid:04X})"})
        return found
    finally:
        if devices: free(devices, 1)
        end(ctx)


if __name__ == "__main__":
    import json
    print(json.dumps(list_probes(), indent=2))
