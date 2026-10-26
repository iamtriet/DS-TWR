# Return to the project's sparse cooperative tracking

The six-board fleet returns to signed firmware 1.0.18, preserving its scheduled
UWB protocol (100 ms superframes, 10 ms slots). The reference TDMA port remains
available separately; it is not active in this session.

Host improvements:

- Adaptive per-link filtering and triangle consistency checks from the recent
  work are enabled before the existing relative solver. Raw measurements remain
  in recordings. See REFERENCE_TDMA.md for filter parameters and limitations.
- The six-node scheduler keeps the four-predecessor scaffold. After bootstrap,
  node 6 prefers four of its five possible peers using freshness, interpolation
  residual noise, and the geometry consistency flag. This score is a heuristic,
  not a measured radio NLOS probability.
- Every third 0.5-second selection probes the weakest peer. Other omitted peers
  rotate during these probes. Until each link has five samples, the original
  rotating survey is retained. Firmware still performs the actual time slots;
  the PC only changes the neighbor mask.
- This changes node 6's peer selection for the current six-node fleet. It does
  not imply full adaptive scheduling for arbitrary larger fleets; their existing
  geometry-based policy remains in place.

Validation: 99 Python tests, including bootstrap, noisy-link preference,
periodic recovery probes and irregularly sampled linear motion. Physical-session
metrics are saved under build/uwb3d/hardware-six/sparse-filtered. No position
ground truth is available; graph fit residuals are not absolute accuracy.
