# Anchor-free broadcast swarm implementation — 2026-10-15



## Entry points



- `include/swarm_wire.h`: packed IEEE 802.15.4 short-address broadcast MAC, 40-bit modular timestamp helpers, delayed-TX rounding.

- `src/uwb_swarm.c`: dynamic-master TDMA state, one broadcast per node per 39 ms superframe; 30 reserved slots of 1.3 ms. Active node i uses slot floor(30*(i-1)/count), spreading small fleets across the superframe. Node 0 is the passive sniffer and never enters the UWB TX branch.

- `src/swarm_timer.c`: nRF52833 TIMER2 at 1 MHz wakes the application before the delayed-TX deadline. DW3110 delayed TX controls the actual RF timestamp; an MCU timer does not replace the DW clock. BLE owns TIMER0/RTC0, Zephyr owns its RTC timebase. The disabled software-PWM node must remain disabled because it also names TIMER2.

- `src/radio.c`: nonblocking arm/completion/RX, actual-TX confirmation, full CIA diagnostics (`dwt_readdiagnostics`), direct RX/first-path power-ratio calculation with DW3110 scaling. Unknown quality is 255, never invented LOS.

- `src/console.c`, `zephyr/swarm.conf`: UARTE EasyDMA via Zephyr async UART at 1,000,000 baud. IRQ RX is disabled for UART0 in this firmware environment. Queue overruns and TX aborts are counted.

- `tools/swarm_localization.py`: all five requested public classes and executable simulation.

- `tools/swarm_core.py`: broadcast codec / clock-domain DS-TWR assembly and Control Center adapter.

- Control Center → Tracking → source **swarm**. Select the passive sniffer plus mobile nodes. Enter its serial port, then identify the board physically attached to it. The sniffer is ID 0 and has no estimated position.



## Build and sign



```powershell

$env:PLATFORMIO_BUILD_DIR='build/swarm-toolchain'

& 'C:\Users\iamtriet\.platformio\penv\Scripts\platformio.exe' run -e swarm

python tools/sign_image.py sign --env swarm --build-dir build/swarm-toolchain --output-dir build/releases/swarm-1.2.2

```



The MCUboot layout and development signing key are unchanged. `swarm` omits the old sparse network implementation to fit the existing OTA slot. Do not overwrite the signing key. Signed image version is 1.2.2. Compile/sign success is not RF qualification or production qualification.



## Wire format and timestamp validity



The maximum radio frame is 252 bytes before the hardware two-byte FCS: nine-byte MAC, forty-byte protocol header, twenty-nine seven-byte peer records. Extended PHR is enabled, CH5 / 6.8 Mbps / 128-symbol preamble. This is a custom application protocol on the 802.15.4 PHY/MAC, not a FiRa-certified protocol; STS/security are not implemented in this mode.



Every record carries a uint32 reply interval, the uint16 sequence of the received peer transmission, and one quality byte. The validity bitmap excludes stale/missing observations. uint32 DW time spans only about 67 ms: older intervals are omitted, never silently truncated. Actual previous TX timestamp plus boot/master generation distinguish missed delayed TX, restart, failover and 16-bit sequence wrap.



For the chain A0 → B0 → A1 → B1:



```

Ra = ((TX_A1 - TX_A0) mod 2^40) - reply_A1[B]

Da = reply_B0[A]

Rb = ((TX_B1 - TX_B0) mod 2^40) - reply_B1[A]

Db = reply_A1[B]

ToF = (Ra*Rb - Da*Db) / (Ra + Rb + Da + Db)

distance = ToF * (1 / (499.2e6 * 128)) * 299702547

```



A0, B0 and A1 must have their TX timestamps confirmed by later broadcasts. B1 only reports an earlier RX: its scheduled TX term cancels exactly in Rb, so waiting another cycle to confirm B1 is unnecessary. Integer products are computed before division on the Python host. 40-bit wrap and maximum interval bounds are explicit. DS-TWR suppresses first-order clock mismatch; it does not eliminate all oscillator variation, antenna bias, quantization, multipath or motion error.



The lower active node ID supplies the epoch through its own broadcast, without an extra sync frame. After more than two missed superframes, live peer observations determine the next candidate. A returning lower-ID master is adopted. This requires a connected radio graph: a partition can elect multiple masters, and global agreement under asymmetric reception is not guaranteed. Hardware failover timing remains to be measured.



