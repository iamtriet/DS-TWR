# Automatic anchor tracking

The **Auto-anchor Tracking** tab measures the four anchors against one another,
then calculates tag positions using that measured relative layout. No manual
anchor coordinates are required. Firmware 1.0.12 or newer is required on the
anchors; use the same release across the fleet.

## Operation

1. Open `Control_Center.cmd`, scan, and select four anchors plus one or more tags.
2. Open **Auto-anchor Tracking**, press **Load selected boards**, and assign the
   four anchors using the selectors. Other selected boards become tags.
3. Choose a geometry mode. For a flat-table test, explicitly select **2D / ALL
   boards in the same plane**: both anchors AND tags must share that plane.
   Otherwise use **3D / anchors at different heights** and spread the anchors in
   three dimensions. Four coplanar anchors cannot provide the unambiguous 3D
   solution used here. The application never silently falls back from 3D to 2D.
4. Press **Start tracking**. Each cycle measures all six anchor pairs, ranges each
   tag against all four anchors, then repeats the six pairs in reverse direction.
   After the first cycle, its closing survey also opens the next cycle, avoiding
   six duplicate exchanges. Survey directions alternate to retain reciprocal
   checking. Motion evidence overlapping the reused survey is retained.
5. After an anchor shifts and settles, subsequent cycles automatically use the
   new layout. There is no manual resurvey step. **Stop** stops every board.

Anchor LED3 remains solid and tag LED3 flashes. LED2 is the charger status LED
and is not MCU controllable on this PCB.

## Meaning of the top-down view

A1 is the origin, A2 defines positive X, A3 defines the positive-Y half of the XY
plane, and A4 selects positive Z. These axes follow the anchors. The view is a
projection onto this relative XY plane, not necessarily the physical floor.
The displayed relative Z is not gravity height. A rigid translation or rotation
of all boards, and the global mirror ambiguity, cannot be recovered from pair
distances alone. Historical world-space trails are therefore disabled in this
tab. The existing surveyed **Four-anchor Tracking** tab is still available.

In explicit 2D mode, all Z values are fixed at zero and a least-squares fit uses
all six anchor edges. This adds a planar consistency check: no fitted edge may
differ from its measurement by more than 5 cm. Zero Z is a user-selected plane
assumption, not a measured height. Change back to 3D for nonplanar deployment.

## Measurement checks and limits

- All six anchor edges must be available before AND after the tag measurements.
- Corresponding distances must agree within 5 cm. This is a rejection threshold,
  **not a claim of 5 cm or 3 cm positioning accuracy**.
- A complete cycle must span at most 5 seconds. Positions expire 5 seconds after
  the cycle began. The four readings for a tag must span at most 1 second.
- While an accepted anchor layout is still fresh, each tag can update as soon as
  its four readings arrive, before the next closing survey completes. This uses
  the previous accepted layout; it does not extend its validity. Anchor motion,
  expiry, or a rejected closing survey marks the display held. Tag cards show
  position age separately from anchor-survey age.
- Triangle inequalities, finite positive distances, minimum separation, and 3D
  geometry conditioning are checked. Tag fit RMS must be at most 15 cm, inherited
  from the surveyed solver. These are initial engineering gates, not calibrated
  confidence intervals.
- BNO085 acceleration/rotation evidence invalidates a layout during anchor
  movement. A new cycle can recover after movement subsides. IMU data is not used
  as an absolute position source; missing IMU data is shown in Measurement
  details, and ranging consistency checks still operate.
- Incomplete, expired or rejected cycles retain the last valid display snapshot,
  labelled **HELD** with its age. Its anchor layout and tag coordinates stay
  together; old tag positions are never mixed into a newer anchor frame. A
  complete valid result replaces the snapshot automatically. The live solver
  still rejects invalid data. Before the first valid fix, no position is invented.
  Changing the board selection, geometry mode or starting a new run resets the
  display history. The tab also shows the current survey rejection reason and
  all measured before/after edges.
- This is intended for **shift, settle, recalculate** operation. Continuously
  moving anchors, constant-velocity motion missed by the IMU, movement out and
  back within one cycle, or changing obstructions may invalidate the assumptions
  despite passing these checks. Measurements are sequential, not simultaneous.

Consistent distances can still be biased. Six edges are enough to define a
tetrahedron, but are not independent proof that its geometry is physically
correct. Rubble/NLOS can bias both anchor and tag ranges. Qorvo discusses this
in [APS006, channel effects on ranging](https://forum.qorvo.com/uploads/short-url/bVkGY18xEsijUq0OAm6iU64JeqF.pdf).
No rubble-specific accuracy or sub-3-cm accuracy has been validated by this
implementation. Validate against known physical
positions and obstructions before relying on the map.

## Implementation and verification

`uwb listen` enables a passive responder on a logical anchor with an explicit
peer allowlist. `uwb range ID` temporarily initiates a synchronous DS-TWR exchange
and then returns to listening. Role and LED remain unchanged. Ordinary continuous
initiators still reject concurrent manual range commands. OTA and stop guards
are preserved.

`tools/auto_anchor.py` contains scheduling, the tetrahedral embedding, snapshot
validation, and the dynamic estimator. `tools/multi_anchor_view.py` supplies the
dedicated tab; `tools/topdown_map.py` renders the relative map. `tools/run_tests.py`
includes synthetic geometry/motion/staleness tests and compiled firmware tests
for listener mode, role preservation, scheduling, stop, and OTA guards.

BLE interruptions trigger connection and role restoration, including tag IDs,
allowlists, continuous responder mode, and IMU streaming. A restarted board is
reconfigured before the next ranging cycle. Recovery clears the survey reuse
cache. Repeated unsuccessful recovery stops the loop with a visible error.

Hardware evidence is written under `build/auto-anchor/`; synthetic UI checks
are named `synthetic-ui.png` and must not be treated as live physical results.
