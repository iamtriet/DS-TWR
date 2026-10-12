# UWB 3D Tracking — implementation and runbook

Status: 2026-10-11. Integrated into the existing Tk Control Center. Current hardware work uses **1.0.18**; gateway setup refuses older versions because physical testing found DW clock/RX defects. See [dated physical test report](UWB3D_HARDWARE_2026-10-11.md) for current deployment and measured performance. Sections G–I below preserve the original 1.0.14 software-only baseline, not the current deployment status. The original signed 1.0.13 image in `build/images` is retained. Neither baseline nor current work qualifies 30-board RF operation.

## A. What is implemented

For boards confirmed to share a flat surface, select **Tabletop 2D** before
starting and choose **XY** for top-down display. This explicitly assumes Z=0;
it does not measure altitude. Use **Relative 3D** when nodes span different
heights. Stop before switching the model. The new planar mode keeps the existing
3D geometry checks intact; it is not an automatic fallback for uncertain height.

- Registry for 30 mobile nodes (host capacity configurable to 128), actual UID discovery, duplicate rejection, timeout/offline state and reconnection.
- Versioned COBS/CRC binary UART transport, bounded parser/queues/history, sequence loss/reboot handling, calibrated hardware-timestamp DS-TWR helpers.
- Symmetric distance matrix with explicit missing/stale cells, effective timestamps, quality when actually supplied, heatmap and selected-link history.
- Relative 3D MDS or sparse four-neighbor lateration initialization; Huber nonlinear least squares, analytic Jacobian, motion/measurement-age weighting, geometry/rank/uncertainty validation, reflection-aware alignment and optional constant-velocity filter.
- Incremental 3D canvas, XY top-down / XZ / YZ projections, orbit/pan/zoom, selection/follow, trails, uncertainty display, node dashboard and separate measurement/estimation/render metrics.
- 13 range-driven simulation scenarios, JSONL recording, replay with speed/pause/seek, position CSV and raw-range CSV.
- Actual experimental MCU radio mode: extended PHR, delayed TX, coordinator synchronization, deterministic slots, shared timestamp broadcasts, next-frame actual-TX confirmation, sparse DS-TWR results and UART aggregation. Dynamic neighbor masks are sent over UART and redistributed by coordinator broadcasts.
- Existing OTA, ST-Link provisioning, BLE/UART controls, IMU diagnostics and legacy tracking pages remain. Starting the new gateway configures UWB only; it does not invoke OTA, LED, UV, PWM or actuator commands.

## B. Source inventory

New host modules:

| File | Responsibility |
|---|---|
| `tools/uwb3d_protocol.py` | Binary contract, COBS/CRC/sequence checks, diagnostic timestamp ledger, timing budget |
| `tools/uwb3d_core.py` | Config, registry, matrix, relative solver, observability, CV filter, neighbor selection |
| `tools/uwb3d_runtime.py` | Acquisition, solver worker, simulator, replay, logging, CSV export |
| `tools/uwb3d_gateway.py` | One-time BLE fleet setup, coordinator-last start, retryable cleanup |
| `tools/uwb3d_view.py` | Integrated 3D/projection/matrix/dashboard controls |
| `tools/uwb3d_benchmark.py` | Reproducible offline performance measurement |
| `tools/uwb3d_gui_check.py` | Actual Tk app integration checks and screenshot |

Firmware additions: `include/uwb3d_wire.h`, `src/uwb3d_wire.c`, `src/uwb3d_firmware.c`, `include/uwb_network.h`, `src/uwb_network.c`. Modified: `include/radio.h`, `include/validation.h`, `src/radio.c`, `src/console.c`, `src/uwb_app.c`, `src/main.c`, `src/ota.c`, `zephyr/prj.conf`, `platformio.ini`. The console shares Zephyr's existing float formatter instead of linking a second newlib float formatter; partitions and signing key are unchanged.

Integration/dependencies: `tools/control_app.py`, `tools/requirements.txt`, `tools/sign_image.py`. Tests: `tests/test_uwb3d.py`, `tests/test_uwb3d_gateway.py`, `tests/test_uwb3d_wire.c`, `tests/test_uwb_network.c`, `tests/network_mocks/validation.h`, updated listener mocks and `tools/run_tests.py`. Documentation: this file, `UWB3D_PROTOCOL.md`, `UWB3D_INSPECTION.md`, README and Control Center guide.