## Estimation



`AsymmetricDSTWRAssembler` maintains symmetric D and W; missing or expired links have W=0. NLOS weight is `exp(-max(0,gap_db-6)/3)`, with conservative weight 0.25 for missing diagnostics. Residual body-mounted pair calibration can be loaded from Control Center's **Swarm calibration** button:



```json

{"pairs": [{"uids": ["hardware_uid_A", "hardware_uid_B"], "bias_m": 0.012}]}

```



The number above illustrates the format only; it is NOT a calibration for these boards. Actual UIDs and measured residual biases must be supplied. Factory OTP antenna compensation is already applied by DW3110; the host correction is additional residual calibration, not a second application of OTP delay.



`WeightedSMACOFSolver` constructs V, `np.linalg.pinv(V)`, and B(X) explicitly; it performs 5–15 stress-decreasing Guttman iterations. Missing distances are filled with Floyd-Warshall only for initial Classical MDS; their measurement weights stay zero. Warm-start uses the previous measured geometry.



`OpportunisticKabschAligner` uses recent distance increments, variance, a supported stable subgroup and dwell time. Stable nodes receive 30x alignment weight. Proper-rotation SVD prevents applying a reflected coordinate transform. Distances cannot determine initial chirality or common rigid motion; individual local flips in weak geometry are not mathematically ruled out merely by fixing det(R).



`SwarmEKFTracker` models [x,y,vx,vy], applies Mahalanobis gating and Joseph covariance updates, caps velocity at 1 m/s and applies zero-velocity measurements for the relative-rest subgroup. Since the observation is SMACOF XY, its Jacobian is constant and this EKF specializes to a linear Kalman update. The same ranges are not fused again as independent observations. Covariance is conditional because node position errors are correlated.



## Run the requested 30-node simulation



```powershell

python tools/swarm_localization.py --seconds 30

python tools/swarm_localization.py --headless --seconds 30 --output build/swarm-simulation/30nodes

```



Requires numpy, scipy, matplotlib and threadpoolctl (already used by this project). Stop-and-Go speeds range from 0 to 0.85 m/s. Each peer RX and sniffer RX independently loses 20% of packets. Six percent of received peer packets have a positive 15–70 cm NLOS injection; LOS timestamp noise corresponds to 2.5 cm standard deviation. Independent oscillator offsets, drift, delayed-TX quantization and wrap are exercised through timestamp records; the solver never receives truth.



Recorded seed 731 / 30 s: 767 solved frames, two skipped startup frames, mean node coverage 97.67%, mean relative RMSE 6.65 cm, P95 RMSE 11.37 cm, P95 full simulation pipeline 18.38 ms. RMSE includes startup and is scored only on the largest connected component after removing the unobservable rigid gauge/chirality. It is not absolute accuracy or measured hardware performance. Missing nodes are not silently included as accurate predictions.



## Physical transport limitation



At 30 nodes the complete raw stream is approximately 214 kB/s including framing, exceeding UART 1 Mbps / 8N1's 100 kB/s payload ceiling. The firmware and UI therefore reject more than 17 mobile nodes on this raw UART transport. The 30-node solver and radio layout are implemented, but 30-node raw collection at 25.6 Hz is NOT qualified or achievable through this unchanged link without an alternate transport/guaranteed compression architecture.



This custom PCB also lacks USB VBUS routing according to HARDWARE.md; simply enabling USB CDC is not a valid fix. A board revision or separate higher-bandwidth collector is needed for raw full-mesh 30-node transport. Current authorized hardware configuration is COM9 passive sniffer + four mobile boards.



Primary reference: Qorvo APS013 (asymmetric DS-TWR), https://forum.qorvo.com/uploads/short-url/5yIaZ3A99NNf2uPHsUPjoBLr2Ua.pdf. Device diagnostics and delayed TX follow the vendored Qorvo DW3xxx Device Driver API; module specification: https://www.qorvo.com/products/p/DWM3001C.



## Hardware bring-up evidence (2026-10-15)



