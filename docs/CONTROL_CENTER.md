# Board Control Center

For the new **UWB 3D Tracking** tab, mobile-node simulation, XY top-down view, binary UART gateway and candidate firmware, use the [UWB 3D runbook](UWB3D_TRACKING.md). Existing survey/cooperative controls are retained under **Legacy 4-anchor cooperative**.

Double-click `Control_Center.cmd` in the project folder. It runs the local Python/Tk desktop app and scans for nearby boards. Dependencies are the existing `tools/requirements.txt`; install them with `python -m pip install -r tools/requirements.txt` on a new Windows computer. Python must include Tkinter. No server or account is required. See [original control tests](CONTROL_CENTER_TEST_2026-10-07.md) and [four-anchor hardware results](FOUR_ANCHOR_TEST_2026-10-08.md).

## Discover and update

1. Power the intended boards and click **Scan BLE boards**. Select individual rows with Ctrl/Shift, or **Select all**.
2. Save a selected roster to retain fleet membership. Load it later to update boards even if some are currently offline; offline boards are reported as failed, not silently omitted.
3. Choose a **signed `.bin`** image, then **Update selected boards**. The confirmation names the targets and version. Signing uses the existing project key; this app installs firmware, it does not compile arbitrary source code.
4. Watch progress in the status bar and Console. The uploader verifies the signature, board name, exact image hash, trial boot, explicit confirmation, and final reboot. It saves per-board reports under `build/batch-ota/`.

The current image is 1.0.13, with LED3 at 100 ms on / 100 ms off and the USB/BLE control features retained. Existing OTA-capable boards can receive it over BLE. The app supports a roster of 30 OTA targets, updated with one or two simultaneous connections; this is not broadcast OTA.

OTA closes control connections. Ranging stops during OTA, and volatile radio settings must be configured again after the update. Keep boards powered until verification finishes. The app prevents closing during an active operation. Two simultaneous BLE control links are supported by this host app; USB ports can also be connected.

## Program a new board using ST-Link

1. Connect the powered board to ST-Link (SWDIO, SWCLK, ground and voltage reference).
2. Open **New Board / ST-Link**, click **Refresh ST-Link ports**, and explicitly choose the intended USB probe. The selector uses USB bus/port locations, not COM numbers. The probe used in this test appears as **ST-Link USB 1-5 (0483:3748)**; Windows calls that physical connection `Port_#0005.Hub_#0002`. Locations can change if the probe is moved to another USB socket or hub.
3. Select the signed application `.bin`; the image selection is shared with the OTA page. The trusted MCUboot image and signing-key manifest come from this project's existing build artifacts. OpenOCD paths are filled from the PlatformIO installation and can be browsed manually if needed.
4. Click **Program new board** and check the selected USB port and firmware version in the confirmation. Keep the probe attached to the same target throughout.
5. The app checks for nRF52833/512 KiB flash, backs up all flash and UICR, rechecks the unique target ID, programs and verifies MCUboot plus the signed application, and compares storage/UICR afterward. It replaces existing application and OTA swap slots. It does not mass-erase, write OTP or change debug protection. A locked target fails rather than invoking recovery automatically.
6. After success, use **Scan BLE boards** to find the new `PCB_...` identity, then save it to your fleet roster. Future releases use OTA. SWD verification confirms programmed bytes; BLE inspection confirms the application actually booted.

Each attempt saves evidence under `build/provisioning/<UTC timestamp>/`: original flash/UICR, target FICR, exact programmed HEX, OpenOCD logs, preserved-region readbacks, and (on success) `result.json`. A disconnected selected probe causes an error; the app never substitutes a different ST-Link automatically. Probe selection is cleared on refresh so an old selection cannot silently follow a changed device list.

CLI equivalent (replace the location with the one returned by discovery):