## C. Data flow and coordinates

DW3110 hardware RX/TX timestamps → MCU sparse pipelined DS-TWR → mobile coordinator 1 → UART binary frames → bounded acquisition queue → parser/registry/matrix → single solver executor → latest-state snapshot → Tk view. Logging has a separate bounded writer. The app reuses its existing asyncio loop; RF scheduling never depends on Windows timing.

The coordinator supplies time slots, **not an absolute spatial anchor**. Inter-node distances cannot recover common translation, common rotation or handedness. Coordinates and velocities are relative formation quantities. Consecutive solutions use O(3) alignment, which explicitly permits reflection. Group translation/rotation simulations therefore do not become absolute motion in the view.

Four noncoplanar nodes are the mathematical minimum. Two nodes show ranges only. Six boards on one flat table do not provide trustworthy 3D height. The solver rejects thin geometry, deficient range-Jacobian rank, excessive residual/conditional uncertainty, and configurations whose thickness is not distinguishable from that uncertainty. These are conservative numerical checks, not a proof of real-world accuracy. Use the retained legacy 2D view for a flat-table experiment.

States are `measured-supported`, `estimated` (solver did not converge), `predicted`, `stale`, or `unavailable`. A held stale position is explicitly colored/labeled; it is not a fresh observation. Conditional uncertainty excludes unobservable global pose and assumes the configured range-noise model.

## D. Build, test and launch

Run PowerShell from the repository root with Python 3.12 including Tk:

```powershell
python -m pip install -r tools/requirements.txt
python -m pip install ziglang==0.16.0
python tools/run_tests.py
& "$env:USERPROFILE/.platformio/penv/Scripts/python.exe" -m platformio run -e validation_b
python tools/sign_image.py sign --env validation_b --output-dir build/releases/1.0.14-candidate
python tools/control_app.py
```

Or double-click `Control_Center.cmd` (also scans BLE). Do not regenerate the existing signing key. Plain `pio upload` sends an unsigned application and is unsuitable for the installed MCUboot configuration. The candidate goes into its separate release directory and does not replace the existing default fleet image.

```powershell
python tools/uwb3d_benchmark.py --seconds 120 --output build/uwb3d/benchmark-final.json
python tools/uwb3d_benchmark.py --seconds 30 --sparse --output build/uwb3d/benchmark-sparse.json
python tools/uwb3d_gui_check.py
```

GUI checking needs an interactive Windows desktop and Pillow (`python -m pip install Pillow`). Normal app operation does not need Pillow.

## E. Physical gateway setup

1. First use **Boards & OTA** to explicitly install `build/releases/1.0.14-candidate/validation_b.signed.bin` on the selected experiment boards. Test two boards first. Existing signed upload verifies trial boot/hash/confirmation. New boards use **New Board / ST-Link**, with the actual probe USB location selected. SWD is independent of UART.
2. Connect a **3.3 V TTL UART** adapter: PCB TX **P0.20 → adapter RX**, PCB RX **P0.21 ← adapter TX**, common ground. Supply board power according to its schematic. UART is **115200, 8N1**. ST-Link V2 is not a COM-port gateway. Never assume a previous COM number is still assigned.
3. Scan/select 2–30 boards in the existing board list. Close individual control sessions and stop legacy fleets. In **UWB 3D Tracking → 3D network**, choose **gateway**, enter the actual UART COM port, load `config/uwb3d-gateway-experimental.json`, then Start. Enter the exact selected board name connected to UART when the app asks for the mobile coordinator. It becomes node 1; other selected names are sorted into IDs 2…N. These network IDs are decimal in commands and hexadecimal in the table.
4. The app checks firmware capability and unique hardware UID, initializes followers first and coordinator last, then releases BLE connections. The coordinator remains mobile. The board count and assignments stay fixed until Stop/reconfigure; discovery/rejoin within that roster is automatic.
5. Start with well-separated, stationary, **noncoplanar** nodes and visible line of sight. Inspect the matrix before trusting positions. The first four IDs form the initial sparse tetrahedron; later IDs initially range to four predecessors. If that tetrahedron is flat or links are missing, 3D may remain unavailable. Reorder selected physical nodes or use a better geometry before restarting.
6. Stop closes acquisition and stops the coordinator before followers. Unreachable stops are reported and retained for retry. Other Control Center hardware operations stop the gateway first. OTA also stops radio activity in firmware.