`tmp/swarm-probe-4.log`: PCB_76A10829, ST-Link USB 1-6.1, signed 1.2.1 candidate, 275 TX / zero missed slots / zero TX errors. COM10 passive collector captured 205 valid frames during 8 seconds (25.625 Hz); this is a single-transmitter timing check, not multi-node localization accuracy. The firmware removes redundant fixed TX configuration from each slot, uses distinct Zephyr SPI configurations (the driver caches by pointer), and polls between TIMER2 events with a 100 us fallback. Last measured TX preparation was 427 us.



The first 1.2.0 hardware run failed to transmit; it must not be used as evidence of a working network. Firmware 1.2.1 is the corrected candidate. Multi-node timing, packet reception and geometry must be evaluated separately from the successful single-node TX measurement.



## Current powered fleet (2026-10-19)



The user changed the setup to five powered boards, with COM9 as sniffer. UART `info` verified COM9's FICR as `a25545db854655fd` (PCB_854655FD). Two BLE scans found exactly PCB_854655FD, PCB_09EC3021, PCB_76A10829, PCB_7A99EB74 and PCB_F42728EC. Use `build/uwb3d/hardware-five-com9/roster.json`; preserve the old six-board roster separately. The intended new IDs are sniffer 0 = PCB_854655FD, mobile 1 = PCB_09EC3021, 2 = PCB_76A10829, 3 = PCB_7A99EB74, 4 = PCB_F42728EC. ST-Link moved to USB 1-4.1; FICR read verified it is still PCB_76A10829.



1.2.1 passed single-transmitter timing but failed the adjacent-slot follower test. Therefore that result does not establish a working swarm. The 1.2.2 candidate processes RX before deciding TX, avoids repeated fixed RX configuration, and computes the power gap directly from the read diagnostics:



`gap_db = 10*log10(C * 2^21 / ((F1/4)^2 + (F2/4)^2 + (F3/4)^2))`.



The accumulator normalization, PRF offset and DGC correction cancel in this difference. Invalid diagnostics retain quality 255. This also avoids extra SPI register reads inside the vendor absolute-power helpers. Multi-node hardware qualification remains pending until recorded exchanges succeed.



The 1.2.2 candidate distributes a four-node fleet over slots 0, 7, 15 and 22 (9.1–10.4 ms separation), retaining the 1.3 ms slot grid and 39 ms broadcast period. SPI EasyDMA uses the Zephyr async API with bounded completion polling to avoid per-register thread suspension. Full diagnostics remain enabled for every received mobile-node frame. Adjacent-slot tests measured RX processing around 732 us and TX preparation around 366 us, leaving insufficient margin at 30 populated slots with this port. The distributed four-node run must not be interpreted as 30-node RF qualification.



The distributed-slot pair check succeeded (`tmp/swarm-pair-122-spread.log`, `tmp/swarm-pair-122-metrics.json`): 409 received beacons in 8 s (205 + 204), 405 assembled DS-TWR observations, zero assembler rejections. The updated follower reported 389 TX and zero missed slots. Raw pair distance median was 0.5342 m, standard deviation 0.0298 m. No external reference distance was supplied, so this is repeatability, NOT absolute accuracy. Both exchange directions contribute observations; the host still solves at 25 Hz.



## Movement isolation and recovery (1.2.3)

Swarm BLE status connections no longer stop ranging. The upload callback locks radio access and yields up to 100 ms for the main thread to stop ranging; flash writes are rejected if the radio remains active. Legacy firmware behavior is unchanged. Host recovery requires 1.2.3+, verifies hardware UID, and restores stopped/rebooted nodes or radios explicitly reporting ready=0. Running, ready radios are left alone. Recovery starts after five seconds without observations, with one attempt at a time and a 30-second per-node cooldown. This does not establish the original reset cause.

Both converged and valid nonconverged position solutions now pass through the EKF. Fresh held observations remain estimated between generations; their measurement age is unchanged. After at least 0.5 s of stable distances, a relative reference triangle supports independent weighted soft-L1 trilateration of other nodes. Internal distance changes release the core; missing/unsuitable cores fall back to SMACOF. This targets one moving node among four. Common rigid motion remains unobservable, and motion below the 8 cm reference consistency threshold may still be misclassified. This patch does not use accelerometer data.

