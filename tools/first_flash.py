"""Prepare a bounded bootloader + signed application HEX; optional SWD programming."""
import argparse
import json
from pathlib import Path
import subprocess
from datetime import datetime, timezone
import struct
from intelhex import IntelHex
from sign_image import ROOT, BASE, SLOT, image_info, imgtool
from stlink import list_probes, validate_location


def tcl_path(path):
    value = Path(path).resolve().as_posix()
    if any(c in value for c in "{}\n\r"):
        raise ValueError("Unsupported Tcl path")
    return "{" + value + "}"


def run_openocd(args, command, log):
    result = subprocess.run([*args, "-c", command], capture_output=True, text=True, timeout=180)
    output = result.stdout + result.stderr
    log.write_text(output, encoding="utf-8")
    print(output, flush=True)
    if result.returncode:
        raise RuntimeError(f"ST-Link operation failed; see {log}")


def provision(openocd, scripts, location, image, evidence, info):
    location = validate_location(location)
    if location not in {p["location"] for p in list_probes(openocd)}:
        raise ValueError("Selected ST-Link is no longer connected; refresh the probe list")
    evidence.mkdir(parents=True, exist_ok=False)
    frozen = evidence / "program.hex"
    frozen.write_bytes(image.read_bytes())
    image = frozen
    args = [str(openocd), "-s", str(scripts), "-f", "interface/stlink.cfg",
            "-c", f"adapter usb location {location}", "-c", "adapter speed 1000",
            "-c", "gdb_port disabled; tcl_port disabled; telnet_port disabled",
            "-f", "target/nrf52.cfg"]
    backup = evidence / "before-flash.bin"
    uicr = evidence / "before-uicr.bin"
    ficr = evidence / "ficr.bin"
    check = 'if {[lindex [nrf52.cpu read_memory 0x10000100 32 1] 0] != 0x52833 || [lindex [nrf52.cpu read_memory 0x10000110 32 1] 0] != 512} {error "Expected nRF52833 with 512 KiB flash"}'
    capture = f'{check}; dump_image {tcl_path(ficr)} 0x10000000 0x120; dump_image {tcl_path(backup)} 0 0x80000; dump_image {tcl_path(uicr)} 0x10001000 0x1000'
    run_openocd(args, 'init; halt; set rc [catch {'+capture+'} result]; resume; if {$rc} {error $result}; shutdown', evidence / "backup.log")
    raw = ficr.read_bytes()
    low, high = struct.unpack_from("<II", raw, 0x60)
    if len(backup.read_bytes()) != 0x80000 or len(uicr.read_bytes()) != 0x1000:
        raise ValueError("Incomplete backup; programming refused")
    name = f"PCB_{low:08X}"
    print(f"Target {name}; FICR {high:08X}{low:08X}; backup saved in {evidence}", flush=True)
    same = f'if {{[lindex [nrf52.cpu read_memory 0x10000060 32 1] 0] != {low} || [lindex [nrf52.cpu read_memory 0x10000064 32 1] 0] != {high}}} {{error "Target changed after backup"}}'
    operation = f'{check}; {same}; program {tcl_path(image)} verify; dump_image {tcl_path(evidence / "after-uicr.bin")} 0x10001000 0x1000; dump_image {tcl_path(evidence / "after-storage.bin")} 0x7a000 0x6000'
    run_openocd(args, 'init; halt; set rc [catch {'+operation+'} result]; reset run; if {$rc} {error $result}; shutdown', evidence / "program.log")
    if (evidence / "after-uicr.bin").read_bytes() != uicr.read_bytes():
        raise ValueError("UICR preservation verification failed")
    if (evidence / "after-storage.bin").read_bytes() != backup.read_bytes()[0x7a000:]:
        raise ValueError("Storage preservation verification failed")
    report = {"status": "PROGRAMMED_AND_VERIFIED", "board": name, "ficr": f"{high:08X}{low:08X}",
              "adapter_location": location, "image": info, "storage_preserved": True, "uicr_preserved": True}
    (evidence / "result.json").write_text(json.dumps(report, indent=2)+"\n")
    print(f"SWD program/verify complete: {name}. Scan BLE to check booted firmware. Report: {evidence / 'result.json'}", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--boot", type=Path, default=ROOT / "build/mcuboot/out/zephyr/zephyr.hex")
    p.add_argument("--image", type=Path, default=ROOT / "build/images/validation.signed.bin")
    p.add_argument("--key", type=Path, default=ROOT / "keys/development.pem")
    p.add_argument("--flash", action="store_true", help="Program boot/application and clear swap slots; no mass erase")
    p.add_argument("--openocd", type=Path)
    p.add_argument("--scripts", type=Path)
    p.add_argument("--adapter-location", help="Explicit ST-Link USB bus/port from tools/stlink.py")
    a = p.parse_args()
    data = a.image.read_bytes()
    info = image_info(data)
    imgtool("verify", "--key", a.key, a.image)
    # Verify the bootloader build used exactly this public key. Compare exported
    # public keys, never record or display private material.
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key(a.key.read_bytes(), password=None)
    import hashlib
    manifest = json.loads((ROOT / "build/mcuboot/manifest.json").read_text())
    public = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if hashlib.sha256(public).hexdigest() != manifest["public_key_sha256"]:
        p.error("Signing key differs from MCUboot build manifest")
    if hashlib.sha256(a.boot.read_bytes()).hexdigest() != manifest["boot_hex_sha256"]:
        p.error("Bootloader HEX differs from MCUboot build manifest")
    boot = IntelHex(str(a.boot))
    if boot.minaddr() != 0 or boot.maxaddr() >= BASE:
        p.error("Bootloader writes beyond its partition (including UICR)")
    app = IntelHex()
    app.frombytes(data, offset=BASE)
    # Include erased padding/trailer in primary so a previous swap state cannot
    # survive re-provisioning. Secondary/scratch are erased below; storage is preserved.
    app.puts(BASE+len(data), bytes([255])*(SLOT-len(data)))
    app.puts(BASE+SLOT, bytes([255])*(0x7a000-BASE-SLOT))
    boot.merge(app, overlap="error")
    output = ROOT / "build/images/combined.hex"
    boot.write_hex_file(str(output))
    print(f"Prepared {output}; {json.dumps(info)}")
    print("Initial provisioning replaces boot/application and clears secondary/scratch swap state. Storage and UICR are untouched; no mass erase.")
    if a.flash:
        if not a.openocd or not a.scripts or not a.adapter_location:
            p.error("--flash requires --openocd, --scripts and --adapter-location (run tools/stlink.py)")
        evidence = ROOT / "build/provisioning" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        provision(a.openocd, a.scripts, a.adapter_location, output, evidence, info)


if __name__ == "__main__":
    main()
