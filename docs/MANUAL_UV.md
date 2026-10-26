# Manual UV control

Firmware: swarm 1.2.7. The controls appear above their own live multi-tag canvas in the dedicated
UV / RGB Control tab. Click a node marker to toggle its selection. Multiple
nodes remain selected with white rings; Clear selection deselects all.

- UV left: left on, right off for every selected node.
- UV right: left off, right on.
- UV both: both on.
- LED off: both off (also clears supported RGB outputs).
- Read LED: reports firmware output state; it does not measure emitted light.

Responses are displayed per node below the controls. A failed board is not
reported as successful, and other selected boards are still attempted. The
same radio fleet keeps running during manual LED commands. BLE recovery and
LED commands share a lock to avoid simultaneous connections to the board.

Outputs latch until another command or a board reset. They default to off
at boot, with no automatic activation during firmware update. Wiring follows
HARDWARE.md: UV left P0.30, UV right P0.02, active high MOSFET inputs.

The RGB dialog supports physical R1/G1 and R2/G2/B2 binary on/off. Physical
B1 is disabled: its firmware pin label is R1, wired to DW3110 GPIO3 rather
than an nRF GPIO. The observed-colour correction below governs UI labels. Choose RGB only
when that daughterboard is fitted; this is not PWM brightness control.

Quiet lock: a fresh IMU Quiet indication maintained for 0.4 s pins a recently
accepted display position. It releases on movement, stale IMU, invalid position
quality or missing node. Raw UWB estimates remain unmodified. This rejects
stationary display drift but does not calibrate absolute ranging error.


## Observed RGB colours and low-latency commands (2026-10-26)
User observation supersedes schematic colour names: old R emits blue, old G
emits red, old B emits green. Control Center maps requested (R,G,B) to firmware
(B,R,G), independently for each RGB package, and maps status back. Firmware
1.2.7 raw pin commands are unchanged. Consequently B1 is physically unsupported,
not R1; the UI permits red/green on package 1 and full RGB on package 2.

The fleet retains up to two identity-verified BLE control sessions. Commands
to the same pair reuse connections; sends within a pair run concurrently.
Selections larger than two run in batches. A new connection verifies UID and
LED capability once. SET commands wait for their firmware acknowledgement but
do not issue redundant info/status reads; Read LED explicitly reads back state.
Per-node elapsed time and new/warm connection information appear in results.
Failures discard that connection without automatically repeating an uncertain
SET. Recovery closes the target's cached session, and Stop/shutdown closes all
cached sessions before stopping radios. UV mappings are unchanged.

Live validation: nodes 0004/0005 acknowledged initial OFF in 3297/4031 ms;
repeat OFF using retained connections took 141 ms on each node. Tracking
remained at six nodes and fifteen links. These are two observed command
round trips, not a guaranteed latency bound. No new physical colour
observation was available; permutation follows the user report.