Firmware build: 189864 bytes flash, 58573 bytes RAM. Signed hash: 84f65716457a2e2920cee6ca616426a55c9077726ac53999b6050f344f5fafc4. Synthetic checks exercised each of four moving-node choices and 200 full-engine frames including held observations. These are implementation checks, not hardware accuracy evidence. Live captures: build/uwb3d/hardware-five-com9/live-123.


Final live session: live-123d. All five boards completed OTA and confirmed 1.2.3 (batch report build/batch-ota/20261019T073612384506Z/results.json). Two BLE reconnects to mobile 1 preserved running=1, increased TX from 491 to 635, and retained missed=0. During live-123d, mobile 2 returned with all swarm counters/configuration zero and reset reason 0; the monitor restored it at elapsed 33.23 s. The physical/reset cause is unresolved; recovery is not prevention. Reference bootstrap now needs only one stable triangle, so a fourth noisy node cannot block reference acquisition. Invalid independent trilateration marks only that node unavailable, retaining supported reference-node estimates. Current four-node availability/latency metrics are in live-123d/hardware-metrics.json; no externally measured position accuracy or user-confirmed movement validation is claimed.

## Continuity correction (2026-10-20, host only)

The later motion-node2 session used explicitly selected stationary references
1/3/4 and a mobile node 2. Its log contained 51,534 published states, including
29,662 reference-consistency failures. These failures are not proof of radio
packet loss. Reference geometry/ranging quality and spontaneous board resets
remain unresolved; the user reports cables remained connected.

The publication path incorrectly exposed cached raw solver positions when a
node became stale or unavailable, after previously displaying filtered points.
Recorded node-2 steps reached 0.8936 m. Swarm now holds the last filtered
coordinate through those transitions, preserving stale/unavailable status and
measurement age. EKF reacquisition no longer resets directly to a distant
observation after 0.5 s; position corrections use the existing 1 m/s model,
bounded by the publication interval. Gated, limited, and reacquiring states are
reported as estimated. This can add settling delay and does not establish
correct physical coordinates.

Three regression tests in tests/test_swarm_continuity.py pass. Replaying the
recorded solver observations through the corrected display/EKF stage changed
steps over 15 cm from 153 to zero, maximum step from 0.8936 m to 0.0940 m, and
stale-transition movement to zero. See
build/uwb3d/hardware-five-com9/motion-node2/continuity-replay.json.
This replay does not rerun radio collection or validate physical accuracy.

At the time of this correction COM9 was absent and no Control Center process
was running. UART info verified COM13 is PCB_F42728EC (mobile 4), COM14 is
PCB_09EC3021 (mobile 1); neither is the enrolled sniffer PCB_854655FD. Live
verification of this correction awaits reconnection of the sniffer.

COM9 was subsequently reconnected and UART identity confirmed PCB_854655FD.
The first live restart exposed an additional trilateration initialization bug:
the centered reference centroid was approximately (-7.4e-17, -3.7e-17).
The local TRF solve stopped after two evaluations at this tiny seed, with
0.529 m range residual. Starting exactly at zero on the same captured ranges
found a solution with approximately 0.122 m residual. The solver now snaps
near-zero warm starts to exact zero and also evaluates a linear
squared-range-difference seed, selecting the successful fit with lower robust
cost. This addresses optimization failure, not range calibration.

Four continuity/trilateration regression tests pass. The current live output
is hardware-five-com9/motion-node2-continuity-v2; the first post-start check
reported four supported positions, six links, 306 range observations/s and
123.5 ms measurement-to-publication latency. The fixed references remain
1/3/4 and only node 2 should move during this controlled trial. No physical
position-accuracy claim follows from these rates or range-fit residuals.

## Stationary noise investigation and optional acceleration (2026-10-20)

The user agreed to keep the boards still for a 60-second capture. Raw link
standard deviations were 0.145 m for 1-2, 0.016 m for 2-3, 0.047 m for 2-4,
and 0.102 m for 3-4. Node-2 displayed XY standard deviations were approximately
0.129/0.126 m. No reference distances were provided, so these quantify
repeatability, not absolute accuracy. Capture/analysis:
tmp/swarm-rest-capture.json and tmp/swarm-rest-capture.log.

A 200 ms robust range-window/reweighting experiment did not reduce position
scatter in replay (approximately 0.134/0.129 m), so distance smoothing and
reweighting were removed from the active solver. The short-window helper is
used only for checking range trends, not as an asserted accuracy improvement.

