# Custom PCB validation: inspection and baseline

Date: 2026-10-01. Status: stages 1–2 inspected; implementation pending selection of firmware base and identification of the installed bootloader. This is not an acceptance certificate or a completed validation framework.

## Project structure and evidence

The supplied `purpleC_2` directory is not a Git checkout or a Zephyr workspace. It contains:

- `Cyborg_Main`: native Altium schematic/PCB, manufacturing outputs, BOM, main schematic PDF, module/IMU/power datasheets.
- `Cyborg_Debug`: CH340G USB/UART adapter design, BOM, schematic and manufacturing files.
- `Cyborg Green C LED RGB` and `Cyborg Green_C LED UV`: daughterboard designs.
- `Tai-lieu-huong-dan/Tai-lieu-huong-dan`: a Word programming guide and two Arduino sketches.
- `HARDWARE.md`: hardware reference and historical bring-up claims; explicitly declares itself the hardware source of truth.

The adjacent `../purpleC` contains the firmware mentioned by the hardware reference: `platformio.ini`, custom board definitions, `zephyr/`, `src/driver/dw3110.*`, `src/protocol/twr_ss.c`, `src/app_ranging.c`, `src/ota/smp_bluetooth.c`, and MCUboot/signing/update tools. It was inspected as a candidate for reuse; its application sources were not changed or copied into this folder.

Inspection included the supplied sketches, guide text, BOM component entries, PDF text and enlarged schematic views, native Altium schematic pin/wire/net-label records, native PCB net names, and the adjacent firmware files noted above. Manufacturing archives and complete routed PCB connectivity were not independently validated. Intermediate extracted evidence is under `tmp/inspection/`.

## Hardware and pin mapping

Platform: Qorvo DWM3001C U7, containing Nordic nRF52833 (64 MHz Cortex-M4F, 512 KiB flash, 128 KiB RAM) and DW3110 UWB. The module also has a LIS2DH12TR sharing the exposed I2C bus; a generic I2C acknowledgment alone does not identify the external IMU.

The BOM and native schematic identify external IMU U8 as **BNO085**, not BNO088. The included IMU datasheet covers BNO080/BNO085. Actual fitted part identity remains to be checked on the manufactured board.

| Function | Mapping | Evidence / constraints |
|---|---|---|
| Debug UART TX / RX | P0.20 / P0.21 | Hardware reference, sketches and effective adjacent DTS; 115200, 8N1 |
| IMU SDA | P0.24, module pad 14 | Native schematic SDA net; module datasheet pin table |
| IMU SCL | P1.04, module pad 15 | Native schematic SCL net; module datasheet pin table |
| IMU interrupt | P0.04, module pad 10 | Native schematic and hardware reference; active low at IMU |
| IMU reset | P0.22, module pad 8 | Native schematic and hardware reference; active low |
| IMU BOOT | P0.05, module pad 9 | Hardware reference and native schematic; high for normal application boot |
| IMU protocol / address | I2C, 7-bit 0x4A | Exported IMU circuit: PS1/PS0 low, SA0 low via R15; sketch uses 0x4A |
| I2C pull-ups | External 4.7 kOhm R17/R19 | Schematic; hardware reference forbids internal pull-ups |
| UWB SCK / MOSI / MISO | P0.03 / P0.08 / P0.29 | Hardware reference and effective adjacent overlay; internal module bus |
| UWB CS / IRQ | P1.06 / P1.02 | Reference/overlay; CS active low, IRQ active high |
| UWB reset / wake | P0.25 / P0.15 | Reference/overlay |
| LED3 | P0.06, active low | Native/reference and exported LED circuit |
| SWCLK / SWDIO | Module pads 2 / 3 | Module datasheet and main design |
| MCU reset | P0.18, module pad 47, nRESET | Main design; exported JP4 pin 3 carries nRESET |
| PWM1–5 | P0.23, P0.07, P0.27, P0.31, P0.30 | Hardware reference and native schematic |
| PWM6 | DW_GP3, module pad 44 | UWB GPIO, not nRF GPIO; do not create an MCU PWM pin |
| Buttons | UNKNOWN / NOT FOUND IN PROJECT FILES | No user button identified in inspected main schematic |
| Additional free GPIO | UNKNOWN / NOT FOUND IN PROJECT FILES | Unconnected module symbols do not establish accessible spare PCB GPIO |

