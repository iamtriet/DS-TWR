# Four-anchor UWB bench test — 2026-10-08

Firmware: 1.0.9+0. Signed image hash `098467c4370fcc66a68ba8adedfabef218b7becc245bcf46d893cc6b861ae25f`.

## Configuration

| Role | ID | Board |
|---|---|---|
| Anchor | A001 | PCB_76A10829 |
| Anchor | A002 | PCB_09EC3021 |
| Anchor | A003 | PCB_7A99EB74 |
| Anchor | A004 | PCB_854655FD |
| Tag | B001 | PCB_642D2C6D |
| Tag | B002 | PCB_F42728EC |

The first-stage tag was PCB_F42728EC / B001. With the second tag added, tag IDs were assigned in board-name order as shown above.

## Hardware results

Four anchors / one tag: **78/80 raw requests succeeded** over four cycles (77.78 seconds including configuration and cleanup). A001 and A003 each had one receive timeout; subsequent requests recovered. Every five-request burst delivered at least four samples. All boards stopped successfully.

Four anchors / two tags, initial run: **152/160 raw requests succeeded**. All eight links ranged, but two five-request bursts delivered only three samples, failing the per-burst criterion. Evidence is retained in `build/four-anchor-tests/multi/`.

After adding one bounded retry with 25 ms delay for recoverable radio errors: **160/160 planned samples delivered**, with **5 failed raw attempts** retained in the counters (160 successes out of 165 attempts, 96.97%). Every link produced 20 samples across four cycles; every five-sample burst completed. No tag stopped permanently and cleanup succeeded for all six boards. Elapsed time: 97.81 seconds including configuration and cleanup.

| Link | Successful measurements | Failed attempts | Observed distance range (m) |
|---|---:|---:|---|
| A001/B001 | 20 | 0 | 0.8734–0.9637 |
| A001/B002 | 20 | 0 | 0.3990–0.4868 |
| A002/B001 | 20 | 2 | 0.7412–0.8123 |
| A002/B002 | 20 | 0 | 0.5067–0.5618 |
| A003/B001 | 20 | 0 | 1.1532–1.2331 |
| A003/B002 | 20 | 1 | 0.3172–0.3706 |
| A004/B001 | 20 | 1 | 1.2101–1.3425 |
| A004/B002 | 20 | 1 | 0.4198–0.4880 |

## Implementation and verification

- Tags use an explicit anchor allowlist; RESP/REPORT address the accepted POLL sender. FINAL must match both that anchor and the token.
- The host enables no autonomous anchor transmitters. One connected anchor performs five rounds across all tags before the host visits the next anchor. Fatal errors halt the scheduler; recoverable frame/range errors are counted and retried at most once.
- The first single-tag test exposed incorrect host errno assumptions: Zephyr uses ETIMEDOUT=116, EBADMSG=77 and EMSGSIZE=122. The host now uses the firmware values. That failed test is preserved in `build/four-anchor-tests/single/`.
- Portable C protocol tests cover all four allowed sources, rejection of an unlisted source, rejection of a FINAL from another source, and response/report destinations. All 35 Python tests passed, including sequential scheduling, bounded retries, cleanup after partial setup, and clearing the allowlist when returning to single-peer mode.
- PlatformIO validation_b build passed: flash 190168 bytes, RAM 48220 bytes. Image signing and verification passed.
- OTA: all initial five boards updated and verified; PCB_854655FD resumed after one BLE interruption. The sixth board also updated and verified. Firmware 1.0.8 artifacts were archived before replacement.

## Evidence

- Initial-five OTA: `build/batch-ota/20261008T025523473659Z/results.json` (5/5 verified).
- Sixth-board OTA: `build/batch-ota/20261008T030835576728Z/results.json` (1/1 verified).
- Single-tag final: `build/four-anchor-tests/single-retest/result.json` and `console.log`.
- Multi-tag final: `build/four-anchor-tests/multi-retest/result.json` and `console.log`.
- Exact plans and scan: `build/four-anchor-tests/single-plan.json`, `multi-plan.json`, `stage2-scan.json`.
- Host tests: `tmp/tests-four-anchor-final.log`. Build/sign: `tmp/build-multi-anchor.log`, `tmp/sign-multi-anchor.log`.

## Limits

These are real DS-TWR distance measurements, not validated XY/3D positions. No surveyed anchor coordinates or reference distances were supplied, so this test does not establish absolute accuracy. Ranges from different anchors are taken at different times; BLE reconnection and host scheduling limit update speed. This short bench test does not establish long-duration reliability, moving-target performance, or 30-tag capacity. The view supports four anchors plus up to eight tags; this hardware run exercised two tags.

LED2 remains the charger-controlled green indicator; no firmware command can drive it. Default LED3 blink behavior is retained.

## Live Control Center check

The actual Tk Control Center started the six-board plan through its normal Start function. All eight displayed links delivered at least five measured samples; the first complete displayed set contained 40 successes and zero failures. The app was left running on the Four-anchor Tracking tab. Evidence: `build/four-anchor-tests/gui-live-result.json`, `gui-live.png`, and `build/control-sessions/20261008T031548624016Z/` (CSV and console).