Firmware 1.2.4 optionally probes the integrated LIS2DH12 at I2C 0x18/0x19,
requires WHO_AM_I=0x33, and configures BDU, 50 Hz, +/-2g high-resolution mode.
It reads after successful own TX only for fleets of at most six mobile nodes;
I2C failures disable sampling, and stale samples are not marked valid.
It does not change GPIO pull-ups, BNO085 reset, or radio timing parameters.
Header flags bits 1/2 carry fresh-sensor/motion status; bytes 37-39 carry
delta mg, quiet duration /20 ms, and sample age /10 ms. Existing 1.2.3
sniffers forward these bytes unchanged. Host parsing remains backward compatible.

The host treats quiet acceleration as a hint, not proof of zero velocity.
ZUPT additionally requires two fresh, low-noise, low-slope range trends and
at least 800 ms quiet acceleration. Constant-velocity motion must remain
trackable even with quiet acceleration. Six regression tests pass, including
this distinction. No acceleration integration into absolute position is used.

Primary sources: https://www.st.com/resource/en/datasheet/lis2dh12.pdf and
https://forum.qorvo.com/t/dwm3001c-module-has-built-in-imu-or-accelerometer/25291.
Signed 1.2.4 image hash:
4a890d7726c793591d0890ded46bc19aad58ff520cfb705d8878786c22ae4000.
Only PCB_76A10829 (node 2) is selected for this trial update.

Hardware verification succeeded after OTA (report:
build/batch-ota/20261020T092247260387Z/results.json). Node 2 reported
LIS2DH12 ready=1 at 0x19, 1053 samples, zero I2C errors; simultaneous radio
status showed 1054 successful TX and zero missed slots. The host received
fresh sensor fields through the unchanged 1.2.3 passive sniffer.
In one 30-second sensor-quiet subset, displayed XY standard deviations were
0.0025/0.0028 m; this is filtered repeatability, not physical accuracy.

The user subsequently reported continued settling drift after movement,
despite an accurate/stable eventual endpoint. A host bug prohibited
reacquisition while marked stationary, allowing a lagging state to reject
consistent new positions. Rest entry now zeroes residual velocity, clears
position/velocity cross covariance, and permits reacquisition after six
coherent observations. Bounded corrections retain the covariance of the
remaining position discrepancy, rather than becoming overconfident before
reaching the observation. The IMU gate switch is respected. Seven regression
tests pass, including rest reacquisition and constant-velocity discrimination.

Current running session: hardware-five-com9/motion-node2-accel-stop on COM9,
references 1/3/4 and mobile node 2. The first post-start check had four supported
positions, six links, 303 ranges/s, 117.5 ms measurement-to-publication latency,
and fresh acceleration on node 2. No claim of less-than-5-cm physical error or
completed motion qualification is made.

2026-10-21 restart: the previous process was no longer running and its snapshot
was from the prior PC uptime. COM9 UID a25545db854655fd was verified before
starting hardware-five-com9/motion-20261021 with the same 1/3/4 references.
All four mobile nodes returned, including fresh LIS2DH12 data from node 2.
Seven continuity regressions pass. No new firmware or filter tuning was deployed.

The new session exposed sustained reference-range inconsistency: a 30-second
window had only 55.3% four-position availability despite median 303 range
observations/s and 120.5 ms measurement-to-publication latency when valid.
One snapshot's reference ranges differed from the startup survey by 0.264,
0.257 and -0.168 m (3-4, 1-3, 1-4 respectively), all reporting quality 1.
This is not proof of physical reference movement. The operator was asked to
confirm which boards moved. A replay comparing instantaneous reference RMS
against a 300 ms median rejected 923 vs 942 of 2770 frames; therefore the
median modification was NOT deployed. The fixed gauge and rejection threshold
remain intact rather than silently absorbing these discrepancies.

tools/swarm_motion_report.py now summarizes a bounded time window from a live
or completed session, separating fresh acceleration phases and reporting
availability, raw/display discrepancy and display steps. These statistics do
not measure absolute position accuracy or establish physical rest. Reports for
this session are motion-report.json and hardware-metrics.json in its directory.

