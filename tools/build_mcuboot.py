"""Build pinned MCUboot for this custom board, without changing global packages."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REV = "c657cbea75f2bb1faf1fceacf972a0537a8d26dd"


def run(args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--key", type=Path, default=ROOT / "keys/development.pem")
    p.add_argument("--source", type=Path, help="Existing MCUboot checkout at the pinned commit")
    a = p.parse_args()
    key = a.key.resolve()
    if not key.is_file():
        p.error("Generate a development RSA-2048 key first: python tools/sign_image.py keygen")
    source = (a.source or ROOT / "build/mcuboot-source").resolve()
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--branch", "v1.9.0", "--depth", "1",
             "https://github.com/mcu-tools/mcuboot.git", source])
    rev = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if rev != REV:
        p.error(f"MCUboot source must be {REV}; got {rev}")
    dirty = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True)
    if dirty.strip():
        p.error("MCUboot checkout must be clean")
    home = Path(os.environ.get("PLATFORMIO_CORE_DIR", Path.home() / ".platformio"))
    pkg = home / "packages"
    zephyr = pkg / "framework-zephyr"
    exe = ".exe" if os.name == "nt" else ""
    cmake = pkg / f"tool-cmake/bin/cmake{exe}"
    ninja = pkg / f"tool-ninja/ninja{exe}"
    dtc = pkg / f"tool-dtc/dtc{exe}"
    python = home / ("penv/Scripts/python.exe" if os.name == "nt" else "penv/bin/python")
    build = ROOT / "build/mcuboot"
    build.mkdir(parents=True, exist_ok=True)
    overlay = build / "boot.overlay"
    overlay.write_text('/ { chosen { zephyr,code-partition = &boot_partition; }; };\n')
    config = build / "boot.conf"
    config.write_text('\n'.join([
        'CONFIG_BOOT_SIGNATURE_TYPE_RSA=y',
        f'CONFIG_BOOT_SIGNATURE_KEY_FILE="{key.as_posix()}"',
        'CONFIG_BOOT_VALIDATE_SLOT0=y',
        'CONFIG_BOOT_MAX_IMG_SECTORS=128', 'CONFIG_BOOT_SWAP_USING_SCRATCH=y',
        'CONFIG_LOG=n', 'CONFIG_CONSOLE=n', 'CONFIG_CONSOLE_HANDLER=n',
        'CONFIG_SERIAL=n', 'CONFIG_UART_CONSOLE=n', 'CONFIG_BOOT_BANNER=n',
        'CONFIG_SIZE_OPTIMIZATIONS=y', 'CONFIG_BOOT_WATCHDOG_FEED=n', '']))
    modules = [zephyr / "_pio/_bare_module", zephyr / "_pio/modules/hal/cmsis",
               zephyr / "_pio/modules/hal/nordic", zephyr / "_pio/modules/crypto/mbedtls", source]
    env = os.environ.copy()
    env.update(ZEPHYR_BASE=zephyr.as_posix(), ZEPHYR_TOOLCHAIN_VARIANT="gnuarmemb",
               GNUARMEMB_TOOLCHAIN_PATH=(pkg / "toolchain-gccarmnoneeabi").as_posix())
    run([cmake, "-GNinja", "-DBOARD=dwm3001c_custom", f"-DBOARD_ROOT={ROOT.as_posix()}",
         f'-DCMAKE_C_FLAGS=-include "{(ROOT / "include/mcuboot_zephyr27_compat.h").as_posix()}"',
         f"-DPYTHON_EXECUTABLE={python.as_posix()}", f"-DCMAKE_MAKE_PROGRAM={ninja.as_posix()}",
         f"-DDTC={dtc.as_posix()}", f"-DOVERLAY_CONFIG={config.as_posix()}",
         f"-DDTC_OVERLAY_FILE={overlay.as_posix()}",
         "-DZEPHYR_MODULES=" + ";".join(m.as_posix() for m in modules),
         "-S", source / "boot/zephyr", "-B", build / "out"], env=env)
    run([cmake, "--build", build / "out"], env=env)
    from intelhex import IntelHex
    image = IntelHex(str(build / "out/zephyr/zephyr.hex"))
    if image.minaddr() != 0 or image.maxaddr() >= 0xC000:
        raise ValueError("Bootloader exceeds the preserved 48 KiB partition or writes outside flash")
    import hashlib, json
    from cryptography.hazmat.primitives import serialization
    private = serialization.load_pem_private_key(key.read_bytes(), password=None)
    public = private.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    manifest = {"source_commit": REV, "boot_hex_sha256": hashlib.sha256((build / "out/zephyr/zephyr.hex").read_bytes()).hexdigest(), "public_key_sha256": hashlib.sha256(public).hexdigest()}
    (build / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"MCUboot PASS: {image.maxaddr()+1} address span bytes; {build / 'out/zephyr/zephyr.hex'}")


if __name__ == "__main__":
    main()
