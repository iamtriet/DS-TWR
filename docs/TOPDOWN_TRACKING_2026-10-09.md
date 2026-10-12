# Top-down tracking view — 2026-10-09

The Control Center now prioritizes a top-down XY map. It has surveyed anchor squares, colored tag circles, bounded 15-second trails, equal-axis grid/scale, pan/zoom/fit controls, and tag-status cards. The raw distance table and transport/IMU details are collapsed by default. The coordinate editor accepts measured X, Y and height without hand-editing JSON. Saved surveys can be loaded again.

No real anchor coordinates have been provided yet. The live map therefore remains in setup mode; raw UWB ranging and IMU motion data operate normally. The UI fixture screenshot is explicitly labelled simulated and is not evidence of physical positions.

## Verification

- All six expected PCB identities were rediscovered on October 9.
- The actual desktop app started four anchors and two tags. Every one of the eight links produced at least five real samples in the initial UI check; all six IMUs reported calibration quality 3 and quiet conditions in that check.
- A longer sample of the first live session showed per-link median successful-update intervals of 0.992–1.063 seconds. Maximum gaps in that snapshot ranged from 1.443 to 3.536 seconds. These are receipt intervals, not absolute sensor-to-screen latency. Timeouts remain visible and further scheduling work is needed for higher-rate reliable tracking.
- The geometry editor was exercised with synthetic test coordinates. XY positioning, equal-axis orientation and the map render were checked with an explicitly labelled test fixture. The app used no test coordinates in the live hardware session.
- All 41 host tests passed, along with the portable C tests run by `tools/run_tests.py`.
- A cancellation race was corrected in the BLE/USB command backend: cancellation during a write now marks the session desynchronized, forcing reconnection before Stop can consume a late range ACK. A deterministic regression test covers cancellation during the write itself. The original observed stop failure recovered on reconnect/retry.

## Evidence

- `build/topdown-tracking/scan.json`
- `build/topdown-tracking/gui-first-check.json` and `gui-first-check.png`
- `build/topdown-tracking/gui-live-result.json` and `gui-live.png` (latest app check)
- `build/topdown-tracking/range-intervals.json`
- `build/topdown-tracking/ui-fixture.png` (SIMULATED UI CHECK ONLY)
- `build/control-sessions/20261009T014048201810Z/` (first real session; CSV and logs)
- `tmp/tests-topdown-final.log`

## Ongoing tracking work

All six boards were previously verified on signed firmware 1.0.11. LED3 role indication is automatic: solid for anchors, blinking for tags. BNO085 asynchronous report setup and dynamic calibration are enabled. The host uses sufficiently fresh, calibrated quiet-motion evidence to gate optional range smoothing; the source now says IMU SMOOTHED only when smoothing actually takes place. Raw measurements remain available and unchanged in the CSV.

The earlier persistent-connection/IMU stress run delivered 139 of 160 planned measurements and did not pass its strict completion criterion. Its successful command RTT median was 94 ms, with a 172 ms 95th percentile; all six IMUs supplied valid decoded reports and quality-3 motion samples. Preserved evidence is `build/tracking-upgrade/persistent-test/result.json`. Removing reconnects improved refresh time but did not eliminate radio timeouts. IMU transport sequence warnings were recorded separately and are not silently converted to valid positioning confidence.

Neither zero latency nor better-than-3-cm tracking accuracy has been established. A map transform is not an accuracy improvement. Independent survey/reference measurements, bias calibration, and separate validation captures remain necessary. Mobile-anchor position estimation is deferred; detected anchor motion invalidates surveyed-position estimates. An IMU alone cannot provide a drift-free absolute anchor location.

For sensor report definitions, see the manufacturer’s [BNO08X datasheet](https://www.ceva-ip.com/wp-content/uploads/BNO080_085-Datasheet.pdf) and [SH-2 reference manual](https://www.ceva-ip.com/wp-content/uploads/2019/10/SH-2-Reference-Manual.pdf).