The default experimental schedule is **10 ms slots / 500 ms superframe**, at most **2 fresh reports per selected edge per second** after pipeline warm-up. An extra broadcast validates the actual Final TX timestamp, so ranges are roughly 0.75–1.04 seconds old when reported. The gateway configuration explicitly accepts up to 1.5-second-old ranges; this trades motion accuracy for this conservative unqualified timing. Displaying at 10 Hz does not remove this latency.

A UART-only setup can be configured manually on each board, substituting its ID and common count:

```text
uwb stop
uwb capabilities
uwb init
net configure 2 6 10 500 43594231
net start experimental
net status
net stop
```

Start coordinator ID 1 last. `net assign NODE MASK_HEX` on coordinator changes a node's lower-ID neighbors; nodes above 4 require exactly four bits. The host selects candidate masks using available geometry, reported quality/NLOS and age every five seconds, only after a supported solution. The estimator independently validates the resulting graph. There is no claim that edge count alone establishes uniqueness.

PHY is currently fixed to CH5 / 6.8 Mbps / preamble128 / extended PHR / STS off in network mode. Stop restores the legacy PHY. Slot, period, count, network ID and neighbor masks are configurable. A 3 ms / 100 ms configuration is explicitly rejected by this firmware.

## F. Offline simulation and replay

Choose **simulation**, 30 nodes, a scenario, full mesh or sparse simulation, then Start. Select **XY** for top-down perspective. Drag to orbit in 3D, Shift-drag to pan, scroll to zoom. Select a table row and Follow to track a relative node. Pause view leaves acquisition running. Reset frame resets estimation/filter history.

All simulated positions first become noisy pairwise RANGE packets. The same decoder and solver process them; truth is used only for reflection-aware aligned RMSE evaluation. Scenarios include static/random/rapid motion, group translation/rotation, expansion, separating groups, loss, missing links, NLOS bias, disconnected graphs, join/leave and coplanarity. Clock drift/40-bit rollover are tested separately in protocol/C tests.

Record produces JSONL metadata, raw framed bytes, states and metrics; End log drains its writer. Export CSV creates the selected position CSV plus `*-ranges.csv`, preserving effective gateway timestamps, quality and validity. Replay uses those raw packets with speed, pause and initial seek; it never controls boards. After seeking, discovery may take until the next recorded discovery batch. Replay/simulation/live source labels remain visible.

Configuration is a JSON representation of `Config`; save/load with **Config** while stopped. `config/uwb3d-simulation.json` keeps a 0.5 s measurement-age limit. The separate gateway configuration makes its longer latency explicit. `IMU gate` controls optional scalar-motion gating; it is not vector inertial propagation.

## G. Executed verification

- `python tools/run_tests.py`: **85 Python tests pass**, plus portable C ranging/frame/scheduler/anchor-listener tests, C/Python binary golden-frame compatibility, and actual `uwb_network.c` tests with mocked radio timestamps. Network tests cover confirmed Final TX, timestamp rollover, five consecutive coordinator slots, sync-loss silence, coordinator restart and OTA stop/PHY restoration.
- PlatformIO firmware builds:192,796 bytes reported flash,59,221 bytes RAM. Imgtool verifies the signed1.0.14 candidate:197,448 bytes, file SHA-256 `53b63c3c160514c1fa6f9652765d960619a1ce04b886b533a982f5acabcb3238`, within unchanged slot/trailer bounds. Final build log is `tmp/build-uwb3d-release.log`.
- Actual Control Center GUI test: 30 nodes, selection/camera/projections, 30×30 matrix/900 heatmap cells, link history, pause-with-acquisition, clean shutdown; no callback errors. The latest one-second active render sample was **9 frames/s**, target 10. It is a short GUI sample, not a long render stress benchmark.
- **120 s full-mesh software simulation**: 4,350 ranges/s; median 10 localization updates/s; median solver **17.94 ms**, maximum **59.98 ms**; median aligned 3D error **12.81 mm** under synthetic 20 mm Gaussian range noise. CPU **37.67% of one core**. RSS 84.1→95.3 MB, peak97.3 MB; 95.37 MB at30 s and95.52 MB at90 s. Queue/log drops0. Logging was off, so zero log drops does not benchmark disk throughput.
- **30 s sparse software simulation**: 110 edges, 1,100 ranges/s, median10 localization updates/s, median solver15.26 ms, max56.97 ms, median aligned error44.92 mm. This already exceeds3 cm in simulation; sparse geometry/noise matters. Queue drops0.
- Reports: `build/uwb3d/benchmark-final.json`, `benchmark-sparse.json`, `gui-test.json`, `simulation-30-gui.png`. Test/build logs: `tmp/tests-uwb3d-final.log`, `tmp/build-uwb3d-final2.log` (later final builds may supersede size only).

