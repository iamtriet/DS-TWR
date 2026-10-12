# IMPLEMENTATION COMPLETE

Software implementation completed 2026-10-05. **No physical board was flashed or tested.** Build and offline test success are not hardware acceptance. Use [HARDWARE_TEST.md](HARDWARE_TEST.md) for executable bench procedures and expected results.

## 1. Hardware discovered

The custom PCB uses DWM3001C: nRF52833 Cortex-M4F, 64 MHz, 512 KiB flash, 128 KiB RAM, DW3110 UWB and internal LIS2DH12. External U8 is **BNO085 in the BOM/native schematic**, although the request names BNO088. Physical fitted identity remains unverified. Power circuitry includes LiPo/TP4056, DW01/FS8205A, AO3401 and SY8089 switching regulation. The exported PDFs contain stale IMU pin assignments; native Altium and HARDWARE.md agree. Full evidence is preserved in [INSPECTION.md](INSPECTION.md); its initial pending-implementation status is historical.

## 2. Project architecture

A self-contained PlatformIO/Zephyr 2.7.1 application was added to this hardware-design directory, adapting the adjacent purpleC architecture. Drivers and CLI are owned by the main thread, UART output has a bounded queue/thread, and BLE management uses the system workqueue. GPIO/UART ISRs do minimal work. The app has independent board, IMU, radio, ranging, multi-tag, OTA, diagnostics and host-tool modules. There is no Git repository here; no commits or worktrees were created.

## 3. Pin mapping

| Interface | Mapping |
|---|---|
| UART TX/RX | P0.20/P0.21, 115200 8N1 |
| IMU SDA/SCL | P0.24/P1.04, 100 kHz, 0x4A, external pull-ups |
| IMU INT/RESET/BOOT | P0.04/P0.22/P0.05 |
| UWB SCK/MOSI/MISO | P0.03/P0.08/P0.29 |
| UWB CS/IRQ/RESET/WAKE | P1.06/P1.02/P0.25/P0.15 |
| LED3 | P0.06 active low |
| SWCLK/SWDIO | Module pads 2/3 |
| MCU nRESET | P0.18, module pad 47 |

PWM1-5, DW_GP3/PWM6 and uncertain UV circuitry remain untouched. No user button or battery ADC path was established. The runbook records rails, straps, resistor selection and schematic conflicts.

## 4. Existing components reused

Reused the custom-board definition, existing UART/UWB pin map, conservative 2 MHz SPI, BLE SMP/MCUboot approach and exact primary/secondary/scratch/storage map. The adjacent project sources were not modified. Its custom radio driver/SS-TWR and signing/first-flash bugs were not carried forward: radio operations now use the Qorvo driver and measured six-timestamp DS-TWR. CEVA SH-2/SHTP implements the sensor protocol. MCUboot is pinned to v1.9.0. [Vendor provenance](../vendor/PROVENANCE.md) records revisions, licenses and patches.

## 5. Files created

Major files and purposes (paths relative to this project):

