# Measured sniffer origin

Launch `Swarm_Sniffer_Origin.cmd` for the six-board COM9 roster. Keep the COM9
board stationary. The UART board is an **active collector**, not a passive
sniffer in this mode: mobile IDs 1–5 stay unchanged and collector ID 6 transmits
in its own TDMA slot. Firmware 1.2.6 is required only on the collector; existing
1.2.5 mobile firmware understands the unchanged broadcast packet with count=6.

The collector forwards CRC-checked received packets and its own packets only
after successful TX completion. A later packet confirms the actual TX timestamp,
using the existing sequence/boot/term checks. The host assembles real DS-TWR
links to the collector. Missing origin data prevents a new origin-relative solve;
the host does not invent a sniffer coordinate from passive reception.

The planar range IEKF fixes the collector state at (0,0). Rotation alignment is
about that origin only, using temporarily quiet peers. Heading remains relative;
one stationary ranging origin does not determine room heading or disambiguate
every symmetric/poor geometry. Do not interpret this as compass-referenced pose.

Filter corrections: resetting quiet velocity now resets its covariance and cross
covariance as well. Quiet-to-quiet ranges can refine startup geometry; moving-peer
measurements cannot drag the quiet subgroup. Motion process noise allows faster
response. BNO085 norm/gyro-motion telemetry is used for motion gating and process
noise; full acceleration/quaternion vector navigation is not implemented.

Tests include origin invariance, missing-origin rejection, noisy startup recovery,
long-duration covariance stability, motion, and collector provisioning/recovery.
Synthetic error bounds are not measurements of physical tracking accuracy.

The 60-second user-confirmed rest recording exposed range standard deviations
of 29.6 cm on 3–4 and 27.3 cm on 3–6. The host now estimates additional link
noise from detrended range history, and two nonparallel stable ranges can veto
a range-only motion accusation (never a fresh BNO085 motion report). Replaying
627 samples reduced stationary radial dispersion of node 2 from 7.80 to 2.56 cm
and node 3 from 26.58 to 13.32 cm. Node 5 increased from 2.62 to 2.99 cm. These
are dispersion figures, not position accuracy; node 3 remains problematic.
Raw recording: `tmp/sniffer-origin-confirmed-rest.jsonl`. Report:
`tmp/sniffer-origin-rest-replay-final.json`; replay with `tools/swarm_rest_replay.py`.
Each link trend is computed once per solve and reused for rest detection,
motion consistency, and observation variance to avoid redundant fitting.
Range-fit RMS above the configured limit is labeled estimated/inconsistent,
even when all packets are fresh. That residual is not an absolute error bound.

The subsequent user trial moved node 2 toward the collector and back. Quiet
state updates and range-only rest departures are now suspended during fresh
BNO motion elsewhere and for two seconds after it. A node's own BNO motion or
stale IMU still releases its rest state immediately. This prevents changed RF
paths from correcting quiet-peer geometry during another node's motion, while
allowing startup/rest geometry refinement outside that interval.

Full-history replay (454 sampled frames) is in
`tmp/node2-motion-settled-history.json`. Node 2's displayed return displacement
at the last motion report changed from 22.1 to 10.6 cm. Quiet nodes still displaced
by up to 10.9 cm in that replay, so this is **not a completed accuracy fix**.
`tools/swarm_motion_replay.py` replays the full pre-motion history; an earlier
short warm-start-only experiment was not used for this comparison.

Release: 1.2.6+0, image hash
`124759a36400261b9adaf54a8d7e67ffbaeb334110e6c5643ca71dfb9faff90e`.
Use the Control Center's **Sniffer origin (stationary)** checkbox when configuring
the fleet manually, or the launcher above to preserve the existing mobile IDs.