Follow-up: reference health is now separated from mobile observation validity
in the explicitly selected fixed-reference mode. After the initial survey,
missing or inconsistent reference-reference links no longer discard independent
fresh mobile-reference ranges. The reference coordinates are preserved; no
automatic re-survey or coordinate deformation is performed. Such solutions are
explicitly marked estimated, with reference_degraded/reference_warning metrics
and a visible status reason that the selected references are assumed fixed.
Reference RMS contributes to reported conditional uncertainty. This does not
establish that the physical references stayed fixed, nor fix range bias.
Three fresh mobile ranges, residual and geometry checks remain mandatory;
actual outages remain unavailable/stale. This change is restricted to the
operator-selected reference mode, not the all-mobile SMACOF solver.

A bounded replay of the final 8 MB of the prior live log evaluated 1416 states:
all had solvable mobile ranges; 1329 were previously rejected solely by
reference health and now produce conditional estimates. Nine continuity tests
pass, including reference-health degradation, true mobile-link loss and
preservation of genuine stale states. Live session motion-reference-health
started on COM9 with all four nodes and fresh node-2 accelerometer data;
initial valid samples showed median 306 ranges/s and 121.5 ms publication
latency. A new operator motion trial is pending. Absolute accuracy and removal
of post-motion drift are not yet established.

The operator confirmed that stale/freezing disappeared but substantial drift
remained. Captured raw trilateration alternated between minima separated by
roughly 0.6–0.8 m, including with fresh quiet acceleration. The reference
triangle's centered singular values were 1.192 and 0.155 m in that session.
This is a branch-selection problem in addition to filtering latency.

MotionReferenceSolver now prefers the solution nearest its previous result
only when competing successful fits are separated by >0.20 m and their robust
costs differ by at most N*(0.05 m)^2/2. A clearly better fit is still allowed;
the previous branch is a continuity prior, not proof of the true position.
The node-quality output flags ambiguous_branches. The EKF also stops coasting
after 120 ms without an accepted observation while retaining uncertainty and
coherent reacquisition. No position freeze is based solely on quiet acceleration.

Same-record replay (tmp/drift-branch-replay.json): 2963 solvable frames, raw
steps >0.30 m decreased from 201 to 80, with 109 ambiguous frames. This is
discontinuity reduction, not an accuracy result; RF-related jumps remain.
Eleven regression tests pass, including ambiguous-branch continuity during
real motion and bounded coasting through rejected observations. The new live
session is hardware-five-com9/motion-branch-continuity on COM9.

Camera-free direct-range estimator: tools/swarm_range_tracker.py implements
per-mobile robust iterated EKF states [x,y,vx,vy]. The selected-reference mode
now uses trilateration only for initialization. Subsequent observations use
h_i(x)=norm(p+(t_i-t_epoch)*v-anchor_i), with t_epoch the newest effective
range timestamp. Each link timestamp is consumed once; duplicate host frames
do not reduce covariance. Four relinearizations use Huber innovation weights
through inflated range covariance and a Joseph covariance update. Prediction
uses a CV model, with 1 m/s speed/step limits and bounded outage prediction.
Quiet acceleration plus quiet range trends supplies the existing relative-rest
hint. There is no acceleration integration and no second position-domain KF.
Reference mismatch warnings, fixed gauge and missing-mobile-range checks remain.
All-mobile SMACOF mode remains available and unchanged.

Validation: 12 swarm tests pass, including actual stop-and-go with asynchronous
range epochs, 2 cm Gaussian range noise, one 1.5 m outlier, covariance PSD and
duplicate-measurement invariance. Synthetic RMS was 0.01095 m; this is not a
hardware accuracy claim. Same recorded input produced 2966 direct-range
solutions with zero >30 cm steps vs 201 raw trilateration steps; step bounds
contribute to this metric, which cannot establish accuracy or lag reduction.
Live output: hardware-five-com9/direct-range-iekf, COM9, references 1/3/4,
mobile 2. No camera or firmware update was required. Full moving-anchor
estimation and a larger independently calibrated anchor array are not yet
implemented by this change.

