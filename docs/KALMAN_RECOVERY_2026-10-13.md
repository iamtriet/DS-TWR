# Live Kalman and recovery changes

Observed on the physical fleet: node 6's BLE diagnostic reported network
`running=0`, default node 1/6, no UWB initialization/calibration or TX/RX since
boot. Meanwhile firmware 1.0.18's coordinator repeated its cached discovery
identity every ten frames. The host incorrectly treated that cached roster as
live presence; an isolated node caused a disconnected graph and all markers
turned stale. This is evidence of a restarted/unconfigured node, not proof of
the electrical cause of the reset.

Changes applied in Control Center, without automated tests as requested:

- Cached discovery updates identity only; accepted radio range receipts provide
  liveness. UI connection wording is `ranging` / `no fresh ranges`, not a claim
  that BLE is disconnected.
- When ranges are absent for five seconds, a bounded monitor verifies the
  board's hardware UID and reads its network state over BLE. If stopped, it
  restores UWB configuration and the original node ID/timing. Running radios
  are left running. One recovery at a time, at least 20 seconds between attempts
  for a given node; stop cancels the monitor before fleet cleanup. No flash or
  reboot commands are issued by recovery.
- Isolated/underconstrained nodes do not invalidate the largest connected
  measured component. The display keeps per-node states and last known points;
  an unavailable node is not falsely rendered as measured.
- Constant-velocity Kalman state is position + velocity (six components per
  node). Process covariance uses actual dt and white acceleration (3 m/s²),
  measurement covariance uses a noise floor and range-fit residual, and a
  normalized innovation above 16 increases maneuver uncertainty. Joseph-form
  covariance update is retained. Long gaps reset velocity on reacquisition.
- Predictions are explicitly amber and bounded to 0.6 s; beyond that, points
  are stale gray. Kalman cannot recover a physical power loss or missing RF.
- Keep the fixed session coordinate frame using shared stationary nodes on
  drop/rejoin; node 3 is excluded as the user-confirmed moving board. Planar
  gauge initialization no longer requires node 3 to stay off the baseline
  between nodes 1 and 2.

Firmware remains 1.0.18, 100 ms superframe, 10 ms slots. Motion profile keeps
the 150 ms range timestamp window and does not feed Kalman-derived velocity
back into the ranging residual, avoiding an unstable compensation feedback loop.
The table-plane assumption still requires approximately planar motion.

Initial live reception after restoring the stopped node showed all six nodes,
about 137 ranges/s and 10–11 position updates/s. Automatic recovery on a future
spontaneous restart and moving-position accuracy still need real-world
observation; no synthetic success or deliberate power interruption was used.
