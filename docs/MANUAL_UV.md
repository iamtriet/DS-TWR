> Current hardware override (2026-10-26): user rewired physical BLUE 1 to
> nRF52833 P0.02. Firmware 1.2.8+ supports B1 and the UI enables all six RGB
> channels. Earlier unsupported-B1 notes below are historical and superseded.
> UV right and RGB B1 share that pin in mutually exclusive UV/RGB modes.

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

## Fast multi-board LED control — 2026-10-30

Firmware 1.2.9 includes physical B1=P0.02, packs the newline into BLE
notifications, omits the duplicate [led] response over BLE (retains @DONE),
and requests a 7.5–15 ms connection interval with zero peripheral latency.
Windows chooses the actual interval; this is not a hard realtime guarantee.
UART acknowledgements and UWB positioning are unchanged.

The UV / RGB Control tab also works without starting tracking. Scan BLE,
Ctrl/Shift-select boards in its list, and press Prepare BLE once. With tracking
running, select node markers instead. RGB presets or the six individual channel
checkboxes apply only to the current selection; UV buttons use the same selection.
Profiles are exclusive because UV right and RGB B1 share P0.02.

Up to six verified BLE sessions are retained across selection changes. SET commands
are sent concurrently and still wait for firmware acknowledgement. Reconnecting,
identity checks and capability discovery happen on first use or after disconnection,
not on every colour change. Disconnect LED releases the links; preparing them again
incurs connection setup time. For larger fleets connections are processed in groups
of six. A scan result is reused for 120 seconds to avoid implicit scans per connection.

Measured latency is host command dispatch through firmware acknowledgement, not
an optical measurement of the LEDs. The initial four-board bench measurement using
older firmware and new retained sessions was median 105 ms for the whole group,
12 successful rounds, versus seconds for cold BLE connection establishment.

Post-update four-board result (firmware 1.2.9): 100 rounds / 400 acknowledged
RGB OFF commands, zero failures; whole-group median 40.67 ms, p95 64.83 ms,
maximum 76.99 ms, minimum 27.71 ms. Initial preparation took 1.44–1.66 s
per board in this run and is excluded from warm-command timing.
Evidence: tmp/rgb-speed-after-20261030.json. OTA report:
build/batch-ota/20261030T093255945724Z/results.json.
Updated: PCB_09EC3021, PCB_76A10829, PCB_7A99EB74, PCB_642D2C6D.
PCB_F42728EC was intermittently visible and still reported older firmware;
it was not part of the four-board firmware update or final comparison.

## Integrated LED and tracking layout
The UV / RGB Control tab now always shows the RGB channel controls and colour
presets inline above the existing XY multi-tag map. No RGB popup is required.
Start UWB tracking and the sniffer UART selector are available beside the map.
Tracking uses the existing runtime, position gate and shared marker selection.
Live positions require an active UWB acquisition; BLE discovery alone supplies
board identities, not positions. RGB checkbox values are desired output values,
not automatic physical LED readback.

## USB/UART board identification
On opening the LED tab, idle Control Center identifies USB serial ports by sending
only `info` at 1,000,000 and 115,200 baud. Bluetooth virtual COM ports are excluded;
ports already used by this app are skipped, and busy ports are not taken over.
DTR/RTS are disabled before opening. Identity requires both PCB_VERSION and the
full 64-bit FICR ID; the low 32 bits form the BLE name PCB_XXXXXXXX.
Detect USB boards repeats detection after unplug/replug. A single match selects
its port automatically; with multiple wired boards choose the desired COM port.
Start UWB tracking re-reads the chosen port and matches its board to the BLE roster,
adding that discovered board to the selected fleet when needed. It no longer asks
for a manually typed sniffer ID. UART connectivity identifies a candidate collector,
not proof that the board is already configured as a passive sniffer.
Bench verified: COM9=PCB_F42728EC (1.2.7), COM14=PCB_09EC3021 (1.2.9).