Failures found and fixed: sparse shortest-path initialization could return a wrong mirror arrangement; replaced by a verified lateration scaffold. Coordinator initially advanced its epoch too late; fixed and regression-tested. Noisy planar ranges could invent Z; added uncertainty/thickness rejection. Original candidate exceeded safe OTA space; removed duplicate float formatter instead of changing partitions. Experimental LTO attempts were discarded; final build uses the pinned normal toolchain. There are no known remaining test failures.

## H. Hardware validation status

| Category | Status |
|---|---|
| Host parser, matrix, 6/10/20/30-node solver, simulation/replay | Implemented and software-tested |
| Actual Tk integration and legacy test suite | Tested; screenshot is simulation |
| MCU delayed TX, extended PHY, BP scheduler, UART aggregator | Implemented, compiled, portable-radio-mock tested; **not physically tested in this upgrade** |
| 1.0.14 OTA candidate | Signed and verified; **not flashed in this upgrade** |
| Existing six-board v1.0.13 operation | Historical evidence only; not proof for this new protocol |
| 30 physical mobile nodes, 3 ms slots, 100 ms superframe | Unqualified; current firmware rejects that fast schedule |
| Full-mesh 435-edge radio mode | Host/simulator supported; MCU candidate exports sparse BP results only |
| BNO085 | Existing driver/diagnostics retained; optional scalar IMU protocol/gating implemented; network firmware does not yet forward remote IMU vectors |
| Battery and NLOS quality | Unknown unless actually supplied; no synthetic sensor quality |
| Absolute world tracking / global heading / common translation | Unobservable from ranges alone |
| Physical TCP/UDP gateway, FiRa/STS interoperability | Not implemented/qualified by this custom protocol |

## I. Limits and next physical acceptance test

First verify 2-node ranges and actual slot counters over multiple DW timestamp wraps; compare with a measured distance. Then use six noncoplanar nodes, log UART raw data, perturb individual nodes and inspect latency, residuals, missing links and recovery. Move/restart the coordinator, disconnect UART and one follower, then test OTA stop/restart. Finally expand to10/20/30 physical nodes and measure actual antenna TX/RX timing, missed slots, FIFO/queue drops and serial utilization with a logic analyzer and packet logs. Do not shorten slots based on the software benchmark.

At30 nodes the coordinator frame is at most427 bytes; a follower at most343 bytes. Airtime arithmetic alone is not qualification: SPI processing, RX turnaround, interrupt/BLE/IMU service and drift must be measured. At500 ms, sparse range UART traffic is about10.1 kB/s before status/discovery, close to115200-baud8N1 capacity11.52 kB/s. A bounded64-record firmware queue absorbs bursts; drops remain possible and must be measured. The older legacy sparse scheduled DS-TWR page remains available as a separately tested fallback.

NLOS rubble, antenna bias, poor geometry, moving nodes during the long exchange and lost packets can dominate error. Huber loss and CV prediction do not eliminate these effects. The current estimator is a timestamp-aware constant-velocity approximation, not a full sliding-window inertial estimator. No physical 3 cm accuracy, zero latency, fresh 10 Hz radio rate or 30-board RF operation is claimed.