```powershell
python tools/stlink.py
python tools/first_flash.py --flash --adapter-location 1-5 --image build/images/validation_b.signed.bin --openocd "$env:USERPROFILE/.platformio/packages/tool-openocd/bin/openocd.exe" --scripts "$env:USERPROFILE/.platformio/packages/tool-openocd/openocd/scripts"
```

## USB and wireless tracking

**USB:** Select the connected debug adapter's COM port and click **Connect USB** (115200 baud). An ST-Link alone does not provide this UART console.

**Wireless:** Select one scanned board, then **Connect selected BLE board**. Firmware 1.0.5 or newer is required. A connection checks the board name, subscribes to telemetry, then selects control mode. Ordinary OTA connections still pause the radio. Windows GATT discovery is forced fresh to avoid stale services after firmware upgrades.

Open **Tracking & Sensors**, select a connected board, and set its role, local ID, and peer IDs:

| Board role | Local ID example | Peer field example |
|---|---|---|
| Tag | B001 | A001 |
| Single anchor | A001 | B001 |
| Multi anchor | A001 | B001,B002,B003 |

The examples are identities, not calibration values. Use unique IDs across physical boards. Configure/start each tag first, then the anchor. A configured tag continues responding after its control link is disconnected, allowing tags to be prepared sequentially; retain the anchor connection for distance telemetry. Configuration is volatile across resets. When changing a board between conflicting existing IDs, the firmware rejects the change; reset/reconfigure deliberately rather than relying on automatic identity reassignment.

**Configure & start** stops existing ranging, applies IDs/role, initializes the UWB radio, enables CSV output, and starts the selected mode. Each step awaits the firmware response. Any rejected command aborts the sequence and attempts to stop ranging. A timeout requires reconnection so a late acknowledgement cannot be mistaken for the next command's success.

Valid module calibration is required. If initialization or ranging reports `CONFIG_REQUIRED`, inspect the console and the hardware runbook. The app never supplies guessed calibration values. An explicitly measured `uwb calibration TX RX POWER PG` override may be entered in the Console, followed by manual start commands without reinitializing (reinitialization reloads OTP).

Live readings show reporting board, tag ID, distance, and age; after three seconds they become **STALE**. The app does not invent positions or distances. The firmware provides DS-TWR ranges; the host provides position estimation in the multi-anchor tabs. Multi-tag firmware currently supports eight tags per anchor, independently of the 30-board OTA fleet capacity. The Four-anchor Tracking page adds host-scheduled ranging for four anchors and up to eight tags. Thirty simultaneous tags require further development and validation.

## Single-anchor range view

1. Scan in **Boards & OTA** and select the boards to use together (one anchor plus 1–8 tags).
2. Open **Single-anchor ranges** and click **Use selected boards**. Choose the anchor from the drop-down. The preview shows each tag's board identity and its assigned UWB ID. Anchor ID is A001; tags receive B001 onward in board-name order.
3. Click **Start fleet** and review the displayed board/ID mapping. The app configures each tag over BLE, disconnects it while leaving it listening, then configures the anchor and keeps only the anchor's control link open. It checks command acknowledgements and reads existing IDs before safely changing roles/IDs. No calibration values are invented.
4. Watch the per-tag table, latest-range indicators and 60-second range-history plot. Colors match across the table and plots. A never-seen tag is WAITING; a sample older than three seconds is STALE. Old samples are not plotted as fresh, and history lines break across stale gaps.
5. **Stop fleet** stops the anchor and each configured tag. Closing the app or starting OTA/SWD programming also stops the fleet first. If a board cannot be reached, stop reports the unresolved board and retains the fleet for retry. Stop the fleet before changing membership, starting individual radio diagnostics, or opening another control connection.

The sample rate is the number of received readings over the last five seconds. Success percentage and timeout totals come from the anchor's firmware counters, polled every five seconds, and cover the current ranging session. Loss of Bluetooth telemetry and loss of UWB reception both produce stale samples; STALE does not diagnose the cause by itself. The view provides distances, not bearings or XY coordinates.