| Files | Purpose |
|---|---|
| `platformio.ini` | Pinned platform, custom board, A/B versions and ST-Link selection |
| `.gitignore` | Keep generated images, keys, logs and build caches out of future commits |
| `boards/dwm3001c_custom.json` | MCU/RAM definitions and conservative application size ceiling |
| `boards/arm/dwm3001c_custom/*.dts`, `Kconfig*`, `*_defconfig`, `board.cmake` | Physical board, fixed partitions, SoC configuration and OpenOCD runner |
| `zephyr/app.overlay` | IMU I2C and UWB SPI/control-node configuration |
| `zephyr/prj.conf` | UART, GPIO/I2C/SPI, queues/stacks, BLE SMP and MCUboot support |
| `zephyr/CMakeLists.txt` | Build application, dependency includes, math library |
| `include/validation.h` | Public module API, firmware version and board constants |
| `src/main.c` | Startup and cooperative module service loop |
| `src/console.c` | UART IRQ input, output queue, bounded parser and complete command dispatch |
| `src/board.c` | LED, device/reset/clock/memory information and operation statuses |
| `src/imu.c` | BNO08x transport, boot/product handshake, report configuration/cache/stream/recovery |
| `src/validation_math.c`, `include/validation_math.h` | Checked quaternion-to-RPY and 40-bit DS-TWR mathematics |
| `src/radio_port.c` | Zephyr SPI/GPIO, reset/wake, IRQ counts and sticky transfer faults |
| `src/radio.c`, `include/radio.h` | Qorvo initialization, production calibration, bounded TX/RX and BLE arbitration |
| `src/uwb_frame.c`, `include/uwb_frame.h` | Byte-level frame serialization, protocol/ID/token/length validation |
| `src/ranging.c`, `include/ranging.h` | Actual anchor/tag four-message DS-TWR exchanges and error propagation |
| `src/uwb_app.c`, `include/uwb_app.h` | Roles/IDs, CLI, continuous service, range statistics and CSV output |
| `src/tag_table.c`, `include/tag_table.h` | Fixed-capacity unique roster, per-tag statistics and wrap-safe ageing |
| `src/multitag.c` | Sequential polling scheduler and roster/status commands |
| `src/ota.c` | BLE name/service/events, bank metadata, trial/confirm/reset and serialized flash jobs |
| `src/diagnostic.c` | Local test sequence with honest PARTIAL/FAIL and peer-test deferrals |
| `src/vendor/*.c` | One compilation wrapper for each used CEVA/Qorvo source |
| `vendor/sh2`, `vendor/dw3000`, `vendor/PROVENANCE.md` | Pinned driver sources, licenses/notices and documented adaptations |
| `include/mcuboot_zephyr27_compat.h` | Matching void prototype absent from Zephyr 2.7's public timer header |
| `tools/build_mcuboot.py` | Pinned, clean-source custom-board bootloader build and key/HEX manifest |
| `tools/sign_image.py` | Local key generation; fresh HEX/version/vector/size validation; signing/verification |
| `tools/first_flash.py` | Bounded combined image; explicit optional ST-Link programming with verify |
| `tools/ota_update.py` | BLE discovery, bounded SMP transfer, state/trial/confirm/reset/interruption utility |
| `tools/uart_monitor.py` | Explicit COM selection, timestamped logs, CLI input and validated range CSV |
| `tools/requirements.txt` | Pinned host dependencies |
| `tools/run_tests.py` | Compile portable C sources and run Python unit tests |
| `tools/validate_artifacts.py` | Check current signed payloads, maps, protection settings and bad-signature rejection |
| `tests/test_core.c`, `tests/mocks/validation.h` | Actual C math/frame/tag tests and mocked anchor/tag transaction tests |
| `tests/test_tools.py` | Host CSV, SMP fragmentation/identity, image integrity and upload-offset tests |
| `README.md`, `docs/HARDWARE_TEST.md`, `docs/IMPLEMENTATION_REPORT.md` | Entry point, full bench runbook and this completion report |
| `docs/INSPECTION.md` | Preserved original hardware inspection/baseline evidence |

Generated outputs include MCUboot, signed A/B, combined HEX, per-image JSON manifests and `build/verification.json`. Private development keys are local ignored files, not deliverable source.

## 6. Files modified

Existing hardware designs, manufacturing exports, HARDWARE.md and supplied Arduino sketches were preserved. Within the new firmware tree, copied board definitions were corrected for LED polarity, stale comments, application size reporting and OpenOCD paths. CEVA gained a bounded fallback operation timeout. Qorvo gained sticky fault exits/bounded calibration waits and removal of unused ioctl-only code; notices are retained. The original inspection document was preserved separately before HARDWARE_TEST.md became the completed runbook. No global framework packages were patched.

## 7. UART implementation

115200 8N1, physical P0.20/P0.21, no flow control. ISR queues bytes, a bounded main-thread parser dispatches commands, and a separate writer drains output. Overlong input and RX overflow are discarded through newline; dropped/truncated logs are counted. The complete help/info/LED/IMU/UWB/multi-tag/OTA/diagnostic/reset command set is live code. Blocking sensor initialization is bounded; commands are serviced between ranging exchanges.

## 8. BNO088 implementation

