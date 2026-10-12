# Custom PCB bench test runbook

Updated 2026-10-06. BLE LED-blink OTA, trial, confirmation and reboot were verified in [the live session](OTA_LED_TEST_2026-10-06.md). Other physical acceptance tests remain unverified. Expected outputs below are examples, not captured measurements. Preserve board revision, device ID, serial log, firmware manifest and test geometry with every bench result.

## Hardware evidence and pin map

DWM3001C contains nRF52833 (64 MHz Cortex-M4F, 512 KiB flash, 128 KiB RAM), DW3110 and a shared-bus LIS2DH12. BOM/native Altium identify the external IMU as **BNO085**, while the request says BNO088. The SH-2 product response and package marking must establish the fitted part. An I2C ACK alone is insufficient.

`HARDWARE.md` and native Altium agree on the following physical GPIO mapping. Both exported main PDFs are stale: they put IMU reset on P0.02 and cross SDA/SCL. See `INSPECTION.md` for extracted net evidence. Confirm the manufactured revision before wiring instruments.

| Function | Physical mapping / setup |
|---|---|
| UART0 TX / RX | P0.20 / P0.21; 115200, 8N1, no flow control |
| IMU SDA / SCL | P0.24 / P1.04; I2C0, 100 kHz, 7-bit address 0x4A |
| IMU INT / RESET / BOOT | P0.04 active low / P0.22 active low / P0.05 high for normal boot |
| IMU straps | PS0/PS1 low for I2C; SA0 low via R15 |
| I2C pull-ups | External R17/R19 4.7 kOhm; internal pulls disabled |
| UWB SPI3 SCK / MOSI / MISO | P0.03 / P0.08 / P0.29; mode 0, 2 MHz |
| UWB CS / IRQ | P1.06 active low / P1.02 active high with pulldown |
| UWB RESET / WAKE | P0.25 assert low then release to input / P0.15 |
| LED3 | P0.06 active low |
| SWCLK / SWDIO | DWM3001C pads 2 / 3 |
| MCU reset | P0.18, module pad 47, nRESET |
| PWM1-5 | P0.23 / P0.07 / P0.27 / P0.31 / P0.30; untouched |
| PWM6 | DW_GP3, not an nRF GPIO; untouched |
| UV output | P0.02 noted in hardware reference; drive circuit uncertain; untouched |
| User buttons / spare GPIO | Not established by inspected project evidence |

Supply chain: LiPo, TP4056 charger, DW01/FS8205A protection, AO3401 ORing, SY8089 switching regulator and VCC_3V3. The schematic's LDO heading is inaccurate. BNO supply uses L2/decoupling and its 32.768 kHz crystal; BOOT/reset have R16/R18 pull-ups. No MCU battery measurement divider was established. Power/charge LEDs are not software-testable GPIOs. USB is disabled because the reference does not route a usable VUSB supply.

For the UART adapter, populate R22/R23 and omit R20/R21 per the hardware reference. Cross adapter RX to MCU TX, adapter TX to MCU RX, and connect ground. Check logic voltage before connection. The dedicated debug adapter is CH340G; no USB console is assumed.

## Reproducible build

Run PowerShell in `C:\Users\iamtriet\Cyborg\Zephyr\purpleC_2`. Python 3.12 and PlatformIO 6.1.19 were used. The platform is pinned to nordicnrf52 10.11.0 / Zephyr 2.7.1 / GCC ARM 8.2.1. Do not substitute a development-kit board.

```powershell
python -m pip install -r tools/requirements.txt
pio run -e validation -e validation_b
python -m pip install ziglang==0.16.0
python tools/run_tests.py
```

`validation` embeds 1.0.0; `validation_b` embeds 1.0.7. Both retain the test features; validation_b also blinks LED3 with 100 ms on / 100 ms off from the main service loop. Long foreground diagnostics can delay a toggle. `include/validation.h` controls version macros; `tools/sign_image.py` checks the embedded version against its selected environment. Changing release versions requires updating that explicit signing policy too. Set `CC` to a native C compiler if using one instead of Zig for host tests.

