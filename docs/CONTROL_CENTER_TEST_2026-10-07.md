# Desktop control validation — 2026-10-07

Implemented `Control_Center.cmd`, `tools/control_app.py`, and `tools/control_backend.py`. The desktop app supports BLE discovery, saved fleet rosters, signed OTA through the existing batch updater, USB and BLE command sessions, single/multi-tag setup, live range age/status, IMU/diagnostic controls, and session CSV/text logs.

Firmware 1.0.5 introduced the control GATT service and command dispatch on the main loop. Version 1.0.6 fixes aggregate statistics formatting: the installed nano printf does not render `ll` integer conversions. Aggregate sums of the eight 32-bit tag counters and the 40-bit radio timestamp are exactly representable as doubles and now print with zero decimal places. The RSA signing key, partition map, MCUboot, LED heartbeat, and hardware wiring remain unchanged.

## Software checks

- `python tools/run_tests.py`: portable C firmware tests and 22 Python tests pass (`tmp/control-full-tests.log`).
- Desktop smoke test: window creation, board selection, telemetry parsing, stale-state presentation, and orderly shutdown pass.
- Firmware build: 189256 bytes reported flash usage, 48192 bytes static RAM. Signed 1.0.6 image size: 194228 bytes. Image hash: `13737435dd2fcf8af8d0543084b4ee5802db336cda113ef32fcb9bac1e66175c`.

## Live command and ranging checks

On 1.0.5, both enrolled boards were updated and confirmed. The original board was initially offline; after power was restored, it updated successfully and the other board was skipped as already current. Evidence: `tmp/control-ota.log`, `tmp/control-ota-retry.log`.

- BLE `info` and `uwb status`: command acknowledgements, board identity, active confirmed firmware and multiline notifications verified (`tmp/control-ble-live.log`).
- BLE UWB initialization: DW3110 ID `0xDECA0302`; module OTP calibration valid (`tmp/control-radio-probe.log`).
- USB debug COM8: `info` and acknowledged `uwb status` passed (`tmp/control-usb-live.log`).
- Two concurrent BLE control links: PCB_76A10829 as anchor A001, PCB_866D6F7F as tag B001. Single-tag mode delivered 109 samples during a 12-second observation.
- Multi-tag scheduler: roster B001 plus deliberately absent B002. B001 had 109/109 successful exchanges; B002 had 109/109 timeouts. Both were correctly distinguished. This tests absent-peer handling, not two simultaneously present tags.
- Both radios were stopped after testing. Evidence: `tmp/control-tracking-live.log`.

Observed distances were approximately 0.23–0.35 m. Physical separation was not independently measured, so this is communication/telemetry evidence, not an accuracy calibration result. No 30-board deployment, multi-anchor position solver, or multi-present-tag radio test was performed.

## Final 1.0.6 desktop integration test

The actual desktop OTA action installed 1.0.6 on both boards, each UPDATED on its first attempt. Total batch time including discovery, upload, trial health, confirmation and final reboot was **225.64 seconds (3 minutes 46 seconds)**. Per-board elapsed times were 154.55 seconds and 217.50 seconds. Build time is excluded. Report: `build/batch-ota/20261007T035842150986Z/results.json`; app log: `tmp/control-final-ota.log`. Both boards have the exact signed 1.0.6 hash above active and confirmed.

The desktop application's own connection and tracking actions were then exercised against both live boards on 1.0.6:

- BLE tag and single-anchor configuration succeeded; the live table showed B001 at 0.2449 m, age 0.1 seconds, LIVE during the observation.
- Multi-anchor setup with B001 present and B002 absent succeeded. Corrected aggregate text: `active=1 attempts=95 success=48 timeout=47 packet_error=0 rate=8.27 Hz`.
- The same GUI connected COM8 and received acknowledged UWB status over USB.
- Both radios were stopped and test connections closed after verification.

Evidence: `tmp/control-final-tracking-run.log`, `tmp/control-final-tracking.log`, and `build/control-sessions/20261007T040249781959Z/` (CSV and log). The app's OTA session is `build/control-sessions/20261007T035841426946Z/`. These are real hardware observations; the separate GUI smoke test's synthetic stale sample is stored in its own test session and was never used as live ranging evidence.