Implemented the BNO08x family protocol using CEVA SH-2/SHTP, without claiming the fitted part is BNO088. BOOT/reset and I2C/INT use verified design mapping; product/version/configuration responses are required. Acceleration, calibrated gyro, calibrated magnetic field and rotation vector are requested at 20 Hz, with 5 Hz output. Quaternion validation/normalization produces RPY; stale required samples are refused. Status exposes communication/invalid/sequence/reset/rate counters; spontaneous reset restores reports. Actual BNO088 compatibility and fitted device identity require hardware testing.

## 9. UWB initialization

Uses Qorvo 08.02.02 with bounded reset/probe/configure paths, expected DW3110 ID 0xDECA0302, hardware IRQ edge counting and sticky SPI faults. PHY is CH5/PRF64/code9/preamble64/PAC8/6.8 Mbps/standard SFD and PHR, STS/PDoA off. Module OTP supplies CH5 TX power, antenna delay and PG count. Missing/invalid calibration disables TX until explicit measured volatile values are supplied. OTP is never written. TX/RX return hardware/error/timeout outcomes rather than placeholder success.

## 10. Single-tag ranging

Both anchor and tag implement POLL/RESP/FINAL/REPORT. All six ranging timestamps are actual hardware timestamps; REPORT carries the tag's three values. DS-TWR uses wrap-safe 40-bit differences and rejects stale intervals, wrong identities/tokens/types/lengths, negative/nonfinite and implausibly large ranges. Role and IDs are explicit. One-shot/continuous modes, packet tests, statistics and CSV are implemented. Accuracy remains unmeasured.

## 11. Multi-tag tracking

One anchor polls a configurable roster (default maximum eight) sequentially with a guard interval. Duplicate/reserved/local IDs are refused. Every tag has attempts/success/failure/timeout/packet-error counts, latest/min/max/mean distance and last-seen age. No successful observation means NOT_MEASURED; last success older than three seconds means inactive. Losing one tag does not prevent polling the rest. Radio utilization/collision count is NOT_MEASURED, since RX errors alone cannot identify collisions.

## 12. OTA implementation

Built MCUboot with RSA-2048 and primary validation using the exact preserved 48/200/200/40/24 KiB partition layout. Application provides BLE SMP, local device naming, upload event logs, metadata inspection and explicit test/confirm/reset. There is no automatic trial confirmation. BLE connections gate radio operations and upload waits for radio quiescence. UART flash jobs share the mcumgr workqueue, preventing concurrent mutation. Host tooling handles signing, integrity/bounds, exact board selection, packet fragmentation/reassembly and checked offsets. The runbook includes successful trial, explicit confirmation, rollback, interrupted upload, wrong-key rejection and SWD recovery procedures. OTA on the real PCB is untested.

## 13. Diagnostic framework

`diag` reports UART peripheral readiness, LED drive result, IMU handshake/fresh reports, UWB communication/configuration, optional configured TX/IRQ delta and primary image metadata. Host UART loopback, clock accuracy, visual LED result, peer ranging and OTA swap tests are not inferred. Overall is PARTIAL if local operations succeed, FAIL if an attempted local operation fails; it cannot falsely report full hardware PASS without peers.

## 14. PC UART utility

`tools/uart_monitor.py` lists/selects serial ports, timestamps console lines, saves logs, accepts paced commands and extracts finite bounded range rows to CSV. Interactive mode and bounded-duration capture are supported. BLE OTA is a separate utility; neither tool automatically selects or flashes a board.

## 15. Build result

| Target / check | Result |
|---|---|
| Application A 1.0.0 | PASS; 187848 bytes reported flash, 47883 bytes static RAM |
| Application B 1.0.1 | PASS; same sizes |
| Signed A / B | Each 192656 bytes including header/TLV; 8048 bytes free before reserved 4096-byte trailer page |
| MCUboot | PASS; 23344 bytes flash / 23456 bytes RAM within 48 KiB / 128 KiB |
| Portable C regression | PASS: frame bounds, timestamp wrap/skew, quaternion, tag scheduling/ageing, mocked anchor and tag success/error paths |
| Python unit tests | 5 tests PASS, with multiple malformed/boundary cases |
| Artifact checks | PASS: signatures, current HEX payload, fixed partitions, no debug-lock setting, no storage/UICR HEX addresses |
| Corrupted RSA signature | Rejected by offline verifier while body hash remained valid |