Firmware uses one main thread for sensor/radio/CLI operations, a UART output queue/thread, and Zephyr's BLE/system workqueues. UART ISR only enqueues bytes; UWB ISR only counts edges. CLI runs between bounded radio transactions. Logs can be dropped when full and the counter is visible in `info`; this is preferable to blocking a radio exchange. Long init/diagnostic commands should be sent one at a time.

### Preserved flash map

| Region | Start | Size | End, exclusive |
|---|---:|---:|---:|
| MCUboot | 0x00000 | 0x0C000 | 0x0C000 |
| Primary | 0x0C000 | 0x32000 | 0x3E000 |
| Secondary | 0x3E000 | 0x32000 | 0x70000 |
| Scratch | 0x70000 | 0x0A000 | 0x7A000 |
| Storage | 0x7A000 | 0x06000 | 0x80000 |

Application vector table is at 0xC200 (512-byte header). Signing reserves a conservative complete 4096-byte page for the swap trailer. The board's PlatformIO application size ceiling is 200192 bytes; the signing script separately checks the actual address span, header, vectors, version, TLV hash and final signed size. No UICR data is generated. APPROTECT locking remains disabled, using the existing UICR policy. Firmware cannot unlock an already locked chip.

### Build MCUboot and images

```powershell
# Run only once; existing keys are never overwritten.
python tools/sign_image.py keygen
python tools/build_mcuboot.py
python tools/sign_image.py sign --env validation
python tools/sign_image.py sign --env validation_b
python tools/first_flash.py
python tools/validate_artifacts.py
```

Keep `keys/development.pem` private and keep the same key across bootloader/A/B. It is a local development key, not a vendor/test key compiled into the source. It and all build artifacts are ignored. MCUboot v1.9.0 is cloned at a pinned commit under `build/mcuboot-source`; no global packages are changed. The script accepts `--source` for a clean checkout at that exact commit.

MCUboot uses RSA-2048, validates primary signatures, and swaps using the existing scratch partition. `build/mcuboot/manifest.json` binds the boot HEX to its public-key fingerprint. Signing reads the freshly built HEX, not a possibly stale firmware.bin. Artifacts:

- `build/mcuboot/out/zephyr/zephyr.hex`: bootloader.
- `build/images/validation.signed.bin` and `.hex`: image A.
- `build/images/validation_b.signed.bin` and `.hex`: image B.
- Matching `.signed.json`: version, size, image hash, file hash. **Use image_hash for SMP test/confirm.**
- `build/images/combined.hex`: boot + signed A, erased primary padding/trailer, erased secondary and scratch. Storage and UICR are excluded.

### First flash / recovery via ST-Link V2

The scripts do not flash unless `--flash` is explicitly supplied. Initial provisioning replaces application/bootloader and clears OTA swap state, so preserve any firmware evidence needed from the old board first. OTP calibration and storage are not touched. Connect SWDIO, SWCLK, common ground and target-voltage reference correctly; power the PCB with its intended supply. No debug lock or mass-erase command is issued.

```powershell
python tools/first_flash.py --flash --adapter-location 1-5 --openocd "$env:USERPROFILE/.platformio/packages/tool-openocd/bin/openocd.exe" --scripts "$env:USERPROFILE/.platformio/packages/tool-openocd/openocd/scripts"
```

The exact generated command is OpenOCD with `interface/stlink.cfg`, `target/nrf52.cfg`, and `program {absolute combined.hex} verify reset exit`. The script verifies image integrity/signature, boot build fingerprint, and address bounds first. Do **not** use plain `pio run -t upload`: its unsigned app is not the combined/signed MCUboot image. No upload was run during development.

