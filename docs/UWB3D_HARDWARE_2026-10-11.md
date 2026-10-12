# Physical sparse BP qualification — 11–12 October 2026

This report records real BLE OTA and UART UWB experiments. Simulation results in
UWB3D_TRACKING.md are separate. Ground-truth distances were not supplied.

## Fleet and gateway

| ID | Board | UART |
|---|---|---|
| 1 | PCB_09EC3021 | COM10, gateway |
| 2 | PCB_642D2C6D | COM11 |
| 3 | PCB_76A10829 | — |
| 4 | PCB_7A99EB74 | — |
| 5 | PCB_854655FD | — |
| 6 | PCB_F42728EC | — |

Enrollment and BLE identities are saved in `build/uwb3d/hardware-six/`.
The user reports one board elevated approximately 20 cm. A stationary BNO085
cannot identify height. Height inference requires range geometry, and common
translation, orientation and reflection remain unobservable from ranges alone.

## Radio defects found during physical testing

- 1.0.14 read SYS_TIME as five bytes. The installed DW3110 driver returns four
  bytes representing bits 39:8. Corrected in 1.0.15.
- 1.0.15 restarted RX after each short software poll, losing beacons. 1.0.16
  preserves active RX across polling calls, without changing legacy RX semantics.
- 1.0.17 added slot diagnostics. Increasing arm lead from 4 to 8 ms did not fix
  missing slots: node 1 transmitted 26 of 136 slots; node 2 received 26 beacons but
  transmitted none, reporting approximately 53 ms lateness.
