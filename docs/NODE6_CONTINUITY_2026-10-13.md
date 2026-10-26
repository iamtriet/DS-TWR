# Node 0006: live correction at about 70 cm

User reports node 6 is approximately 70 cm from the group, coplanar and without
occlusion. This number is contextual, not a known distance to any specific
reference, and is not used to force a range or calibrate an offset.

Live observations showed two problems beyond temporal noise:

- The position Kalman increased observed excursion slightly, while unfiltered
  positions already jumped substantially. It is disabled in this session;
  `--kalman` remains an explicit option. Adaptive per-link range filtering stays
  active, with no velocity extrapolation of displayed positions.
- Node 6 switched between position hypotheses about 1.8–2 m apart, sometimes
  with only three fresh distances or a changed neighbor set. A short distance
  does not eliminate weak bearing geometry or biased UWB ranges.

Final active profile:

1. Survey nodes 1–5 from their own measured ranges. Node 6 cannot deform that
   survey. The initial four-node reference subset was too close to collinear
   in observed data; the five-node core preserves the additional geometry.
2. Locate node 6 separately using four fresh reference distances, a robust
   nonlinear range residual and multiple initial hypotheses. Preserve the
   previous core solution as the next seed instead of rebuilding its orientation.
3. Preserve a proper planar rotation for the display frame; exclude moving
   node 3 from its alignment. Do not reflect the entire scene frame-to-frame.
4. Prefer geometrically diverse neighbors using the minimum eigenvalue of
   weighted bearing information. Keep a 20% switching hysteresis and periodic
   probe, rather than changing peers on small noise differences.
5. Reject sudden switches to distant hypotheses unless five consecutive
   consistent samples within the configured range-residual limit support reacquisition. A previous nearby solution is
   eligible only if it still fits measured ranges. Unavailable positions remain
   explicitly stale, rather than presented as fresh measurements.
6. Clear host assignment acknowledgements when the gateway restarts so the
   intended peer mask is reapplied; firmware defaults must not silently persist.

`node_quality` reports node 6's own range residual and conditional geometry
uncertainty. The main `rms` now describes the reference group, explicitly tagged
with `rms_scope`; a small core residual is not a claim of accurate node 6 position.
The raw ranges remain recorded. Systematic bias still requires ground-truth
calibration; smoothing or hypothesis continuity cannot establish absolute accuracy.

Applied directly on hardware without automated tests as requested. Latest run:
`build/uwb3d/hardware-six/sparse-node6-stable/`.
