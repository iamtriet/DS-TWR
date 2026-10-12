# Live LED OTA test — 2026-10-06

Result: **BLE OTA upload, trial boot, explicit confirmation and subsequent reboot verified on the PCB.**

- Debug UART: COM8, 115200 8N1. No ST-Link was used in this session.
- Device: PCB_76A10829, BLE D1:3E:A9:6B:F8:8A, FICR dc6ebb5f76a10829.
- Previous image: 1.0.0, hash 5f72a6e114dbd61f9f411f9e2e14f9465eef7a131bc0e6161f3e55d0abd4a0f3.
- New image: 1.0.1, hash 087b85b860bc699c27325e6b5d29e38cfb2358c7f3bbf0675a1cce5bc727ddda.
- New signed image size: 192880 bytes. Build reported 188076 flash bytes / 47883 static RAM bytes.
- LED3: P0.06 active-low, configured for 500 ms on / 500 ms off by the main service loop. Long diagnostics can delay a toggle. Visual LED observation was not available to the agent.
- The trial first reported active=true and confirmed=false. After explicit host confirmation it reported active=true and confirmed=true.
- A second BLE-requested reset retained 1.0.1; UART reported confirmed=1 and LED heartbeat enabled at 11:52:09 Asia/Saigon.

## Compatibility fixes validated during transfer

The original host uploader split oversized requests across writes; the installed Zephyr 2.7 SMP receiver rejected the first request with rc=3 before the upload callback. The client now fits a complete SMP request in the negotiated ATT write (249 bytes on this connection).

Image management also rounds intermediate writes to four-byte flash alignment. A partial first attempt exposed this as returned offset 404 versus expected 407. The client now aligns intermediate chunks and successfully resumed using the matching file hash. All six Python host tests pass, including negotiated-size/alignment coverage.

Changed files: platformio.ini enables VALIDATION_LED_BLINK for validation_b; src/main.c services the LED; src/board.c identifies the feature; tools/ota_update.py implements request-size and alignment handling; tests/test_tools.py adds regression coverage; the runbook reflects the working procedure.

## Evidence

- build/logs/com8-ota-preflight.log
- build/logs/com8-led-ota.log
- build/logs/com8-led-ota-after-reboot.log
- build/led-ota-evidence/build-led-ota.log
- build/led-ota-evidence/led-ota-upload.log
- build/led-ota-evidence/led-ota-trial.log
- build/led-ota-evidence/led-ota-boot-state.log
- build/led-ota-evidence/led-ota-confirm.log
- build/led-ota-evidence/test-led-ota-tools.log

This session does not verify visual LED output, IMU, RF ranging, unconfirmed rollback, wrong-key rejection on hardware, or power loss during swap. Those remain separate bench tests. Historical implementation reports describe the state before this live test.
