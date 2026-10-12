"""Check generated images/configuration and exercise offline signature rejection."""
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
from intelhex import IntelHex
from sign_image import ROOT, BASE, HEADER, SLOT, TRAILER, image_info


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def main():
    result = {}
    key = ROOT / "keys/development.pem"
    for env in ("validation", "validation_b"):
        path = ROOT / f"build/images/{env}.signed.bin"
        data = path.read_bytes()
        info = image_info(data)
        ih = IntelHex(str(ROOT / f".pio/build/{env}/firmware.hex"))
        current = bytes(ih.tobinarray(start=BASE+HEADER, end=ih.maxaddr()))
        size = struct.unpack_from("<I", data, 12)[0]
        require(data[HEADER:HEADER+len(current)] == current and size-len(current) in range(4), "Signed payload is stale")
        conf = (ROOT / f".pio/build/{env}/zephyr/.config").read_text()
        require("CONFIG_BOOTLOADER_MCUBOOT=y" in conf and "CONFIG_MCUMGR_SMP_BT=y" in conf, "Missing OTA config")
        require("CONFIG_NRF_APPROTECT_LOCK=y" not in conf, "Debug lock enabled")
        result[env] = {**info, "free_before_reserved_trailer": SLOT-TRAILER-len(data)}
        proc = subprocess.run([sys.executable, "-c", "from imgtool.main import imgtool; imgtool()",
                               "verify", "--key", str(key), str(path)], capture_output=True, text=True)
        require(proc.returncode == 0, proc.stdout+proc.stderr)
    boot_conf = (ROOT / "build/mcuboot/out/zephyr/.config").read_text()
    for item in ("CONFIG_BOOT_VALIDATE_SLOT0=y", "CONFIG_BOOT_SIGNATURE_TYPE_RSA=y", "CONFIG_BOOT_SWAP_USING_SCRATCH=y"):
        require(item in boot_conf, f"Missing boot policy: {item}")
    require("CONFIG_NRF_APPROTECT_LOCK=y" not in boot_conf, "Bootloader locks SWD")
    # Compare the compiled partition layout for both application and bootloader.
    for path in (ROOT / ".pio/build/validation/zephyr/zephyr.dts", ROOT / "build/mcuboot/out/zephyr/zephyr.dts"):
        dts = path.read_text(encoding="utf-8")
        for start, size in ((0, 0xc000), (0xc000, 0x32000), (0x3e000, 0x32000), (0x70000, 0xa000), (0x7a000, 0x6000)):
            require(re.search(rf"reg\s*=\s*<\s*0x{start:x}\s+0x{size:x}\s*>", dts), f"Partition mismatch in {path}")
    combined = IntelHex(str(ROOT / "build/images/combined.hex"))
    require(combined.minaddr() == 0 and combined.maxaddr() == 0x79fff, "Combined image touches storage/UICR or has wrong bounds")
    require(bytes(combined.tobinarray(start=0x3e000, end=0x79fff)) == bytes([255])*(0x7a000-0x3e000), "OTA swap state not cleared for provisioning")
    # Leave body/hash intact and alter only the RSA signature, proving signature
    # rejection separately from the host's content-hash validation.
    corrupt = bytearray((ROOT / "build/images/validation_b.signed.bin").read_bytes())
    end = HEADER+struct.unpack_from("<I", corrupt, 12)[0]
    cursor = end+4
    while cursor < len(corrupt):
        kind, _, length = struct.unpack_from("<BBH", corrupt, cursor)
        if kind == 0x20:  # RSA-2048 PSS
            corrupt[cursor+4] ^= 1
            break
        cursor += 4+length
    else:
        raise ValueError("No RSA signature TLV to test")
    image_info(corrupt)
    negative = ROOT / "build/negative/corrupt_signature.bin"
    negative.parent.mkdir(parents=True, exist_ok=True)
    negative.write_bytes(corrupt)
    proc = subprocess.run([sys.executable, "-c", "from imgtool.main import imgtool; imgtool()",
                           "verify", "--key", str(key), str(negative)], capture_output=True, text=True)
    require(proc.returncode != 0 and "signature" in (proc.stdout+proc.stderr).lower(), "Corrupt signature unexpectedly accepted")
    result["offline_invalid_signature"] = "REJECTED; physical MCUboot rejection remains untested"
    result["partition_map"] = "MATCHES; combined HEX excludes storage/UICR"
    output = ROOT / "build/verification.json"
    output.write_text(json.dumps(result, indent=2)+"\n")
    print(output.read_text())


if __name__ == "__main__":
    main()
