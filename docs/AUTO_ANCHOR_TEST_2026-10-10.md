# Automatic anchor test — 10 October 2026

User confirmed all six boards were stationary on a flat table. The test used
explicit same-plane 2D mode, without entering any anchor coordinates.

## Deployment

All six boards run signed, verified and confirmed firmware **1.0.12**.
Image digest: `bf23386aacdabbde4bbb9d5a95786e8b529a4ba809bd87d47f3ef61dc2eed47f`.
The first batch updated five boards; PCB_7A99EB74 subsequently updated on its
isolated retry. Reports:

- `build/batch-ota/20261009T030731230031Z/results.json`
- `build/batch-ota/20261010T043350394668Z/results.json`

Anchors: PCB_76A10829 (A001), PCB_09EC3021 (A002), PCB_7A99EB74 (A003),
PCB_854655FD (A004). Tags: PCB_642D2C6D (B001), PCB_F42728EC (B002).

## Final live GUI run

Evidence: `build/auto-anchor/gui-live-result.json`, `summary.json`, and
`gui-live.png`. Session: `build/control-sessions/20261010T043758460573Z/`.

| Measure | Result |
| --- | --- |
| Recorded cycles | 16 |
| Anchor measurements returned, including retries | 192 / 192 |
| Tag measurements returned, including retries | 128 / 128 |
| Accepted layouts with both tag solutions | 4 / 16 |
| Rejected: planar edge fit exceeds 5 cm | 10 |
| Rejected: reciprocal edge change exceeds 5 cm | 2 |
| Median full cycle | 2.9765 seconds |

The screenshot is an actual accepted live frame, not a synthetic layout. It
does not represent continuous acceptance: twelve cycles were rejected and their
positions hidden. In the four accepted cycles, B001 varied 2.29 cm in X and
3.17 cm in Y; B002 varied 1.53 cm in X and 4.68 cm in Y. These are small-sample
stationary spans, **not absolute accuracy**. Tag range-fit RMS was approximately
3.9–5.3 cm. No ground-truth positions were supplied.

The automatic survey and relative view function, but the current measurement
consistency is insufficient for reliable tracking or a sub-3-cm claim. Range
bias/antenna calibration and a wider anchor arrangement should be investigated
with physical reference measurements. No rubble trial or physical anchor-shift
trial was performed; changed-anchor recovery was tested using synthetic known
geometry. The live application was left open in Auto-anchor Tracking, same-plane
2D mode. It continuously retries surveys and rejects inconsistent layouts.

## Software verification

- Firmware build passed: 192016 / 200192 flash bytes, 48249 RAM bytes.
- Portable C DS-TWR tests and new compiled anchor-listener tests passed.
- 52 Python tests passed (`tmp/tests-auto-anchor-final.log`).
- Tests cover planar/3D geometry, changed anchors, stale/missing ranges, motion
  rejection, grossly inconsistent edges, listener allowlists, cleanup, scheduling,
  role preservation and OTA guards.
- Initial hardware test stopped all six nodes cleanly. Its results are retained
  in `build/auto-anchor/hardware-result.json`. That first run used an exact
  triangle precheck; the final planar solver instead fits all six noisy edges and
  checks each residual, including noisy collinear subsets. No fit threshold was
  relaxed to achieve acceptance.

Usage and limitations: [Automatic anchor tracking](AUTO_ANCHOR_TRACKING.md).
