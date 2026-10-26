# Live motion correction

Applied directly to the six-board sparse session, without running automated
tests, as requested. Firmware remains 1.0.18; changes are in Control Center.

The earlier static profile mixed link measurements up to seconds apart, used a
slow range low-pass, and aligned the display using all nodes. Those choices can
lag motion and distribute one node's movement across the displayed frame.

The live gateway launcher now enables `motion_profile`:

- Reject measurements older than 450 ms in this session and keep only links
  within 150 ms of the latest effective measurement timestamp.
- Use range cutoff 1.5 Hz + 4 × estimated range speed. Disable the static Hampel
  step guard and triangle rejection in this profile; motion is not a spike.
- Disable the additional CV position filter/velocity extrapolation in the live
  launcher and lower temporal regularization to 0.01.
- Align against a leave-one-out stationary majority, so a moving board does not
  force the entire displayed frame to follow. This assumes a mostly stationary
  group; collective motion remains unobservable from distances alone.
- Initialize planar sparse graphs from a measured triangle and three-neighbor
  lateration, then optimize measured edges and verify rank/residuals as before.
  No old link is fabricated to fill the missing fifteenth pair.

Physical reception confirmed six nodes, roughly 137 ranges/s, 10–11 solver
updates/s, and about 120 ms measurement span. This is operation evidence, not
ground-truth moving-position accuracy. User-observed movement feedback remains
necessary. The tabletop setting still assumes Z=0 and cannot track lifting a
board out of the plane correctly.

Session: `build/uwb3d/hardware-six/sparse-motion-oct13-v2/`.

## User-confirmed moving node

The user clarified that the board attached to ST-Link is node **0003**, not
the UART gateway node 0001. COM10 identifies the gateway's UART, not the
ST-Link-connected board. The latest live session uses `--moving-node 3`.

Frame alignment now references a fixed first accepted geometry, rather than
the preceding estimate, and excludes node 3 from the alignment fit. This
prevents cumulative frame drift and keeps the moving node out of the display
reference. It does not freeze the other five measured positions. Reset frame
or a changed online roster establishes a new reference.

Latest session: `build/uwb3d/hardware-six/sparse-motion-node3/`. Live data showed
six nodes, 137 ranges/s, 10 position updates/s and 110 ms measurement span.
No automated tests were run for these changes, per the user's instruction.
Moving-position accuracy still requires the user's physical observation.
