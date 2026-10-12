# Cooperative tracking hardware test — 2026-10-10

## Deployment

All six enrolled boards were verified running the confirmed 1.0.13 image. The interrupted earlier batch was resumed: two boards were already current, four finished updating. The resumed batch completed in 246.03 s with zero failures. Evidence: `build/batch-ota/20261010T081045315501Z/results.json`.

Signed image: `build/images/validation_b.signed.bin`, 197236 bytes.

- MCUboot image digest: `934c56e6fa0018c34b48a926031f1f5fe973ffcb023dd7d668274883fc931752`
- File SHA-256: `560a685a9e4361ef597c065bc7a7c12d69d9d736e86c12f38f25d5d7ea680b4b`
- Firmware build: 192260/200192 bytes flash, 48257 bytes RAM.
- Python regression suite: 67/67 passed after the final host corrections; portable C tests also passed, including cooperative tag initiation without a role change.

## Initial six-board test

Four anchors A001–A004 and two tags B001/B002 were configured using `build/four-anchor-tests/multi-plan.json`. The selected mode was 2D, based on the user's statement that all six boards were stationary on one flat table. No manual coordinates or synthetic ranges were fed into the hardware test.

| Measurement | Result |
|---|---:|
| Completed cycles | 20 |
| Accepted joint frames | 20/20 |
| Successful scheduled slots, including retries | 171/171 |
| Successful tag-to-tag measurements | 19 |
| Cycle duration minimum / median / maximum | 0.968 / 1.2975 / 2.063 s |
| First cycle slots | 13 |
| Steady cycle slots | 8 or 9 |
| Active graph edges | 12 of 15 possible pairs |
| Fit RMS minimum / median / maximum | 1.31 / 3.03 / 5.50 cm |
| Cleanup errors | 0 |

The fit RMS is agreement with the selected UWB observations, **not absolute position error**. Acceptance gates changed from the legacy survey method, so the new acceptance rate must not be represented as a controlled accuracy improvement.

Actual B001–B002 ranges varied from 0.1817 to 0.2515 m. B001 chose A001/A002/A003 throughout. B002 used A001/A002/B001 for 18 frames, A002/A003/B001 for one frame, and A001/A002/A004 for one frame. This confirms the implementation performs actual cooperative ranging and changes its selected neighbors on hardware.

All six firmware status responses showed `cooperative=1` and continuous listening. A001–A004 retained role `anchor`; B001 and B002 retained role `tag` while participating in symmetric ranging. LED role behavior was tested in firmware code; no physical LED observation is claimed here.

All six BNO085s reported ready, streaming, fresh data, linear-acceleration accuracy level 3, and zero invalid reports, communication errors or missed reports at the end of this run. Existing transport-event code 6 was still reported; this test does not resolve that diagnostic. Motion detection is active, but the stationary bench run does not validate moving-anchor performance.

## Remaining accuracy limitations

The stationary coordinate spans over this short run were:

| Tag | X span | Y span |
|---|---:|---:|
| B001 | 9.25 cm | 7.48 cm |
| B002 | 15.69 cm | 37.56 cm |

These are observed peak-to-peak variations in a noisy relative frame, not errors against surveyed ground truth. They show that sub-3-cm stable tracking is **not achieved**. The implementation deliberately preserves raw observations and reports rejected/stale frames rather than hiding uncertainty with fabricated coordinates. Calibration, range bias/NLOS investigation and independently measured reference positions remain necessary.

The schedule is host-serialized BLE/UWB ranging, not firmware-level synchronized TDMA. The six-board timings do not establish scaling to 30 simultaneously tracked boards. Anchor movement recovery and degenerate geometry have synthetic regression coverage; physical moving-anchor validation is still outstanding.

## Final host corrections and 30-cycle retest

The initial live GUI session exposed two host issues beyond the first 20-cycle run: old position seeds could remain trapped in a wrong local minimum after neighbor changes, and anchor edges could expire during a cycle even when fresh at its start. The final implementation retries rejected old seeds with fresh range-only initialization under the same quality gates, and schedules anchor refreshes against the predicted cycle end. A recorded failing hardware frame now provides a deterministic regression fixture (`tests/cooperative_reseed_case.json`): the old seed failed at approximately 11.7 cm RMS while fresh initialization converged at approximately 3.05 cm RMS. No rejection thresholds were relaxed.

The final 30-cycle hardware run is the representative result for the delivered scheduler:

| Measurement | Final result |
|---|---:|
| Accepted joint frames | 25/30 |
| Successful slots, after retries | 264/264 |
| Actual tag-to-tag measurements | 30 |
| Cycle minimum / median / maximum | 0.812 / 1.2655 / 1.750 s |
| Accepted fit RMS minimum / median / maximum | 2.34 / 3.00 / 5.30 cm |
| Slot distribution | 15 cycles × 8; 11 × 9; 2 × 10; 1 × 12; 1 × 13 |
| Stop errors | 0 |

One bootstrap frame lacked fresh selected observations. Four frames were rejected for range inconsistency (the peak-residual gate can reject even if RMS is below 8 cm); the live display retains the previous frame marked HELD in that case. Final UI rejection text now includes both RMS and peak residual to make that distinction clear.

For the accepted frames, stationary peak-to-peak X/Y spans were 9.02/8.90 cm for B001 and 14.91/16.76 cm for B002. This remains above the requested 3 cm target. The system recovers valid frames after rejecting inconsistent data; it does not guarantee every cycle produces a fresh position.

Final test evidence: `build/cooperative/hardware-final/result.json` and `console.jsonl`. The updated Control Center was restarted in **Multi-tag Cooperative**, with the six-board plan and explicit same-plane 2D mode.

## Evidence

- Raw hardware events: `build/cooperative/hardware/console.jsonl`
- Slot measurements, frames, role/IMU status, timings and stop results: `build/cooperative/hardware/result.json`
- Test driver: `tools/cooperative_test.py`
- Separate real live UI capture: `build/cooperative/gui/live.png`
- Synthetic layout screenshot (not hardware evidence): `build/cooperative/synthetic-ui.png`
- User instructions and algorithm details: [Cooperative tracking](COOPERATIVE_TRACKING.md)