If the app fails to start, keep SWD available, connect under reset if necessary and re-run deliberate initial provisioning. Existing readback protection or electrically inaccessible SWD requires hardware-specific recovery; do not automatically mass erase or change UICR. Confirm VCC, nRESET and physical SWD wiring first.

## UART monitor and CLI

```powershell
python tools/uart_monitor.py --list
python tools/uart_monitor.py --port COM7 --interactive --log build/logs/boardA.log --csv build/logs/boardA.csv
# Optional bounded capture / paced commands:
python tools/uart_monitor.py --port COM7 --seconds 20 --command info --command "imu init" --command "imu read"
```

Choose the actual COM port explicitly. Host timestamps are UTC, not radio time. CSV columns are host_utc, tag_hex, distance_m, age_ms. `UWB,B001,1.2345,0` is an example of a new measurement; the final zero means age at publication, not validity or signal strength. Invalid/nonfinite/out-of-range CSV rows are rejected. Text diagnostics remain in the log. Interactive commands are queued at 5-second intervals; this conservative pacing prevents UART RX overflow during init. Avoid arbitrary command batches during long diagnostics.

| Commands | Meaning |
|---|---|
| `help`, `info` | Command list; firmware, IDs, clock/reset registers, memory and module stats |
| `led on`, `led off` | Drive active-low LED3; visual check still required |
| `diag` | Board/IMU/UWB local tests and honest PARTIAL/FAIL summary |
| `reset` | MCU reset; can roll back an unconfirmed trial |
| `imu init`, `imu reset` | BOOT high, reset sequence, SH-2 product/config handshake |
| `imu status`, `imu read` | Counters / fresh cached reports; stale data is an error |
| `imu stream`, `imu stop` | Enable/disable 5 Hz human output; report sampling stays 20 Hz |
| `uwb init`, `uwb status` | Reset/probe/configure; ID, OTP calibration, IRQ/events/errors |
| `uwb role anchor` / `tag` | Choose role while stopped |
| `uwb id A001`, `uwb peer B001` | Explicit 16-bit hexadecimal IDs; 0/FFFF/equal IDs refused |
| `uwb tx`, `uwb rx` | One test frame / bounded receive (hardware timeout may precede software limit) |
| `uwb range`, `uwb continuous`, `uwb stop` | One DS-TWR / repeated exchanges / stop all ranging |
| `uwb output text` / `csv` | Range output format |
| `uwb calibration TX RX POWER PG` | Volatile explicit calibration: TX/RX decimal ticks, power/PG hexadecimal |
| `multitag add B001`, `clear`, `start`, `stop`, `status`, `stats` | Explicit roster and sequential anchor scheduler |
| `ota status`, `ota test`, `ota confirm`, `ota reboot` | Inspect, request secondary trial, confirm current image, reset |

`PASS` from a configuration command means that operation succeeded, not that the PCB has passed acceptance. Timeouts yield WAITING_FOR_PEER/NO_DATA; details and raw rc distinguish stages. `CONFIG_REQUIRED` means missing IDs/calibration. Role/ID changes are refused while ranging. IDs/roles/calibration overrides/roster are volatile and must be set after every reset. No identity defaults or fabricated distances are used. Unique IDs across actual boards must be maintained by the operator.

## Single-board tests