Two-tag hardware trial, 2026-10-21: newly powered PCB_642D2C6D, hardware UID
92e677d5642d2c6d, BLE E3:31:36:AD:13:3A, was found on firmware 1.2.1.
It was OTA-updated to the signed 1.2.4 image and verified/confirmed successfully
(build/batch-ota/20261021T060741998571Z/results.json; 117.28 seconds for board).
The six-board roster is hardware-six-com9/roster.json. New CLI option
--preserve-roster-order keeps old identities unchanged: sniffer 0, references
1/3/4, mobile tags 2 (PCB_76A10829) and 5 (PCB_642D2C6D). Default alphabetical
ordering is unchanged for existing launch commands. The five-radio schedule
is configured on all nodes; COM9 remains the passive sniffer. Output directory:
hardware-six-com9/direct-range-multitag. Thirteen swarm tests pass, including
independent two-tag states and per-link measurement histories.

BNO085 / all-mobile implementation (2026-10-21): firmware 1.2.5 initializes
the existing SH-2 driver before ranging, then services it in a 4096-byte,
priority-5 worker. A mutex serializes SH-2 init/service. Radio timing remains
in the higher-priority main loop. Only snapshots of decoded data are read
from the TX path; no BNO I2C transfer is performed there. Five-node operation
remains the current hardware qualification target.

Wire protocol stays version 1 and header size 40: flag bit 3 identifies BNO085
motion data (requires fresh-data bit 1). score_mg is linear acceleration
magnitude for BNO085, rather than LIS acceleration delta. Gyro magnitude
>0.12 rad/s or linear acceleration >0.18 m/s2 resets the quiet timer. Reports
must have SH-2 accuracy >=2 and age <=150 ms. Host freshness remains <=100 ms
sample age and <=250 ms receipt age. LIS2DH12 is a labelled fallback; a host
cannot mistake it for BNO085. The unchanged sniffer forwards these bytes.

No quaternion-based dead reckoning is enabled: node 2's initial hardware
diagnostic reported quaternion/magnetometer accuracy 0 even though linear
acceleration/gyro accuracy were high. BNO motion norms adapt the process noise
and corroborate relative rest; they are not integrated as global acceleration.
This intentionally avoids asserting an unverified shared heading.

tools/swarm_cooperative_tracker.py jointly estimates [x,y,vx,vy] per node from
inter-node ranges, with timestamp offsets, robust iterated updates, Joseph
covariance, unique-link observation consumption and bounded corrections.
SMACOF initializes the relative shape; no node coordinate is fixed afterward.
The state/covariance are conditioned on zero centroid and mean velocity.
Absolute translation and global heading remain unobserved. Common movement
of the whole group therefore cannot be read as world displacement. Individual
relative-rest hints require BNO/LIS quiet plus quiet trends to every peer.
All-mobile mode is selected by leaving reference_nodes empty; fixed-reference
mode remains available. Swarm_All_Mobile.cmd starts the six-board COM9 roster.

Firmware signed hash (1.2.5):
3734921ea4a2e953cb8e37ec2c60a756fc7ea9f77d040758e9b1d435b214ac6f.
Flash 191600/200192; RAM 62865/131072. Node 2 OTA was verified/confirmed:
build/batch-ota/20261021T074445870351Z/results.json. Its concurrent smoke test
reported 1167 decoded IMU reports, zero comm errors/missed reports, and UWB
262 TX, one missed slot. A subsequent 10-second sniffer capture received
265 node-2 frames with source=BNO085, alongside all four other radios.

Fifteen targeted swarm tests pass, including all five nodes moving, common
translation gauge, range outlier rejection and BNO wire-source validation.
The broader test runner passed portable C checks after repairing missing
swarm mocks in the listener harness; 112/114 Python tests passed. Two legacy
test_uwb3d tests still expect no output for incomplete/disconnected graphs,
where the existing base engine now returns partial-graph estimates. Neither
failure exercises the new swarm estimator; they are not reported as passing.

Remaining four mobiles were successfully OTA-updated and confirmed in
build/batch-ota/20261021T074800050800Z/results.json (4/4, 331.69 seconds).
Together with the pilot this updates all five ranging nodes to 1.2.5; the
passive COM9 sniffer stays on 1.2.3. Live all-mobile output directory is
hardware-six-com9/all-mobile-bno, with no reference_nodes configured.

