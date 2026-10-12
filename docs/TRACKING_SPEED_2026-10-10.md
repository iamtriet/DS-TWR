# Tracking speed and B002 recovery — 10 October 2026

Reconnected PCB_F42728EC (B002) after the user's power cycle and restored its
tag role, ID B002, four-anchor allowlist, continuous responder, role LED and IMU.
No firmware update was needed; all boards remain on 1.0.12.

Changes:

- Reuse the closing six-edge survey to open the following cycle. Alternate
  direction and retain motion history over the overlap. No parallel UWB
  transmissions or relaxed geometry thresholds.
- Publish each tag fix immediately after its four readings arrive when the
  previous accepted anchor layout is still fresh. Never refresh anchor validity
  from tag traffic. A failed closing survey or detected anchor motion holds the
  display.
- Detect broken BLE sessions, reconnect and restore roles; clear cached surveys
  on recovery. Bound repeated failed recovery attempts and retain Stop behavior.
- Display position age on each tag card; show anchor survey age separately.

Hardware benchmark (`build/tracking-speed/result.json`), same six-board plan:

| Mode | Cycles | Median wall time per cycle | Tag links returned |
| --- | --- | --- | --- |
| Original full surveys | 8 | 2.844 s | 64/64 |
| Reused surveys | 20 | 2.3125 s | 160/160 |

Median cycle duration was **18.7% shorter**, including the initial full cycle in
the optimized sample. These are measurement cycles, not verified position update
rates. Both phases had zero accepted planar layouts: measured edges failed the
unchanged fit/reciprocity checks. Faster ranging therefore did not establish a
faster *valid-position* rate in this setup. Early-fix behavior was verified with
known synthetic geometry in tests; it still needs consistent live geometry to
benefit the map.

An additional intentional BLE disconnection of B002 verified automatic recovery.
Afterward all four anchors again returned distances to B002: 0.5883, 0.3415,
0.5790 and 0.4621 m. B002 status confirmed role tag, continuous=1, four allowed
anchors and successful responder exchanges. Cleanup returned no stop errors.

Portable C tests and 56 Python tests passed (`tmp/tests-tracking-speed.log`).
The updated GUI was restarted. Its prior real display snapshot was restored as
HELD during this application update; it is historical, not a fresh position.
GUI evidence is under `build/tracking-speed/gui/`.
