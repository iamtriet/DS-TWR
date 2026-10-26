# Albifache reference mode in Control Center

This is a separate firmware protocol adapted from
[albifache/DWM3001CDK](https://github.com/albifache/DWM3001CDK), commit
`c40ff067ad350e768baab6f11deeab26a01cc145`. It is not a comparison result.
The original application/MAC sources and AGPL-3.0 license are preserved in
`vendor/albifache`; see its README for modifications and attribution.

## Implemented

- Coordinator-controlled INIT → REQUEST → RESPONSE → REPORT → FINAL exchange.
- DS-TWR timestamps and 6 ms TDMA slots, with upstream STS mode 1 / length 1024,
  channel 5, 850 kbit/s and AES-GCM/MIC16 frame processing.
- Runtime tag/anchor/listener selection through authenticated UWB INIT frames.
  UI node ID 1 maps to upstream coordinator MAC 0. All boards can take any role.
- 2–9 boards, at most eight anchors per round. Tag ID 0 rotates the tag role;
  a fixed tag ID keeps the remaining selected nodes as anchors. This schedules
  multiple tags sequentially, not simultaneously within a round.
- Fresh shared AES and STS keys for every host start, redacted command logging,
  UID preflight, coordinator started last and stopped first.
- Existing top-down tracking view, distance matrix, recording and export use
  the reference measurements. Anchor role holds the existing status LED on;
  tag/listener roles use the existing blink behavior.
- Bounded scheduler and AES waits, malformed-frame checks and OTA stop guards.

## Build and use

1. Build with `python -m platformio run -e reference` (use the project's
   PlatformIO Python environment). Sign with
   `python tools/sign_image.py sign --env reference --output-dir build/releases/reference-1.1.0`.
2. In **Boards & OTA**, install `reference.signed.bin` version **1.1.0** on every
   participant. Normal 1.0.18 BP firmware cannot participate in reference rounds.
3. Select 2–9 boards. Open the existing UWB tracking tab and **Reference setup**.
   Default `200,0,0` means a 200 ms round, rotating tag, all other nodes anchors.
   The anchor mask uses bit 0 for node 1, bit 1 for node 2, etc.; 0 means all
   nodes other than the current tag. Unselected nodes listen.
4. Enter the UART gateway COM port, then **Start**, and identify the board wired
   to that UART. It becomes node 1; the remaining board names are sorted.
   ST-Link is not a UART port. The host provisions the selected boards over BLE.
5. Use **Stop** before OTA or changing settings. To return to BP, reinstall
   the saved 1.0.18 image on every board. Bootloader/key/partition are unchanged.

For six boards at the default period, a complete role rotation takes about
1.2 seconds. This deliberately retains the reference schedule; it does not
promise the update rate of the existing sparse scheduler. Use rotating roles
to obtain anchor-to-anchor ranges for relative localization. A fixed-tag star
alone does not determine anchor geometry; the view may correctly report
insufficient constraints. Tabletop mode assumes Z=0; ranges give relative
geometry, not an absolute world position.

## Qualification and remaining differences

The port is software-tested and built separately; these checks do not establish
RF operation, accuracy, replay resistance, or interoperability on physical boards.
No comparison or automatic deployment is part of this implementation step.
Hardware AES is mocked in parser tests; crypto qualification requires hardware.
BLE key provisioning is a bench mechanism, not authenticated key management.
The upstream INIT STS IV behavior is retained; do not treat the mode as a
security-qualified product. Never reuse recorded session keys for another run.

The upstream deep-sleep/wake helpers target a different driver/board. Deep sleep
is not enabled in this port; receivers remain available between rounds. BNO085
fusion remains in the existing tracking stack; this reference firmware does not
add synchronized IMU fusion. There is no claim of better-than-3-cm accuracy.

## Software verification (2026-10-12)

### Adaptive range filtering

The live launcher enables **Range filter** before localization. The UI checkbox
is applied on Start; stop the session before changing it. Config JSON stores
`range_filtering`. Each unordered link has its own causal filter: a seven-sample
median/MAD guard (minimum 10 cm, four robust standard deviations) rejects an
isolated spike; a consistent second excursion allows real motion. Rejected
samples never refresh the measurement timestamp. A stale gap resets the filter.
Accepted ranges use a One Euro style speed-adaptive low-pass (minimum cutoff
0.18 Hz, beta 2, derivative cutoff 1 Hz), followed by the existing Huber geometry
solver and optional CV position filter. Source principle:
https://gery.casiez.net/publications/CHI2012-casiez.pdf.

Before solving, a triangle consistency guard excludes at most two long links
if each exceeds two independent two-hop paths by more than 15 cm. It never
replaces the distance or overwrites raw data; the remaining graph must pass
the existing solver's rigidity checks. `inconsistent_links` lists exclusions.
This is a heuristic for slowly moving/tabletop nodes: asynchronous fast motion
or multiple biased links can invalidate its assumptions. It is not an NLOS
classifier or a guarantee that the excluded link was the sole bad measurement.

Raw UART recordings and raw link history are retained; filtered edge records
also expose `raw_distance`. The dashboard shows filter state and rejected spike
count. Smoothing adds response delay, especially for a first abrupt movement;
it cannot remove sustained multipath bias or replace antenna calibration.
No static position lock or fabricated IMU measurements are used.

`python tools/assess_range_filter.py PATH/session.jsonl` replays unique observed
samples and reports within-link variation before/after filtering. Variation
can include actual movement and drift, so it is not an absolute accuracy metric.
The captured reference session showed about 20% median reduction across links.
97 Python tests pass, including redundant triangle checks, seeded noise reduction, a sustained step,
a moving ramp, duplicate rejection, spike age preservation and gap recovery.

- 94 Python tests passed, plus portable C tests including the actual adapted
  upstream parser, role assignment, replay rejection, length guards and abort.
- Both `reference` and normal `validation_b` firmware built successfully.
- Final reference build: 193,504 flash bytes reported by PlatformIO, 55,730 RAM
  bytes. MCUboot signing, signature verification and actual slot/trailer bounds
  passed; signed image size is 198,132 bytes.
- Control Center reference setup was exercised in Tk without starting hardware.
- Signed image SHA-256:
  `dc4907e7e4a5f0cd4ff4865a785736ad984d8eed159fe3cf5c054d3cfcd2e677`.