The native schematic has BNO_RST wire endpoints `(378,405)` to `(330,405)`, matching U7 P0.22 at Y=405. U7 pad 14 is at Y=405 on its right side and connects to SDA; pad 15 is at Y=395 and connects to SCL. These records establish that the PDF pin conflicts are stale export differences, not a reason to swap the firmware pins.

Power: battery BAT+, debug 5 V, VIN_OR and VCC_3V3; TP4056 charger, DW01/FS8205A battery protection, AO3401 power ORing, SY8089AAAC regulator. The schematic heading/reference calls the regulator an LDO, but the circuit includes LX and an inductor and the component is a switching regulator. IMU VDD/VDDIO are fed through L2 with local decoupling, with a 32.768 kHz crystal. R18 pulls IMU reset high; R16 pulls BOOT high. No MCU battery-divider input was identified. MCU clock-source runtime health and watchdog enable state remain unmeasured.

UART connector selection: populate R22/R23 and omit R20/R21 per `HARDWARE.md`. Use a common ground and the debug adapter routing appropriate to that assembly. USB is disabled by the reference because VUSB is not routed as a usable supply. Do not infer working USB from the module's USB capability. The reference's P0.02 UV output has an uncertain driver circuit; leave daughterboard outputs inactive during bring-up.

## Conflicts and missing information

1. Requested BNO088 versus BNO085 in BOM, native design, sketches and reference. Product response and package marking should be recorded; do not label a successful BNO085 test as proven BNO088 compatibility.
2. Both exported main PDFs show BNO_RST on P0.02 and crossed SDA/SCL net labels. Native SchDoc and reference agree on the mapping above. Use native/reference, record which fabrication revision is fitted.
3. Adjacent DTS introductory comments give stale UART TX/RX and MISO assignments. Effective UART properties and SPI overlay agree with the reference.
4. Adjacent DTS declares LED active high; driver LED on/off helpers are also opposite to the documented active-low circuit.
5. Arduino BLE DFU and Zephyr MCUboot/BLE SMP are distinct boot chains. The installed bootloader, signing-key match, and on-device partition contents are **UNKNOWN / NOT FOUND IN PROJECT FILES**. Historical notes do not establish the current board state.
6. RF antenna delays, a validated production PHY configuration, and a tested vendor DW3110 driver are not present in this supplied folder. Adjacent custom radio code needs validation before reuse for RF tests.

## Existing implementation and defects to resolve

### IMU

`test_BNO08x.ino` uses Wire, Adafruit_BNO08x and Bluefruit. It sets I2C to 100 kHz, resets low for 50 ms, waits 200 ms, requests game rotation vectors every 50 ms and gates reads on INT. It currently halts forever on initialization failure, blocks for LED blinking, ignores report-reenable failure, and omits most required reports/statistics. Arduino pin numbers depend on the selected board variant; do not equate numeric Arduino pins with SoC GPIO without validating the variant.

### UWB / ranging

The adjacent stack calls its method single-sided two-way ranging (SS-TWR), using tag POLL and anchor RESP. It is not suitable as a validated distance baseline:

