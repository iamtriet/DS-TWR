# DS-TWR — UWB Double-Sided Two-Way Ranging Firmware & Desktop Control System

> Complete Zephyr RTOS firmware and Python desktop toolchain for the **Cyborg greenC** custom PCB featuring a **Qorvo DWM3001C** (nRF52833 + DW3110 UWB) and **BNO085** IMU. Supports DS-TWR ranging, multi-tag fleet tracking, relative 3D positioning, signed MCUboot BLE OTA, and a one-click Windows Control Center.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Hardware Platform](#hardware-platform)
- [Repository Structure](#repository-structure)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
  - [Build Firmware](#build-firmware)
  - [Run Tests](#run-tests)
  - [Sign & Flash](#sign--flash)
- [OTA Updates](#ota-updates)
  - [Single Board OTA](#single-board-ota)
  - [Batch OTA (up to 30 boards)](#batch-ota-up-to-30-boards)
- [Desktop Control Center](#desktop-control-center)
- [UWB 3D Tracking](#uwb-3d-tracking)
- [Firmware Architecture](#firmware-architecture)
- [Configuration](#configuration)
- [Testing](#testing)
- [Documentation](#documentation)
- [License & Vendor Dependencies](#license--vendor-dependencies)

---

## Overview

This project implements a complete **Ultra-Wideband (UWB) Double-Sided Two-Way Ranging** system built on top of Zephyr RTOS. It runs on a custom PCB combining the Qorvo DWM3001C module (nRF52833 SoC + DW3110 UWB transceiver) with a Hillcrest/CEVA BNO085 9-axis IMU.

The system enables centimeter-level indoor positioning with:
- **Single-pair DS-TWR** distance measurements
- **Multi-tag fleet tracking** (1 anchor → up to 8 tags)
- **Relative 3D position estimation** for up to 30 mobile nodes
- **Secure MCUboot BLE OTA** with RSA-signed images
- **Desktop GUI** for fleet management, tracking visualization, and provisioning

---

## Features

| Category | Capability |
|---|---|
| **UWB Ranging** | Calibrated DS-TWR, single/multi-tag, configurable IDs, collision-aware scheduling |
| **3D Tracking** | MDS/lateration initialization, Huber nonlinear least-squares solver, constant-velocity filter |
| **IMU Integration** | BNO085 via CEVA SH-2/SHTP over I2C, rotation vectors, accelerometer, gyroscope |
| **OTA Updates** | MCUboot + MCUmgr SMP over BLE, RSA-2048 signed images, parallel 2-board batch |
| **Desktop App** | Tk-based Control Center: BLE scan, OTA, ST-Link provisioning, tracking visualization |
| **UART Console** | Interactive CLI for diagnostics, radio control, IMU queries, network status |
| **Simulation** | 30-node range simulator with 13 scenarios, JSONL recording/replay, CSV export |
| **Hardware Support** | LED UV/RGB daughter boards, PWM outputs, battery-powered operation |

---

## Hardware Platform

**Board:** Cyborg greenC v001 (36.5 × 45.5 mm)

```
┌───────────────────────────────────────────────────────┐
│              Cyborg greenC v001                        │
│                                                       │
│  ┌──────────────────────┐    ┌──────────────────────┐ │
│  │  DWM3001C (U7)       │    │  BNO085 IMU          │ │
│  │  nRF52833 + DW3110   │    │  I2C + INT/RST       │ │
│  │  512K Flash / 128K   │    └──────────────────────┘ │
│  └──────────────────────┘                             │
│                                                       │
│  [JP1 8-pin] ─── Daughter boards (UV/RGB/Debug)       │
│  [JP4 6-pin] ─── USB or UART (resistor-selectable)   │
│  [SWD]       ─── Programming (ST-Link V2)             │
│  Nguồn: LiPo → TP4056 → LDO → 3V3                   │
└───────────────────────────────────────────────────────┘
```

| Component | Specification |
|---|---|
| MCU | ARM Cortex-M4F @ 64 MHz (nRF52833) |
| UWB | DW3110 via SPI3, IEEE 802.15.4z HRP |
| IMU | BNO085 (CEVA SH-2), I2C @ 400 kHz |
| BLE | Bluetooth 5.2 (integrated) |
| Flash | 512 KB on-chip (MCUboot layout) |
| RAM | 128 KB SRAM |

> **Full hardware reference:** see [`HARDWARE.md`](HARDWARE.md) for the definitive GPIO pin map, connector details, power design, flash layout, and known hardware traps.

---

## Repository Structure

```
DS-TWR/
├── src/                    # Firmware source (C)
│   ├── main.c              # Application entry point & main loop
│   ├── radio.c             # DW3110 UWB radio driver interface
│   ├── radio_port.c        # Zephyr SPI/GPIO adapter for DW3000
│   ├── uwb_app.c           # DS-TWR ranging application logic
│   ├── uwb_network.c       # Multi-node network coordination
│   ├── uwb_frame.c         # UWB frame construction/parsing
│   ├── uwb_reference.c     # Reference ranging implementation
│   ├── multitag.c           # Multi-tag sequential polling
│   ├── ranging.c            # Range measurement utilities
│   ├── imu.c               # BNO085 IMU driver (SH-2/SHTP)
│   ├── ota.c               # MCUboot BLE OTA (MCUmgr SMP)
│   ├── console.c           # UART CLI with command dispatch
│   ├── board.c             # GPIO, LED, board identity
│   ├── control.c           # BLE control service
│   ├── diagnostic.c        # Hardware diagnostics
│   ├── uwb3d_firmware.c    # 3D tracking firmware-side support
│   ├── uwb3d_wire.c        # Binary UART wire protocol
│   ├── tag_table.c         # Multi-tag state management
│   └── validation_math.c   # Math utilities
├── include/                # Header files
├── tools/                  # Python desktop tools
│   ├── control_app.py      # Tk Control Center GUI (36K)
│   ├── ota_update.py       # Single-board BLE OTA
│   ├── ota_batch.py        # Batch OTA for 30 boards
│   ├── first_flash.py      # Initial ST-Link provisioning
│   ├── sign_image.py       # MCUboot RSA image signing
│   ├── build_mcuboot.py    # MCUboot bootloader builder
│   ├── uwb3d_core.py       # 3D solver, registry, CV filter
│   ├── uwb3d_runtime.py    # Acquisition, simulator, replay
│   ├── uwb3d_view.py       # 3D canvas, projections, dashboard
│   ├── uwb3d_protocol.py   # COBS/CRC binary UART protocol
│   ├── uwb3d_gateway.py    # UART gateway controller
│   ├── tracking.py         # Legacy 2-board tracking
│   ├── tracking_view.py    # Tracking visualization
│   ├── multi_anchor.py     # Multi-anchor controller
│   ├── auto_anchor.py      # Automatic anchor calibration
│   ├── cooperative_tracking.py  # Cooperative tracking
│   ├── stlink.py           # ST-Link probe discovery
│   ├── uart_monitor.py     # UART log monitor
│   ├── topdown_map.py      # Top-down map view
│   ├── run_tests.py        # Test runner
│   ├── validate_artifacts.py    # Build artifact validator
│   └── requirements.txt    # Python dependencies
├── vendor/                 # Third-party drivers
│   ├── dw3000/             # Qorvo DW3000 driver (v08.02.02)
│   ├── sh2/                # CEVA SH-2/SHTP (BNO085)
│   ├── albifache/          # Support library
│   └── PROVENANCE.md       # Dependency provenance & local changes
├── boards/                 # Custom PlatformIO board definitions
├── zephyr/                 # Zephyr build configuration
│   ├── CMakeLists.txt
│   ├── prj.conf            # Kconfig: BLE, MCUboot, SPI, I2C, UART
│   └── app.overlay         # Devicetree overlay
├── config/                 # Runtime configuration files
├── docs/                   # Extensive documentation (21 files)
├── tests/                  # Unit & integration tests (C + Python)
├── keys/                   # Signing keys (gitignored)
├── Cyborg_Main/            # Main PCB Altium design files
├── Cyborg_Debug/           # Debug adapter PCB design
├── Cyborg Green C LED RGB/ # RGB daughter board design
├── Cyborg Green_C LED UV/  # UV daughter board design
├── Tai-lieu-huong-dan/     # Vietnamese hardware documentation
├── HARDWARE.md             # Definitive hardware reference
├── platformio.ini          # PlatformIO project configuration
├── Control_Center.cmd      # One-click desktop app launcher
├── Enroll_30_Boards.cmd    # Batch enrollment script
├── Update_All_Boards.cmd   # Batch OTA update script
└── Update_Two_Boards.cmd   # Two-board OTA update script
```

---

## Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| [PlatformIO](https://platformio.org/) | Core 6+ | Firmware build system |
| [Python](https://python.org/) | 3.10+ | Desktop tools, signing, tests |
| [ST-Link V2](https://www.st.com/) | — | Initial board programming |
| Windows Bluetooth | — | BLE OTA and control |
| [Zig](https://ziglang.org/) | 0.16.0 | Native test compilation |

---

## Getting Started

### Build Firmware

```powershell
# Install Python dependencies
python -m pip install -r tools/requirements.txt

# Build both firmware environments
pio run -e validation -e validation_b
```

**Firmware Environments:**

| Environment | Purpose |
|---|---|
| `validation` | Full firmware without LED blink |
| `validation_b` | Full firmware + LED heartbeat (100ms on/off) — primary release |
| `reference` | Reference ranging mode |

### Run Tests

```powershell
# Install Zig for native C tests
python -m pip install ziglang==0.16.0

# Run the full test suite
python tools/run_tests.py
```

The test suite includes both native C tests (compiled with Zig) and Python tests covering the desktop tools, protocol, tracking solver, and OTA logic.

### Sign & Flash

```powershell
# Generate RSA-2048 signing key (only once — never overwrites)
python tools/sign_image.py keygen

# Build MCUboot bootloader
python tools/build_mcuboot.py

# Sign firmware images
python tools/sign_image.py sign --env validation
python tools/sign_image.py sign --env validation_b

# Prepare initial flash HEX (does NOT flash by default)
python tools/first_flash.py

# Validate build artifacts
python tools/validate_artifacts.py
```

> ⚠️ **Do not use plain `pio upload`** — it sends unsigned firmware and is incompatible with the MCUboot setup. Use the ST-Link procedure below or BLE OTA.

**First-time programming via ST-Link:**

```powershell
python tools/first_flash.py --flash --adapter-location 1-5 \
  --image build/images/validation_b.signed.bin \
  --openocd "$env:USERPROFILE/.platformio/packages/tool-openocd/bin/openocd.exe" \
  --scripts "$env:USERPROFILE/.platformio/packages/tool-openocd/openocd/scripts"
```

---

## OTA Updates

All OTA updates use **BLE MCUmgr SMP** with RSA-2048 signed images. The firmware never auto-confirms a new image — explicit verification is always performed.

### Single Board OTA

```powershell
python tools/ota_update.py --image build/images/validation_b.signed.bin
```

### Batch OTA (up to 30 boards)

**Step 1: Enroll the fleet (once)**
```powershell
# Power all 30 boards, ensure BLE range
Enroll_30_Boards.cmd
# Or: python tools/ota_batch.py enroll --count 30
```

**Step 2: Update all enrolled boards**
```powershell
Update_All_Boards.cmd
# Or: python tools/ota_batch.py update --parallel 2
```

The batch updater:
- ✅ Verifies signed images with local key
- ✅ Skips boards already running the current version
- ✅ Uploads with negotiated BLE packet size
- ✅ Performs trial boot, verification, explicit confirmation
- ✅ Runs two boards concurrently with connection reuse
- ✅ Saves per-board reports under `build/batch-ota/`

See [`docs/BATCH_OTA.md`](docs/BATCH_OTA.md) for details.

---

## Desktop Control Center

Double-click **`Control_Center.cmd`** to launch the Python/Tk desktop application.

**Features:**
- 📡 **BLE Discovery** — scan and select nearby boards
- 📦 **Signed OTA** — upload firmware with verification
- 🔧 **ST-Link Provisioning** — program new boards via SWD
- 📏 **Live Tracking** — real-time distance readings with trend graphs
- 🎯 **Multi-tag View** — 1 anchor + up to 8 tags, 60-second trends
- 🌐 **3D Positioning** — relative 3D tracking with projections
- 🔬 **Sensor Diagnostics** — IMU reports, radio status, board info
- 🎮 **Fleet Roster** — save/load board sets for batch operations

No server or account required. Dependencies: `tools/requirements.txt` and Python with Tkinter.

See [`docs/CONTROL_CENTER.md`](docs/CONTROL_CENTER.md) for the full guide.

---

## UWB 3D Tracking

The 3D tracking system estimates relative positions of mobile nodes using only range measurements — no pre-surveyed anchors required.

**Capabilities:**
- Registry for 30 mobile nodes (configurable up to 128)
- Versioned COBS/CRC binary UART transport
- Symmetric distance matrix with staleness tracking
- MDS or sparse lateration initialization
- Huber nonlinear least-squares solver with analytic Jacobian
- Constant-velocity filter with motion/age weighting
- 3D canvas with XY/XZ/YZ projections, orbit/pan/zoom
- 13 range-driven simulation scenarios
- JSONL recording with replay, speed/pause/seek, CSV export
- Experimental sparse BP-DS-TWR UART gateway

See [`docs/UWB3D_TRACKING.md`](docs/UWB3D_TRACKING.md) and [`docs/UWB3D_PROTOCOL.md`](docs/UWB3D_PROTOCOL.md).

---

## Firmware Architecture

```
main.c
 ├── board_init()           → GPIO, LED, device identity (FICR)
 ├── console_init()         → UART CLI @ 115200 baud
 ├── ota_init()             → MCUboot + BLE MCUmgr SMP
 └── Main Loop:
      ├── cli_service()     → Command parsing & dispatch
      ├── imu_service()     → BNO085 sensor polling
      ├── uwb_service()     → DS-TWR measurement scheduling
      ├── network_service() → Multi-node coordination
      └── reference_service() → Reference ranging
```

**Flash Layout (MCUboot):**

```
Address      Size      Label          Purpose
─────────────────────────────────────────────
0x00000000   48 KB     mcuboot        Bootloader
0x0000C000   200 KB    image-0        Primary app slot
0x0003E000   200 KB    image-1        OTA staging slot
0x00070000   40 KB     image-scratch  Swap buffer
0x0007A000   24 KB     storage        NVS settings
─────────────────────────────────────────────
             512 KB    TOTAL
```

---

## Configuration

### PlatformIO (`platformio.ini`)

- **Platform:** `nordicnrf52@10.11.0`
- **Framework:** Zephyr RTOS
- **Board:** `dwm3001c_custom` (custom board definition)
- **Upload/Debug:** ST-Link

### Zephyr (`zephyr/prj.conf`)

Key Kconfig options:
- `CONFIG_BOOTLOADER_MCUBOOT=y` — MCUboot secure bootloader
- `CONFIG_MCUMGR_SMP_BT=y` — BLE firmware management
- `CONFIG_USB_DEVICE_STACK=n` — **Must remain off** (VUSB is a dead net)
- `CONFIG_BT_PERIPHERAL=y` — BLE advertising
- `CONFIG_SPI=y` + `CONFIG_I2C=y` — UWB + IMU communication

### Gateway Config (`config/uwb3d-gateway-experimental.json`)

Tunable parameters for the 3D tracking solver: target rate, node timeouts, noise model, solver budget, filtering options.

---

## Testing

The project includes comprehensive tests at both firmware and host levels:

**Native C Tests** (compiled with Zig):
- `tests/test_core.c` — Core firmware logic
- `tests/test_anchor_listener.c` — Anchor-side protocol
- `tests/test_reference_app.c` — Reference ranging
- `tests/test_uwb_network.c` — Network protocol
- `tests/test_uwb3d_wire.c` — Wire protocol codec

**Python Tests:**
- `tests/test_uwb3d.py` — 3D solver, registry, protocol (13K)
- `tests/test_tracking.py` — Tracking estimation
- `tests/test_cooperative.py` — Cooperative tracking
- `tests/test_batch.py` — Batch OTA logic
- `tests/test_control.py` — Control center
- `tests/test_auto_anchor.py` — Auto-anchor calibration
- `tests/test_multi_anchor.py` — Multi-anchor coordination
- And more...

```powershell
python tools/run_tests.py   # Run all tests
```

---

## Documentation

The `docs/` directory contains 21 detailed documents covering implementation, test results, and hardware analysis:

| Document | Topic |
|---|---|
| [`UWB3D_TRACKING.md`](docs/UWB3D_TRACKING.md) | 3D tracking implementation & runbook |
| [`UWB3D_PROTOCOL.md`](docs/UWB3D_PROTOCOL.md) | Wire/radio protocol specification |
| [`UWB3D_HARDWARE_2026-10-11.md`](docs/UWB3D_HARDWARE_2026-10-11.md) | Hardware test results |
| [`CONTROL_CENTER.md`](docs/CONTROL_CENTER.md) | Desktop app user guide |
| [`BATCH_OTA.md`](docs/BATCH_OTA.md) | 30-board OTA update guide |
| [`COOPERATIVE_TRACKING.md`](docs/COOPERATIVE_TRACKING.md) | Cooperative tracking system |
| [`IMPLEMENTATION_REPORT.md`](docs/IMPLEMENTATION_REPORT.md) | Software verification report |
| [`HARDWARE_TEST.md`](docs/HARDWARE_TEST.md) | Hardware bench runbook |
| [`INSPECTION.md`](docs/INSPECTION.md) | Schematic inspection evidence |
| [`HARDWARE.md`](HARDWARE.md) | **Definitive hardware reference** |

---

## License & Vendor Dependencies

This project uses the following third-party components (see [`vendor/PROVENANCE.md`](vendor/PROVENANCE.md) for details):

| Dependency | License | Source |
|---|---|---|
| CEVA SH-2/SHTP | Apache-2.0 | [ceva-dsp/sh2](https://github.com/ceva-dsp/sh2) |
| Qorvo DW3000 driver | Qorvo proprietary (use with Qorvo devices only) | [br101/dw3000-decadriver-source](https://github.com/br101/dw3000-decadriver-source) |
| MCUboot | Apache-2.0 | [mcu-tools/mcuboot](https://github.com/mcu-tools/mcuboot) v1.9.0 |
| Zephyr RTOS | Apache-2.0 | via PlatformIO `nordicnrf52@10.11.0` |

Local adaptations to vendor code are documented in [`vendor/PROVENANCE.md`](vendor/PROVENANCE.md).

---

*Built on Zephyr RTOS • Qorvo DWM3001C • Cyborg greenC PCB*