Live verification after configuration: five fresh BNO085 sources, ten links,
no fixed reference IDs. In a 29.969-second window all 875 snapshots had five
supported positions, median publication rate 30 Hz and median measurement
latency 122.5 ms. hardware-metrics.json records this availability result.
The operator motion trial was requested; physical accuracy improvement and
post-motion settling time are not yet established by these metrics.

Display-frame correction after the operator moved only node 0002: the previous
centroid frame necessarily displaced every other displayed coordinate. Also,
requiring quiet ranges to every peer excluded stationary nodes whenever one
peer moved. CooperativeRangeTracker now finds a mutually quiet subgroup of
at least three nodes: fresh IMU quiet evidence plus stable pairwise range
trends inside that subgroup. A changing link to a moving peer does not veto it.
Members are released immediately when their motion/freshness checks fail;
these are temporary frame references, not permanently configured anchors.

MovingFrameAligner uses a proper rigid Procrustes transform (det R > 0) to
align that subgroup to its last displayed coordinates. It changes no pairwise
distance and cannot hide shape deformation or range bias. A non-collinear
subgroup is required; otherwise the last transform is retained and the UI
explicitly shows RELATIVE XY instead of STABILIZED XY. Internal filtering
still uses a centroid gauge; returned/logged/displayed positions use the
adaptive frame. Global rigid group motion remains externally unobservable.

Seventeen swarm tests pass. The single-moving-node regression moves 0002 by
0.6 m while all four other nodes remain within 0.04 m of their initial shown
positions, and releases the subgroup when it moves. A separate test proves
all distances are preserved by display alignment. These are synthetic checks,
not physical accuracy claims. Live output: hardware-six-com9/all-mobile-stabilized.

2026-10-22 follow-up: after another report of movement error, the old process
was found closed, its latest snapshot from prior PC uptime, and COM9 absent.
The operator reconnected the sniffer; UID a25545db854655fd was reverified.
Old log also showed node 2 falling back to LIS2DH12. A quiet frame subgroup now
requires fresh BNO085 evidence specifically and 2 seconds quiet, plus the
existing pairwise range-trend corroboration; LIS remains usable as telemetry
but cannot establish a stationary frame subgroup.

The range IEKF now uses a Schmidt/consider-state update for the corroborated
quiet subgroup: zero its Kalman-gain rows, while retaining its uncertainty in
innovation covariance and the Joseph covariance update. Thus moving-peer range
errors do not deform the quiet group. Members are released on motion/freshness
failure, and there is a one-second warmup before this conditioning begins.
This is conditional stationary modelling, not proof of absolute rest; wrong
stationarity decisions can still bias the estimate. No new firmware is needed.

Same-record bounded replay (tmp/consider-state-replay.json), 937 frames:
quiet-node per-frame step p95 0.00540 m -> numerical zero by construction;
median range-fit RMS 0.05092 -> 0.05734 m. No physical ground truth was supplied.
The 17 targeted tests pass, including 8 cm oscillatory errors on a moving tag's
ranges without pulling its quiet peers, and release when those peers move.
Live output: hardware-six-com9/all-mobile-consider-20261022 on COM9.

Further user motion complaint: a live log tail contained 499 frame-subgroup
transitions. Quiet membership was recomputed from noisy ranges on every solve,
undoing the prior consider-state protection. Membership now has hysteresis:
fresh BNO085 quiet plus a mutually stable subgroup must persist 0.2 s to join;
BNO motion releases immediately; BNO data loss releases after 0.35 s; two
coherent range trends to other retained peers must exceed 0.06 m/s for 0.25 s
to release without IMU motion. Known moving peers are excluded from this
range-only release decision, so two moving tags do not evict static peers.
This still cannot distinguish common rigid-body movement from physical rest.

Replay before the final 0.25 s range-release dwell used 1408 identical frames:
group transitions 656 -> 115, stabilized coverage 95.5% -> 90.8%. It does not
establish positional accuracy. Eighteen swarm regressions pass, including
retention through missing quiet-range evidence, immediate IMU-motion release,
no unsupported rejoin, and stale-IMU release. Live output directory is
hardware-six-com9/all-mobile-latched-20261022. Full vector inertial navigation
remains unimplemented: protocol v1 sends linear acceleration magnitude and a
gyro-derived motion flag, not full acceleration/quaternion vectors. The current
fusion is a UWB estimator conditioned by BNO085 motion evidence.