- **1.0.18 refreshes the latched SYS_TIME value before every read**, using a zero
  write to the write-one-to-clear SYS_STATUS register. Pending event bits are
  retained. Read-only polling previously kept the sampled time frozen until a
  later write, often at the roughly 61 ms hardware RX timeout.
  See the [SYS_TIME documentation discussion](https://forum.qorvo.com/t/feedback-to-the-dw3000-documentation/10506).

Host gateway setup now refuses firmware older than 1.0.18. Build: 193,088 bytes
flash, 59,233 bytes RAM. Signed image: 197,728 bytes, file SHA-256
`2668e81123d9abe7f7f363cb1fd8684a5d9c9eeb98ab1cde406c460c8b43fc5e`.
Bootloader, partitions, keys and OTP calibration are unchanged.

## Two-board acceptance sample

Evidence: `build/uwb3d/hardware-six/pair18-100/` (raw recording, states, counters,
measurement summary); OTA report `build/batch-ota/20261011T105316739242Z/results.json`.

- Both boards confirmed firmware 1.0.18 after OTA.
- Slot 10 ms, superframe 100 ms, approximately 30 s capture, crossing the 40-bit
  UWB timestamp wrap interval.
- 300 unique ranges, 10.003 Hz observed mean; maximum host receipt gap 203 ms.
- Median distance 0.515 m, standard deviation 0.01659 m, min/max
  0.473/0.558 m. This is repeatability, **not measured absolute accuracy**.
- Each board TX=309, RX=309, missed slots=0, rejected=0, TX errors=0.
- Each had one RX error; follower sync_losses=1 after coordinator-first cleanup.
  Counters are queried after stopping, so cleanup and pre-start listening are
  included. Hardware RX timeout counts are expected during idle gaps.
- Two nodes supply one distance; they cannot produce a relative 3D position.

## Six-board test

All six boards confirmed 1.0.18. The four-board OTA report is
`build/batch-ota/20261011T105737723353Z/results.json` (4/4 successful).

Six-node startup rotates node 6's four-neighbor assignment every 0.5 s at periods
up to 150 ms. The five masks cover every lower neighbor over time, obtaining all
15 pair distances across a rolling window while keeping four scheduled edges
per node 6 frame. This does not imply simultaneous full-mesh observations.

Two six-board runs are recorded in `six18-100/` and `six18-fastuart/`. The first
revealed CH340/Windows delivering timed 4096-byte reads as full blocks roughly
every 0.6 s. Nonblocking draining with a 5 ms polling yield reduced the median
chunk to 128 bytes. The display no longer waits for a full block. Measurement
rate now counts accepted unique ranges, excluding repeated radio reports.

The second run captured approximately 30 s:

- All 15 distance pairs observed; 4,174 unique ranges.
- Ten fixed pairs averaged 10 Hz each; five rotating pairs averaged 7.57–7.77 Hz.
  The median aggregate unique measurement rate was approximately 136 Hz.
- Each board transmitted 317 slots, with **zero missed slots and zero rejected
  network packets**. Coordinator received 1,581 of 1,585 expected peer broadcasts;
  each follower received 1,585. Post-stop follower sync losses include deliberate
  coordinator-first shutdown. Radio counters also include earlier runs.
- Relative position updates approximately 10 Hz (the rolling one-second counter
  sometimes reads 11 due to timing boundaries). Median solver time approximately
  8.5 ms and median distance-fit RMS approximately 1.9 cm.
- Median newest-measurement age at publication approximately 216 ms. Mean edge
  age approximately 288 ms; oldest retained rotating edge approximately 717 ms
  including solver publication. These are estimates using gateway/host clock
  alignment, not independently measured physical motion-to-screen latency.
- Typical stationary range spread was 1.2–3.5 cm standard deviation. Pair 1–4
  had a much larger 11.8 cm spread. Fit RMS is not position accuracy.
- The inferred six-point geometry does not cleanly identify a unique 20 cm
  out-of-plane board: even the best five-board plane had approximately 6.6 cm
  RMS thickness in the last sample. No board is asserted to be the raised one.
  Range bias, geometry and the stated physical layout require further validation.
- 89 Python tests plus portable C tests pass, including minimum firmware gating,
  partial UART block delivery, duplicate measurement-rate handling and six-node
  neighbor coverage. Log: `tmp/tests-uwb3d-hardware-final.log`.

`tools/uwb3d_live.py` opens the existing Control Center in XY projection, fits the
first available live position set, and optionally captures the actual window.
The live session directory is `build/uwb3d/hardware-six/live18/`. The **Fit** button
also scales the scene for compact tabletop layouts. Live state and logs are real
gateway data; no simulation is substituted if a board or position is unavailable.

## Tabletop follow-up — 12 October

The user confirmed that **all six boards are now on the table**. The resumed 3D
session correctly rejected height observability. An explicit **Tabletop 2D**
solver option now estimates XY from ranges, constrains Z to the reference plane,
and labels Z=0 as assumed rather than measured. Relative 3D remains the default
for general configurations; no automatic flattening hides a failed 3D solve.

Planar startup requires complete pair distances, then the range-only Jacobian
must remain full rank in 2D. Collinear configurations, high residuals and weak
conditional uncertainty are rejected. Existing stale-position behavior remains.
The 3D coplanarity checks are preserved. All **91 Python tests** and portable C
tests pass (`tmp/tests-uwb3d-planar.log`). Firmware remains 1.0.18; this is a host
solver/view change and requires no additional OTA.

The current tabletop session is in `build/uwb3d/hardware-six/live18-planar/`.
Launch with `tools/uwb3d_live.py` using the roster/gateway/port arguments above,
`--period 100 --planar --capture --output build/uwb3d/hardware-six/live18-planar`.
In the GUI, stop before changing between **Relative 3D** and **Tabletop 2D**,
then start again. XY is the top-down view; Fit scales the compact board layout.

The actual planar GUI was inspected and captured as `live-top-down.png`. A live
snapshot contains 7,319 unique ranges, six displayed positions and 15 valid pairs.
Median aggregate measurement rate is 136 Hz, solver 11.5 ms, newest measurement
age at publication 221 ms, and oldest edge age at publication 722 ms. Publication
is approximately 10 Hz; render counter reads 10 Hz. Median fit RMS is **7.63 cm**,
with visible stationary position jitter. This is a working live network/view,
not acceptance of the requested sub-3-cm positioning accuracy. Investigate
pair-specific bias/NLOS and measure ground truth before calibration. The GUI
remains open on the planar hardware session; Stop performs fleet cleanup.

## Reproduction

```powershell
python tools/uwb3d_bench.py --roster build/uwb3d/hardware-six/roster.json --gateway PCB_09EC3021 --port COM10 --period 100 --seconds 45 --output build/uwb3d/hardware-six/six18-100
python tools/uwb3d_measurements.py build/uwb3d/hardware-six/six18-100/session.jsonl --output build/uwb3d/hardware-six/six18-100/measurements.json
```

Only run one hardware controller at a time. Bench cleanup stops the coordinator
first, then followers. A 100 ms superframe does not mean zero latency: confirmed
BP exchanges span frames, and UART/solver/display add delay. No 3 cm accuracy,
30-board RF capacity, rubble/NLOS performance or inertial altitude is qualified.