1. Measure 3V3 and reset/BOOT levels before enabling tests. Check sensor rail ripple and oscillator assembly if startup fails.
2. Boot and capture `info`; record FICR ID, firmware version, reset register and clock state. Clock registers establish configuration/state, not frequency accuracy. Reset reason bits are raw and can contain accumulated causes.
3. `led on`, `led off`: observe LED3. GPIO return codes alone cannot prove LED assembly.
4. `imu init`: expect product/version response followed by enabled reports. Missing response is FAIL, not success inferred from an I2C ACK. Reset assert is 50 ms followed by 200 ms settling; CEVA handles SHTP/SH-2 boot and continuation packets. Commands have bounded protocol waits.
5. `imu read`, `imu stream`, move/rotate the board, then `imu stop`, `imu status`. ACC is m/s2, gyro rad/s, magnetic field uT, quaternion WXYZ, RPY degrees. Euler angles use the sensor's frame; no undocumented PCB axis remapping is applied. Required accel/gyro/quaternion samples must be younger than 1 second. Mag is displayed only while fresh; all four features are requested. Accuracy 0..3 is reported. A plausible gravity norm is a bench observation, not a software assertion.
6. Check report/error/reset/missed counters. The rate is aggregate sensor reports per elapsed time, not per-sensor Hz. Sequence gaps are modulo-256 estimates, so long outages cannot be precisely counted. Sensor reset triggers report restoration; stale streams stop and request `imu init`.
7. `uwb init`: require ID 0xDECA0302, vendor initialization/config success. Observe IRQ changes using actual TX/RX. An ID alone does not prove RF operation.
8. `diag`: local tests run, peer/OTA tests stay WAITING/SKIPPED. Overall stays PARTIAL until the separate bench matrix is completed; it never claims a full system PASS.

## UWB calibration and radio configuration

PHY: channel 5, PRF64, preamble code 9, preamble length 64, PAC8, standard IEEE SFD, 6.8 Mbps, standard PHR, SFD timeout 65, STS/PDoA off. All boards must run the same configuration and this protocol. This is not wire-compatible with the adjacent project's old SS-TWR frames or a generic vendor demo.

Production calibration is read, never written, from DWM3001C Rev F datasheet table 10:

| OTP word | Channel 5 data |
|---|---|
| 0x11 | TX power, 32 bits |
| 0x18 | PG count, upper 16 bits (lower 16 belongs to CH9) |
| 0x1A | RX antenna delay upper 16; TX antenna delay lower 16 |

Blank/invalid calibration keeps RX/diagnostics available but blocks TX/ranging. `uwb calibration TX RX POWER PG` uses explicitly supplied bench-calibrated values in RAM only. No guessed numeric calibration recipe is provided. Supply module-specific measured values and compliant TX power; reinitialize to restore OTP. RF accuracy and regional power compliance require measurement.

Hardware RX timeout is 60000 UWB microsecond units; software deadlines are 20 ms for passive tag polling, 60 ms per exchange receive, 30 ms TX completion, and at most 1000 ms in the raw API. Idle tag listening produces radio timeout counts; tag ranging attempts count only addressed polls. Timeout counters are not collision measurements. Signal strength/quality and occupancy are NOT_MEASURED, rather than invented from packet success.

## Two-board packet and single-tag range tests

Use two identical loaded boards, independently powered, stable clear line-of-sight and known separation. Ordinary BLE/OTA connections pause ranging. Firmware 1.0.5 adds explicit BLE control mode for wireless commands and range telemetry; the desktop app selects it automatically. Keep other BLE/OTA clients disconnected during RF testing. After disconnect, explicitly restart ranging.

Tag console, one command at a time:

```text
uwb role tag
uwb id B001
uwb peer A001
uwb init
uwb continuous
```

Anchor console:

```text
uwb role anchor
uwb id A001
uwb peer B001
uwb init
uwb range
uwb continuous
uwb status
uwb stop
```

For raw TX/RX, stop tag continuous, run `uwb rx` on the receiver and promptly `uwb tx` on the other board. Since the receive window is short, use synchronized host scheduling or repeat; failure to overlap windows is not proof of broken RF. Raw RX checks addressed source/type/length and prints hardware timestamp.

Ranging is four-frame asymmetric DS-TWR: POLL, RESP, FINAL, REPORT. REPORT contains the tag's poll-RX, response-TX and final-RX timestamps. The anchor combines these with its actual poll-TX, response-RX and final-TX. The 2 ms turnaround margin is not assumed in the distance formula. All timestamp deltas wrap at 40 bits. Stale/wrong ID/type/token/version/length frames are rejected; intervals over 0.5 s, negative/nonfinite distances or distances above 1000 m are rejected. Antenna corrections are installed in the hardware timestamp path.