- The anchor waits with `k_usleep(100)` then transmits immediately. The tag subtracts a fixed `600 * 512` timestamp interval; neither a measured reply interval nor an enforced delayed transmit supplies that interval. Even the UUS conversion is inconsistent with the time-unit formula in the same file.
- Response validation checks message type but omits source, destination, PAN and sequence matching.
- `dw3110_read_reg()` caps the SPI transfer to a 24-byte local buffer but copies the original requested length from that buffer. Received frames can therefore cause an out-of-bounds read.
- SPI, GPIO and timestamp return values are widely ignored. Initialization accepts a partial DECA signature. IRQ is configured as input but has no interrupt callback; it is not a verified interrupt path.
- Reset uses logical GPIO values with an active-low devicetree flag; assertion/release ordering must be corrected and verified.
- RSSI/FPL are custom approximations, including fabricated fallback values, and must not be advertised as validated diagnostics.
- Radio register/header definitions and initialization/calibration sequence need comparison with the correct DW3110 vendor implementation before RF transmission.
- Tag addresses take only the low eight FICR bits. They are not guaranteed unique. There is no collision scheduler or independent per-tag table.

Reuse the platform/bus setup where verified. Do not merely wrap these routines and call resulting measurements PASS. A future multi-tag test should use an explicitly configured unique ID roster and anchor-controlled, bounded sequential exchanges, with per-tag attempts/success/failure/age statistics. Single-tag timestamp correctness must pass first.

### OTA

The local Arduino OTA sketch advertises BLEDfu; UART `r` writes GPREGRET 0xA8 through the SoftDevice and resets. It does not validate that the installed bootloader understands that value. The guide describes an OpenOCD mass erase followed by bootloader/app programming; bootloader and application HEX files referenced by that guide are absent here.

The adjacent Zephyr project provides BLE SMP image/OS management and MCUboot tools. Its documented partitions are:

| Region | Start | Size |
|---|---|---|
| MCUboot | 0x00000000 | 0xC000 (48 KiB) |
| Primary image | 0x0000C000 | 0x32000 (200 KiB) |
| Secondary image | 0x0003E000 | 0x32000 (200 KiB) |
| Scratch | 0x00070000 | 0xA000 (40 KiB) |
| Storage | 0x0007A000 | 0x6000 (24 KiB) |

These are source configuration values, not a verified dump of the connected device. The adjacent MCUboot config selects RSA signatures and scratch swap. Rollback must be demonstrated with test boot and explicit confirmation, not inferred from the layout.

`first_flash.py` creates `combined.hex` but invokes an ordinary PlatformIO upload without explicitly selecting that combined artifact; its fallback prints instructions and reports success without flashing. Do not use this as proof of bootloader installation. The UART-triggered Arduino DFU and Zephyr SMP update flows must not be mixed.

No flash region, clock, RF calibration, interrupt priority, UICR or debug protection setting was modified in this inspection.

## Baseline builds actually performed

Tools observed: Adafruit nRF52 core 1.7.0; PlatformIO core 6.1.19, nordicnrf52 platform 10.11.0, Zephyr 2.7.1, ARM GCC 8.2.1. PlatformIO reported another core version and replaced its SCons dependency during its normal build setup. Firmware source was unchanged.

From `purpleC_2`:

```powershell
arduino-cli compile --fqbn adafruit:nrf52:pca10100 'Tai-lieu-huong-dan\Tai-lieu-huong-dan\test_BNO08x'
arduino-cli compile --fqbn adafruit:nrf52:pca10100 'Tai-lieu-huong-dan\Tai-lieu-huong-dan\DWM30001C_OTA_SLOW'
```

- IMU: **FAIL**, missing `Adafruit_BNO08x.h`. No successful IMU baseline yet; dependency versions are not pinned by this project.
- OTA blink sketch: **PASS**, 99,276 bytes program storage, 14,580 bytes global RAM. Arduino's reported application limits are 286,720/106,496 bytes; those are board-package limits, not total physical flash/RAM.

From adjacent `C:\Users\iamtriet\Cyborg\Zephyr\purpleC`:

```powershell
pio run -e ranging
```

- **PASS**, 27,080 bytes flash and 7,487 bytes RAM reported. Existing build scripts emit distutils deprecation warnings. A successful build does not validate radio registers, timestamp math, or runtime safety. The OTA-enabled Zephyr configuration was not built in this inspection.

## Implementation gates and planned tests

