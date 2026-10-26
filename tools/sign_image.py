"""Generate a local development key, sign current HEX, and verify slot bounds."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SLOT = 0x32000
BASE = 0xC000
HEADER = 0x200
# MCUboot scratch trailer: max_sectors * 3 * write_alignment + fixed fields,
# rounded conservatively to one complete nRF flash page.
TRAILER = 0x1000


def image_info(data):
    if len(data) < 32:
        raise ValueError("Truncated image")
    magic, load, header, protected, size, flags = struct.unpack_from("<IIHHII", data)
    if magic != 0x96F3B83D or header != HEADER or load != 0 or flags != 0:
        raise ValueError("Unsupported MCUboot header")
    end = header + size + protected
    if not size or end + 4 > len(data) or len(data) > SLOT - TRAILER:
        raise ValueError("Image size exceeds slot/trailer or is truncated")
    tlv_magic, total = struct.unpack_from("<HH", data, end)
    if tlv_magic != 0x6907 or total < 4 or end + total != len(data):
        raise ValueError("Invalid or trailing MCUboot TLV data")
    cursor = end + 4
    image_hash = None
    while cursor < end + total:
        if cursor + 4 > end + total:
            raise ValueError("Truncated TLV header")
        kind, reserved, count = struct.unpack_from("<BBH", data, cursor)
        cursor += 4
        if reserved or cursor + count > end + total:
            raise ValueError("Invalid TLV length")
        if kind == 0x10:
            if count != 32 or image_hash is not None:
                raise ValueError("Invalid duplicate/SHA256 TLV")
            image_hash = data[cursor:cursor+count]
        cursor += count
    if image_hash != hashlib.sha256(data[:end]).digest():
        raise ValueError("Image SHA256 mismatch")
    major, minor, revision, build = struct.unpack_from("<BBHI", data, 20)
    return {"version": f"{major}.{minor}.{revision}+{build}", "bytes": len(data),
            "image_hash": image_hash.hex(), "file_sha256": hashlib.sha256(data).hexdigest()}


def imgtool(*args):
    subprocess.run([sys.executable, "-c", "from imgtool.main import imgtool; imgtool()", *map(str, args)], check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["keygen", "sign"])
    p.add_argument("--key", type=Path, default=ROOT / "keys/development.pem")
    p.add_argument("--env", choices=["validation", "validation_b", "reference", "swarm"], default="validation")
    p.add_argument("--output-dir", type=Path, default=ROOT / "build/images")
    p.add_argument("--build-dir", type=Path, default=ROOT / ".pio/build")
    a = p.parse_args()
    if a.action == "keygen":
        if a.key.exists():
            p.error("Refusing to overwrite an existing signing key")
        a.key.parent.mkdir(parents=True, exist_ok=True)
        imgtool("keygen", "-k", a.key, "-t", "rsa-2048")
        print("Local development key created. Keep it private; use the same key for MCUboot and both images.")
        return
    from intelhex import IntelHex
    image = IntelHex(str(a.build_dir / a.env / "firmware.hex"))
    # Zephyr leaves the reserved header absent in HEX (minaddr is vector start).
    if image.minaddr() not in (BASE, BASE + HEADER) or image.maxaddr() >= BASE + SLOT - TRAILER:
        p.error("Application HEX address outside primary payload region")
    data = bytes(image.tobinarray(start=BASE + HEADER, end=image.maxaddr()))
    sp, reset = struct.unpack_from("<II", data)
    if not 0x20000000 < sp <= 0x20020000 or not reset & 1 or not BASE + HEADER <= (reset & ~1) <= image.maxaddr():
        p.error("Invalid nRF52833 application vectors")
    versions = set(re.findall(rb"PCB_VERSION=(\d+\.\d+\.\d+)\x00", data))
    if len(versions) != 1:
        p.error("Expected exactly one firmware version manifest in current HEX")
    version = versions.pop().decode()
    expected = {"validation":"1.0.0","validation_b":"1.0.18","reference":"1.1.0","swarm":"1.2.7"}[a.env]
    if version != expected:
        p.error(f"Stale or wrong build: expected {expected}, got {version}")
    out = a.output_dir
    out.mkdir(parents=True, exist_ok=True)
    raw = out / f"{a.env}.unsigned.bin"
    signed = out / f"{a.env}.signed.bin"
    raw.write_bytes(bytes(HEADER) + data)
    imgtool("sign", "--key", a.key, "--header-size", hex(HEADER), "--align", "4",
            "--version", version, "--slot-size", hex(SLOT), "--max-sectors", "128", raw, signed)
    imgtool("verify", "--key", a.key, signed)
    info = image_info(signed.read_bytes())
    ih = IntelHex()
    ih.frombytes(signed.read_bytes(), offset=BASE)
    ih.write_hex_file(str(signed.with_suffix(".hex")))
    signed.with_suffix(".json").write_text(json.dumps(info, indent=2) + "\n")
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()