Record distance mean/min/max, success fraction and timeout/error counts at several measured separations and orientations. Compare to reference distance and calibration requirements. Software supports the exchange; it does not prove accuracy, line-of-sight detection or NLOS compensation.

## Multi-tag test

Use one anchor and at least two tags (three tags gives four boards). Each tag uses its own ID (B001, B002, B003) and peer A001, then `uwb init`, `uwb continuous`. At the anchor:

```text
uwb stop
uwb role anchor
uwb id A001
uwb init
uwb output csv
multitag clear
multitag add B001
multitag add B002
multitag add B003
multitag start
multitag status
multitag stats
multitag stop
```

`MAX_TAGS` defaults to 8 in `include/tag_table.h` and can be overridden at build time. Explicit roster avoids discovery collisions. One exchange is in flight; a 20 ms guard follows completion/timeout, then round robin advances. Each tag tracks attempts, success, failures, timeout, packet errors, latest/min/max/sum and last-seen age. An entry is active only after successful ranging within 3000 ms. Statistics persist across stop/start and rates include elapsed idle time; clear the roster for a new measurement session. Disconnect/power down one tag: its entry must age inactive while others continue. Duplicate IDs are rejected locally; duplicated IDs on separate physical tags must be corrected by the operator.

This is one anchor polling a roster, not a synchronized multi-anchor network, localization solver or security protocol. BLE and software delays affect rates; benchmark on the real PCB. Saturation is observable as errors/timeouts/log drops, not a fabricated utilization percentage.

## BLE OTA: A/B trial, confirmation and rollback

BLE name is `PCB_<low FICR device ID>`; SMP service is 8d53dc1d-1db7-4cd3-868b-8a527460aa84. The bench configuration has no pairing/authentication. Signed boot images prevent an untrusted firmware image from running, but unauthenticated management can upload, reset, or request state changes; restrict this build to the test bench.

```powershell
python tools/ota_update.py scan
python tools/ota_update.py state --address "BOARD_ADDRESS"
python tools/ota_update.py upload --address "BOARD_ADDRESS" --image build/images/validation_b.signed.bin
$trialHash = (Get-Content build/images/validation_b.signed.json | ConvertFrom-Json).image_hash
python tools/ota_update.py test --address "BOARD_ADDRESS" --hash $trialHash
python tools/ota_update.py reset --address "BOARD_ADDRESS"
```

Replace BOARD_ADDRESS with the discovered exact device address. The client sends bounded CBOR SMP messages, fits each entire request within the negotiated BLE write limit, aligns intermediate image chunks to four bytes, and reassembles response notifications, checks response identity and upload offsets, and waits up to 30 seconds per response. Restart upload after link errors; an offset-zero request with full-file SHA256 can resume a matching upload reported by the target. Never test a partially uploaded image.

1. Initially boot signed A (1.0.0), inspect UART `ota status`, run local checks, then **`ota confirm`**. This establishes a confirmed rollback baseline.
2. Upload B with the command above. Upload alone does not mark it pending. UART reports upload start/progress/completion; metadata is not signature verification.
3. Request B test by its **image_hash**, then reset. MCUboot verifies signature and swaps. Capture UART version 1.0.7 and `confirmed=0` before continuing. A test image is **never automatically confirmed**.
4. Rollback test: do not confirm B. Run UART `ota reboot` (or power cycle). Expect A 1.0.0 after revert. Actual swap/revert is a hardware test, not inferred from build success.
5. Confirmation test: upload/test B again; boot B, complete required checks, then UART `ota confirm`, or `python tools/ota_update.py confirm --address "BOARD_ADDRESS" --hash $trialHash`. The host only confirms the matching active image. Reboot and verify B stays active/confirmed.
6. Interrupted transfer: with a confirmed running baseline, run upload with `--stop-after 8192`. Expected host exit code 2, no trial request, and current image still boots. Re-run a full upload and verify completion.
7. Invalid signature: begin on confirmed A (so the B content hash does not also identify the active slot); generate a separate throwaway key with `python tools/sign_image.py keygen --key keys/wrong-test.pem`, sign B with `python tools/sign_image.py sign --env validation_b --key keys/wrong-test.pem --output-dir build/negative`, then upload that image. It has a valid content hash but the wrong signing key. Request trial using `build/negative/validation_b.signed.json` image_hash and reboot: the trusted bootloader must retain/revert to the valid baseline. This deliberately tests bootloader rejection; never rebuild MCUboot with the throwaway key. For a corrupted-byte file the host should reject SHA256 before upload.

