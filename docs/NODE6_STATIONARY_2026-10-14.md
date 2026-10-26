# Stationary UWB jitter — 2026-10-14

All six boards were reported stationary. Absolute surveyed distances are not needed to quantify jitter. Firmware remains 1.0.18; changes are in host processing.

## Applied changes

- Fast range filter: causal three-sample median, robust noise estimate from first differences, three consecutive same-direction innovations before increasing bandwidth. Stationary cutoff 0.25 Hz; moving cutoff 3 Hz with a 0.35 s release. This adds smoothing delay; moving-board response was not verified in this session.
- Removed mandatory three-second neighbor probes for the five-reference profile. Retain a healthy set unless its geometry/noise score becomes substantially worse; failed links still allow replacement.
- Triangle consistency now applies to the coherent fast profile (150 ms measurement span). Reject overlong links with two violations above 15 cm or one gross violation above 20 cm. Hold rejected links at least five seconds; re-admit only when they agree within 10 cm with fresh geometry solved from other links. No replacement ranges or coordinates are fabricated. Remaining graph rigidity is checked.
- Kalman remains off. Raw ranges are retained. No imposed 70 cm distance or position lock.

Link 0001–0005 sometimes reported approximately 1.1 m while 0001–0002 plus 0002–0005 was about 0.8 m. This inconsistent constraint deforms the reference group and amplifies node 0006 jitter. A triangle alone does not identify which physical range is biased; rejecting the overlong link is a mitigation, not calibration. Re-admission based on prior geometry also has a tradeoff: a rejected link cannot bootstrap a new geometry on its own.

## Live observations

No automated test suite was run, following the user's request. Live session statistics describe precision, not absolute accuracy. Always inspect fresh-solution percentage together with jitter to avoid mistaking frozen positions for improvement.

Saved baseline: sparse-node6-stable/stationary-before.json (30 s): node 0006 radial P95 56.56 cm, per-frame step P95 14.19 cm, fresh solutions 60.8%.

Intermediate sessions: sparse-node6-noise-aware, sparse-node6-coherent, sparse-node6-quarantine. These initially improved step jitter but retained substantial slow drift. A trial with raw-dispersion weights (sparse-node6-weighted) reduced fresh node 0006 solutions and was reverted. The final solver does NOT use these weights.

Final running session: build/uwb3d/hardware-six/sparse-node6-guarded. Data includes raw packets, per-node states, latest positions and stationary-after.json. No claim of 3 cm accuracy or complete jitter elimination.

Final saved observation (29.969 s / 320 publications, stationary-after.json): node 0006 radial P95 13.84 cm, inter-frame step P95 4.69 cm, fresh solutions 91.9%. Other nodes' radial P95: 1=1.82 cm, 2=2.78 cm, 3=3.90 cm, 4=1.34 cm, 5=6.43 cm. This is an improvement over the saved baseline, but residual node 0006 jitter remains material. Session is left running; no claim of complete correction.