The exact board/ID mapping is saved as `tracking-fleet.json` beside each session's CSV and console log. Range history is bounded in memory. Existing logs and CSV remain available after the fleet stops.

## Diagnostics and evidence

The sensor controls provide IMU initialization, individual reads, streaming, and stop. The Console accepts the existing diagnostic commands and records responses. Firmware management commands are routed through the OTA workflow rather than the raw console. A completed diagnostic command does not mean all diagnostic checks passed; read the board's reported results.

Each app session writes `console.log` and `ranges.csv` under `build/control-sessions/<UTC timestamp>/`. CSV contains host UTC, reporting board, tag ID, measured distance, and source age. No simulated samples appear in normal operation.

This is the existing bench BLE security configuration: board names are operational identifiers; commands are not authenticated. Firmware OTA retains RSA signature verification. Production wireless access control is separate work.

## Transport details

Control service UUID: `8d53dc20-1db7-4cd3-868b-8a527460aa84`; write characteristic ends in prefix `8d53dc21`, notifications `8d53dc22`. Host subscribes, writes `MODE CONTROL`, then sends one printable command per write (under 160 bytes). Notifications form a newline-delimited text stream and may span BLE packets. `@DONE <rc>` terminates each remote command; zero means the command returned successfully, not that a physical measurement was obtained. Commands are queued to the firmware's main loop rather than executed inside Bluetooth callbacks.

SMP image upload locks control out before flash writes and waits for ranging/radio work to stop. Reconnect to return to control mode. Control and OTA are mutually exclusive in the desktop workflow.


## Four anchors with one or more tags (firmware 1.0.11)

1. Update all participating boards to the supplied signed image.
2. Select 5–12 boards on **Boards & OTA**. Open **Four-anchor Tracking** and click **Load selected boards**.
3. Choose any four distinct boards in the A001–A004 selectors. Remaining selected boards become B001–B008 tags, ordered by board name. All boards use the same firmware and may change roles after stopping the fleet.
4. Click **Start four-anchor ranging**. The app applies roles, initializes the radios and BNO085 sensors, and retains BLE control connections. LED3 is automatically solid for anchors and blinking for tags. LED2 is charger controlled.
5. The main view is a top-down XY map, with square anchors, colored circular tags, 15-second trails, zoom/pan controls, and a tag-status panel. Open **Measurement details** for raw distance, displayed distance, source, sample age, per-link rate and failed attempts. Raw samples are always retained in the CSV. IMU freshness and calibration state appear below the table. The command round-trip measurement includes BLE and firmware execution, not just radio time of flight.
6. **Stop fleet** stops every participant and IMU streaming. OTA, initial programming and closing the app also stop the fleet first. Failed stops remain available for retry.

Anchors issue one addressed measurement at a time and visit each tag before moving to the next anchor. Persistent connections remove repeated BLE discovery and connection delays. A recoverable radio error gets at most one retry after 25 ms; all failed attempts remain visible. Transport loss or fatal radio errors stop the scheduler. The connected fleet is limited by the PC Bluetooth adapter; six boards are the current hardware test target, while larger fleets need separate validation. This is not broadcast or synchronized ranging. Absolute zero latency is physically impossible; use measured age and rate to assess performance.

The BNO085 now waits for asynchronous report configuration to become effective, enables dynamic calibration, and reports gravity-removed acceleration and angular speed with freshness and calibration quality. The host only smooths when both participating IMUs are fresh and sufficiently calibrated, five consecutive motion reports indicate quiet conditions, and the UWB innovation is small. Moving, stale or uncertain cases use raw ranges immediately. The filter has a 150 ms time constant, with no look-ahead buffer. It can reduce stationary jitter; it does not remove systematic antenna bias or prove absolute accuracy. Constant velocity is not observable from an accelerometer alone, so UWB change also gates smoothing.

### Surveyed position map and accuracy

