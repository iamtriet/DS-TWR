# One-click updates for 30 boards

## First use: initial programming is required

Each board must already contain a compatible MCUboot bootloader and this project's signed BLE application, using the same signing key as the update. A blank board cannot receive BLE OTA. The CH340/debug adapter on COM8 carries UART and does not program MCU flash with this project.

Connect each board to ST-Link/SWD once and use the existing initial-provisioning command from the project directory:

```powershell
python tools/first_flash.py --flash --adapter-location 1-5 --openocd "$env:USERPROFILE/.platformio/packages/tool-openocd/bin/openocd.exe" --scripts "$env:USERPROFILE/.platformio/packages/tool-openocd/openocd/scripts"
```

Use the preserved local key and the matching bootloader/A image. Initial provisioning replaces firmware and clears secondary/scratch swap state; storage/UICR/OTP are preserved. This requires physical SWD connections for each board. Programming 30 blank boards simultaneously requires a suitable production fixture/probes; software cannot bypass that wiring requirement. No initial programming is attempted by the batch OTA launcher.

## Enroll once

1. Power the intended 30 prepared boards, keep them within BLE range, and disconnect other BLE clients.
2. Enable the PC's Bluetooth. Install `python -m pip install -r tools/requirements.txt` if needed.
3. Double-click `Enroll_30_Boards.cmd` in the project directory.

Enrollment scans three times, requires exactly 30 matching SMP devices, checks unique PCB names/addresses, reads device names and image state, and saves `boards_ota.json`. It changes no firmware and never overwrites an existing roster. Missing or duplicate boards prevent enrollment. Power only your intended fleet during enrollment and label each physical board with its PCB name. For a smaller bench set use `python tools/ota_batch.py enroll --count 2 --roster my_two_boards.json`.

## Update with one click

Double-click **`Update_All_Boards.cmd`**. It uploads the current `build/images/validation_b.signed.bin` to the enrolled fleet. The current image is wireless-control version 1.0.7 (100 ms on / 100 ms off). No COM ports or SWD connections are needed for OTA; each board only needs power and BLE reception.

The tool verifies the signed file with the local key, then processes up to two boards concurrently from the one-click launchers:

1. Verify the enrolled GATT device name and inspect actual image state.
2. Skip boards already running the exact confirmed image hash.
3. Upload/resume with negotiated BLE packet size and four-byte flash alignment, or reuse an already staged matching image.
4. Request a test swap and reboot.
5. Reconnect and verify the exact image hash is active; observe it again after five seconds.
6. Explicitly confirm that image, reboot, and verify the confirmed image remains active.

The launcher deliberately auto-confirms after these boot/BLE checks. They do not replace visual LED, sensor, RF or application-specific acceptance testing. The board firmware itself still never auto-confirms. An unrelated unconfirmed/pending image is refused rather than confirmed or overwritten. A rejected image is never confirmed.

Each board gets up to two attempts; failures are recorded and the run continues to the remaining boards. Results are saved after every board under `build/batch-ota/<UTC-run-time>/results.json`. Exit zero means every enrolled board is UPDATED or ALREADY_CURRENT; failures return nonzero. Re-run the same launcher after interruption or correcting a failed board: actual device state determines what remains to do, so no completed-board list is blindly trusted.

The launchers use two concurrent board sessions, not a broadcast. Each board keeps its BLE connection open until reboot, and discovery is performed once before the batch. This removes repeated connection/scanning overhead and overlaps the two boards. Actual upload throughput still depends on the Bluetooth adapter and radio conditions; two parallel links do not guarantee twice the throughput. The old sequential timing is not a benchmark for this version. Keep all boards powered until the summary is displayed. If concurrent links are unreliable, use `python tools/ota_batch.py update --roster boards_ota_two.json --parallel 1`; connection reuse still applies. CLI defaults to one board at a time, while both Windows launchers explicitly select two. Reports include per-board and total elapsed seconds.

## Later firmware releases

Build and sign the intended release with the same trusted key before running the launcher. The current launcher intentionally selects `validation_b.signed.bin`; for another signed image use:

```powershell
python tools/ota_batch.py update --roster boards_ota.json --image build/images/your_release.signed.bin
```

Use unique release versions for future firmware changes; the tool compares the exact content hash, not just a version label. Roster names/addresses are operational identifiers, not cryptographic device authentication. This remains the project's bench BLE management configuration.

## Validation scope

The single-board LED upload/trial/confirmation/reboot was physically verified on 2026-10-06. Batch workflow tests cover complete update, already-current skip, resumed trial, rejected image, unrelated trial/pending refusal, and roster errors. A 30-board live run cannot be verified until the remaining boards have their initial firmware and are enrolled.

## Enrolled two-board test set

`Update_Two_Boards.cmd` uses `boards_ota_two.json`, the two devices verified during the 2026-10-06 ST-Link/BLE session. It updates only that pair with the current signed B image. This separate roster does not change the 30-board enrollment requirement.

## Connection-reuse / parallel validation (2026-10-06)

17 host tests pass, including connection reuse through reboot boundaries, request-failure cleanup, bounded concurrency and sibling cancellation. A live `--parallel 2` check reached both boards concurrently and verified their existing confirmed 1.0.3 images: 2/2 ALREADY_CURRENT in 11.55 seconds including discovery. No image was uploaded in that check; it is not a full-transfer speed measurement. The subsequent 1.0.4 transfer reached both boards, but cached Windows services prevented post-reboot verification (231.36 seconds, reported failed). Fresh GATT discovery fixed reconnection; a resumed run verified and confirmed both boards in 26.01 seconds without reupload. This recovery run is not a clean full-update benchmark. Existing signature, identity, trial-health, explicit confirmation and final reboot checks remain enabled.

On 2026-10-07 the desktop app updated both boards to 1.0.6 in **225.64 seconds**, with both UPDATED on the first attempt. This is a full transfer/confirmation/reboot measurement, excluding build time. See [desktop test evidence](CONTROL_CENTER_TEST_2026-10-07.md). Two parallel links share adapter capacity, so a 30-board duration is not established by this two-board measurement.