1. Select the intended firmware base: reuse adjacent Zephyr in this workspace, or extend the supplied Arduino project. Identify the installed bootloader before enabling update/reboot-to-DFU or selecting linked offsets.
2. Establish the chosen unmodified baseline with reproducible dependency versions; fix the IMU dependency if Arduino is selected.
3. Add bounded UART CLI and board diagnostics; retain explicit NOT_TESTED, FAIL and WAITING_FOR_PEER states. Print reset reason, build/version, FICR ID, configured/runtime clock information and watchdog state.
4. Verify I2C initialization, BOOT/reset, product response and report setup with deadlines and recoverable errors. Add accelerometer, gyro, supported magnetometer and quaternion/RPY reports, fresh-data detection and throttled streaming.
5. Validate UWB bus/device ID/reset/IRQ and vendor configuration before enabling TX/RX. Correct timestamps and peer/frame validation before accepting single-tag distance results.
6. Add static per-tag state and anchor-controlled scheduling only after the single-tag hardware gate passes.
7. Validate the chosen bootloader metadata, signing and flash ranges. Use versions 1.0.0 and 1.0.1 for normal update, interrupted upload, rejected image, unconfirmed test-image rollback and recovery tests.
8. Add full diagnostics and an optional timestamped UART-to-CSV tool. Hardware tests requiring a peer cannot pass without an observed exchange.

The complete requested CLI (`info`, `imu ...`, `uwb ...`, `multitag ...`, `ota ...`, `diag`) is **not implemented yet**. The only existing local OTA UART command is `r`; do not send it before checking bootloader compatibility.

## Flash and recovery

The guide specifies ST-Link V2 with OpenOCD `interface/stlink.cfg` and `target/nrf52.cfg`. Its procedure includes `nrf5 mass_erase`, then programming bootloader and application HEX files. That destructive procedure was not run. No exact safe deployment command can be finalized until the selected bootloader, image artifacts and addresses agree. ST-Link recovery, reset wiring and protection state require physical verification. Never change protection settings to make an upload succeed.

## Troubleshooting checklist

| Symptom | Checks |
|---|---|
| No UART output | R22/R23 selection, adapter routing/ground, 115200 8N1, P0.20 TX, 3V3 rail, reset state, linked boot address |
| IMU absent | Native schematic versus stale PDF, fitted part, PS straps, 0x4A address, BOOT high, reset pull-up, SDA/SCL pull-ups and bus levels |
| IMU resets repeatedly | 3V3 transients/decoupling, BOOT/reset levels, report-restore failures, bus errors |
| UWB ID invalid | MISO P0.29, CS P1.06, reset polarity, SPI header/length, module power and checked driver errors |
| RX timeout | Matching PHY/PAN/addresses, peer actually transmitting, RX state/status handling, antenna clearance |
| Unstable distance | Correct timestamp intervals/wrap, measured reply delay, oscillator correction, antenna calibration, multipath; current SS-TWR math is not a valid baseline |
| Tags collide | Unique configured IDs, one scheduled exchange at a time, guard interval, late/stale response rejection |
| OTA rejected | Correct bootloader family, image header/version/signing key, slot bounds, complete upload |
| No boot after OTA | Test/confirm state, signature/layout match, recover known matching bootloader and app via SWD |

## Verification boundary and changed files

**VERIFIED IN SOFTWARE:** document/source inspection; native schematic resolves the stale PDF pin conflicts; successful unchanged Arduino OTA and adjacent Zephyr ranging builds; reproducible missing-library failure for the IMU sketch.

**REQUIRES PHYSICAL HARDWARE VERIFICATION:** actual fitted IMU/revision; rail/reset/clock health; UART traffic; product response and sensor reports; UWB TX/RX/IRQ and repeatable calibrated ranges; multi-tag arbitration; installed bootloader identity; image validation, update, rollback, power-interruption recovery and ST-Link recovery.

Created: this report and inspection extracts/images under `tmp/inspection/`. Modified application/driver/board sources: none. No firmware was flashed. Requested firmware implementation and acceptance tests remain outstanding.