No application C compiler warnings remain. Tooling still emits upstream Python distutils deprecation notices. Boot configuration may report upstream logging settings disabled by the minimal overlay, and bundled GDB may warn about its index-cache path; these do not prevent the successful build. The missing timer prototype was corrected with a local compatibility header. Build logs are `tmp/build-final.log`, `tmp/build-mcuboot.log`, `tmp/host-tests.log`; durable artifact results are in `build/verification.json`. Static RAM does not establish worst-case stack use on hardware.

## 16. Build command

```powershell
python -m pip install -r tools/requirements.txt
pio run -e validation -e validation_b
python -m pip install ziglang==0.16.0
python tools/run_tests.py
python tools/sign_image.py keygen  # Skip when the local key exists.
python tools/build_mcuboot.py
python tools/sign_image.py sign --env validation
python tools/sign_image.py sign --env validation_b
python tools/first_flash.py
python tools/validate_artifacts.py
```

## 17. Flash procedure

Connect powered PCB and ST-Link V2 to SWDIO/SWCLK/GND/reference voltage. `first_flash.py` prepares a signed combined image by default; explicit `--flash --openocd PATH --scripts PATH` runs OpenOCD program/verify/reset. This replaces boot/application and clears secondary/scratch, preserving storage/UICR/OTP and leaving SWD protection unchanged. The full Windows command and recovery process are in the runbook. Plain PlatformIO unsigned upload must not be used for this MCUboot installation. **No flash command was executed against a board.**

## 18. Test commands

Start `python tools/uart_monitor.py --port COM7 --interactive` using the actual port, then `help`, `info`, `led on/off`, `imu init/read/stream/status/stop`, `uwb init/status`, and `diag`. For two boards set anchor A001/peer B001 and tag B001/peer A001, start tag continuous, then anchor range/continuous. For multi-tag add B001/B002/B003 at the anchor and start polling. For OTA confirm A, upload B, request test using image_hash, reset, then test either explicit confirmation or unconfirmed rollback. Complete command sequences and acceptance evidence are in HARDWARE_TEST.md.

## 19. Known limitations

This is bench firmware: legacy Zephyr/MCUboot architecture preserved, unauthenticated BLE management, no watchdog-triggered trial reset, no multi-anchor synchronization/localization/NLOS solution, no measured RF occupancy or collision detector. Calibration overrides/IDs/roles/roster are volatile. IMU product family discrepancy, PCB axis orientation and actual fabrication revision require verification. Logging can drop under load; queues/counters expose this. Only about 8 KiB signed-image headroom remains. UWB/IMU coexistence, worst-case stacks, BLE transfer behavior and performance require bench measurement. The old installed bootloader cannot be identified from source files alone.

## 20. VERIFIED BY SOFTWARE

Both complete applications and the custom-board bootloader compile/link. Images fit preserved partitions, match current HEX payloads and expected A/B versions, and verify under the local signing key. Combined HEX excludes storage/UICR and clears OTA swap state. Bad-signature rejection was exercised offline. Actual portable C frame/math/tag/anchor/tag code and Python host checks pass. No app implementation placeholders remain. This establishes software construction and selected logic properties, not electronics or on-device boot behavior.

## 21. REQUIRES PHYSICAL PCB TEST

Power/rail/reset integrity; SWD programming/readback/recovery; bidirectional UART and resistor routing; LED visual operation; IMU identity/boot/INT/motion reports/axes/accuracy/reset recovery; DW3110 SPI/reset/IRQ/TX/RX; antenna calibration and two-board distance accuracy; multi-tag fairness/rates/dropout; BLE discovery/upload; signed B trial, rollback, confirmation, interrupted transfer, invalid-image rejection, power loss during swap and external-reset recovery. Record these as NOT_TESTED until measured. The runbook provides the acceptance checklist without fabricated PASS results.
