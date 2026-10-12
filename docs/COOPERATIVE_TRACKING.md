# Sparse cooperative multi-tag tracking

Firmware 1.0.13 and the **Multi-tag Cooperative** tab replace the previous automatic anchor-survey workflow. The manual **Four-anchor Tracking** tab remains available. All coordinates are calculated on the host from actual UWB distances, including tag-to-tag distances; all anchors and tags participate in one joint estimate.

## Run

1. Install `tools/requirements.txt` (includes NumPy) and update every participating board to 1.0.13.
2. Open `Control_Center.cmd`, scan and select 5–12 boards (four anchors and up to eight tags).
3. Open **Multi-tag Cooperative**, click **Load selected boards**, and select any four boards as anchors.
4. For the flat-table test choose **2D / ALL boards in the same plane**. Three-dimensional mode requires suitable noncoplanar anchor geometry.
5. Click **Start tracking**. The map shows a top-down view, chosen links and tag neighbors. Purple links connect tags. Expand **Measurement details** for actual distances and link statistics.
6. Stop before changing membership or geometry mode. LED3 is solid for anchors and blinks for tags, even when a tag initiates a cooperative measurement. LED2 is charger controlled.

## Calculation and schedule

The four-anchor backbone has six edges. Each tag chooses three predecessors in 2D, or four in 3D, from anchors and earlier tags. This lateration structure limits the graph to `6 + 3T` or `6 + 4T` active edges rather than measuring every pair. The solver still verifies observability; an edge count alone does not prove usable geometry.

Neighbor selection combines successful-slot history, robust distance dispersion, previous fit residuals and geometric spread. Hysteresis limits unnecessary switching, while weak-link reliability penalizes retaining a failed peer. One extra exploration edge every third cycle allows previously excluded neighbors to recover. A usable tag-to-tag edge receives a small selection preference; it is not forced if a better geometry or reliable link is needed.

The host grants one measurement at a time and waits for its acknowledgement before granting the next. The firmware's `uwb cooperate` mode allows both logical roles to initiate an addressed range and respond to allowlisted peers. The schedule is serialized over BLE; it is **not autonomous, clock-synchronized firmware TDMA**. Recoverable radio failures get at most two retries, each after 25 ms. A delayed BLE command delays the schedule.

Every cycle refreshes the selected tag edges and the oldest two backbone edges. Additional backbone edges are refreshed if they would expire before the predicted end of the cycle, using recent measured cycle durations plus a margin. Initially and after movement, all structural edges are reacquired. With four anchors and two tags in 2D, the selected graph has 12 of 15 possible edges; minimum steady cycles use eight slots, or nine with exploration. Freshness deadlines and retries can increase work. No fixed update rate is promised.

The host jointly refines anchor and tag coordinates with Huber-weighted Gauss–Newton, using current ranges rather than displaying the initialization. If a prior position seed fails after neighbor changes, it retries with fresh range-only lateration to escape a wrong local minimum. Both attempts require redundant constraints and a full-rank weighted Jacobian with condition number no greater than 1000. The fit is rejected if RMS exceeds 8 cm or any residual exceeds 15 cm. These are graph-consistency gates, **not position-accuracy bounds**, and differ from the old planar survey's 5 cm gate. A successful new fit cannot be counted as proof of improved accuracy against the old method.

Selected anchor edges expire after 4.5 s; selected edges involving tags after 2.5 s. A rejected or expired frame keeps the last coherent coordinates on screen, marked **HELD** with age. Before the first valid fix, no positions are invented. Anchor and tag positions from different accepted frames are never mixed.

## Moving anchors, IMU and limitations

Fresh calibrated BNO085 motion reports invalidate earlier ranges when an anchor moves; the scheduler then reacquires the graph. The joint solver also recalculates anchor geometry from UWB observations. This supports re-estimation after repositioning, not validated high-speed mobile-anchor tracking. Measurements within a cycle are asynchronous, so moving objects can distort a frame.

The IMU is used as a motion cue, not an absolute position sensor or an inertial dead-reckoning substitute. Constant velocity, lost reports and sensor errors can evade the motion check. The cooperative solver uses raw latest UWB distances, with observed variation affecting weights; it does not claim a validated UWB/IMU navigation filter.

The coordinate frame is relative: A001 is the origin, A002 defines +X, A003 selects positive Y, and A004 selects the Z sign in 3D. Whole-fleet translation, rotation and reflection cannot be determined from inter-board distances alone. In 2D all boards must share a plane. Rubble and non-line-of-sight propagation can bias even mutually consistent ranges. Sub-3-cm accuracy requires independent ground truth and calibration and has not been established.

The implemented UI limit is four anchors plus eight tags; six boards are the current hardware test target. Thirty-board OTA capacity does not imply thirty-board simultaneous tracking support.

## Evidence and reproduction

Session logs contain `COOP-SLOT`, `COOP-FRAME`, raw console telemetry and `cooperative-plan.json`. Run a standalone hardware test with the GUI fleet stopped:

```powershell
python tools/cooperative_test.py --plan build/four-anchor-tests/multi-plan.json --output build/cooperative/hardware --cycles 20
```

The test saves actual slot values, graph decisions, frame validity, timings, firmware role status, IMU status and cleanup errors. A valid fit is not a ground-truth accuracy test. `python tools/run_tests.py` checks synthetic recovery, tag-to-tag constraints, neighbor replacement, exploration, stale/motion handling, degeneracy, outliers, 3D geometry, held display and serialized slots, as well as the existing regression suite.