Click **Set anchor positions** to enter measured X, Y and height Z for all four anchors directly in the app. Coordinates refer to UWB antenna reference points. Use **Apply and save** to preserve the survey, or **Load survey** to reuse a saved JSON file. Setup can be completed before starting the fleet. Until coordinates are supplied, the map shows a setup prompt and never places guessed tag markers. `mode: "xyz"` requires noncoplanar anchor geometry. For a fixed-height plane use `mode: "xy"` and set the measured `tag_height_m`. Unknown coordinates remain null in the template and are rejected; the app never invents an anchor layout.

The solver uses range-difference initialization followed by geometric least squares. It rejects rank-deficient geometry, inconsistent fits, stale data and excessive time skew. A fit residual is not a bound on position error. The map shows an XY projection with estimated Z. Position accuracy stays labelled **NOT VERIFIED** until a separate reference experiment is performed.

Optional `range_bias_m` entries are keyed by `ANCHOR_BOARD_NAME/TAG_BOARD_NAME`, and are subtracted from measured ranges for position estimation. Only enter independently measured calibration values; validate using a different capture and several distances/orientations. For a stationary reference experiment, add `reference_tags` with measured tag XYZ coordinates to the survey and run:

```
python tools/check_range_accuracy.py --session build/control-sessions/<session> --survey surveyed-anchors.json
```

The report gives range RMSE, 95th-percentile absolute error, maximum error and fraction below 3 cm. With valid surveyed geometry and sufficiently fresh ranges it also reconstructs positions and reports Euclidean position error against the supplied stationary tag references. Reference accuracy itself is not independently verified. Sub-3-cm position accuracy requires surveyed geometry, antenna/range-bias calibration, appropriate line of sight, and independent ground truth; IMU data cannot guarantee it.

Mobile anchors are deferred. Keep surveyed anchors fixed for the position map. IMU-detected anchor movement latches a warning and invalidates position estimates until the fleet is stopped and anchors are resurveyed. Motion detection is not a guarantee that an anchor remained fixed, particularly during constant velocity or missing IMU data. Absolute mobile-anchor positioning needs externally observed anchor poses or a validated cooperative estimator; integrating acceleration alone drifts.

The mapping is saved as `four-anchor-plan.json`; actual readings are in `ranges.csv`, with reporting board identity and host receipt time. Per-attempt status and IMU telemetry are in `console.log`. Firmware radio settings remain volatile across resets. Stop and reconfigure after any board resets.


### Top-down map controls

- Anchor squares are A001–A004; tag circles use stable colors and IDs shown in the tag panel.
- The map uses equal scales on X and Y, with +Y upward. Height is displayed separately; this is an XY projection of the selected position calculation, not an automatic assumption that all boards share a height.
- Drag the map to pan; use the wheel or +/− to zoom. **Fit anchors** restores the surveyed extent.
- A tag trail spans at most 15 seconds of valid solved positions. Invalid or stale positions are removed rather than extrapolated into a made-up trajectory.
- The tag panel shows board identity, fresh-link count, IMU status, and coordinates or the reason a position cannot be shown. The range table is under **Measurement details**.
- In XY mode, all tags must remain at the measured fixed height entered in the editor. XYZ mode requires suitable noncoplanar geometry. Existing arbitrary-role selection remains available.

See [top-down UI and hardware check](TOPDOWN_TRACKING_2026-10-09.md).

## Automatic anchor positions

Use **Multi-tag Cooperative** for automatic relative positions using Sparse Cooperative Localization, Dynamic Neighbor Selection and Scheduled UWB Ranging. It jointly estimates anchors and tags, including real tag-to-tag measurements, without manual anchor coordinates. See [Cooperative tracking](COOPERATIVE_TRACKING.md) for setup, scheduling, motion handling and quality limits. The former [automatic anchor survey](AUTO_ANCHOR_TRACKING.md) document describes the legacy implementation.