UART alternatives after BLE disconnect: `ota test` checks secondary metadata and requests a test swap; `ota reboot` resets. UART flash operations run on the same workqueue as mcumgr, preventing concurrent erase/trailer writes. A radio busy check rejects a too-early upload; the host waits one second after connection to let bounded radio work finish. `ota status` reports compiled map/headers/state, **not proof of the identity of an unknown installed bootloader**.

No watchdog forces a hung trial to reset automatically. A power cycle/external reset is the recovery trigger. Power-loss resilience during an actual swap and corrupt-image rejection must be tested on hardware with SWD recovery available. The original software validation exercised neither physical OTA nor power interruption. The linked 2026-10-06 session subsequently verified BLE upload/trial/confirmation/reboot; power interruption and rollback remain untested.

## Troubleshooting / evidence to collect

| Symptom | Next checks |
|---|---|
| No UART | 3V3/common ground, COM port, 115200 8N1, TX/RX crossing, R22/R23 vs R20/R21, bootloader/signature, SWD |
| BNO no product response | Correct native pin mapping, 0x4A, PS0/PS1/SA0, RESET release, BOOT high, pull-ups, power/crystal, logic-analyzer START/ACK/INT |
| BNO repeated resets | Supply dips/ripple, reset/BOOT noise, crystal and decoupling; preserve reset and comm counters |
| UWB ID wrong/zero/FFFF | SPI waveform/CS/mode, reset release, wake, supply and module assembly; a vendor error is not a peer timeout |
| UWB TX blocked | Read OTP output; missing calibration or unset/equal IDs; do not inject guessed antenna delays |
| UWB timeouts | Both IDs/roles, matching PHY/firmware, peer listening, BLE disconnected, antennas/geometry and calibration |
| Unstable distances | Known spacing, both antenna delays, reflective/NLOS environment, orientation, packet errors and timestamp rejects |
| IRQ absent despite SPI | IRQ wiring P1.02, polarity/pulldown, status clearing and edge delta during actual TX/RX |
| OTA not discoverable | UART advertising rc, BLE adapter/permissions, name/FICR, previous connection |
| OTA fails upload | Exact address, response rc, flash space, interrupted link; resume/restart; never request a partial trial |
| Trial stays on A | Wrong key/signature/version/image, missing pending request; compare manifests and boot build evidence |
| Trial returns to A | Expected if unconfirmed; capture B's version and confirm deliberately only after checks |
| Build signing fails | Rebuild selected environment; verify current HEX, embedded version, key, header/vector/slot bounds |

## Acceptance record

For each row record PASS/FAIL/NOT_TESTED and attach evidence: power/rails; SWD flash/readback; UART bidirectional traffic; LED visual; IMU product identity, motion reports/axes/accuracy/reset recovery; DW3110 ID/reset/IRQ/TX/RX; two-board range accuracy across reference distances; multi-tag rate/fairness/dropout; OTA A baseline, B trial, rollback, confirmation, interrupted transfer, invalid signature, power loss and SWD recovery. Software regression logs cannot fill those physical rows with PASS.
