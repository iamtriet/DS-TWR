# U3D v1 firmware/host contract

All multibyte integers and floats are little endian. This is a custom experimental protocol, not FiRa. The C encoder and Python decoder share a golden-frame regression test in `tools/run_tests.py`.

## UART framing

COBS(header + payload + CRC32), followed by zero. Firmware also prefixes zero so an intervening ASCII diagnostic cannot consume the next binary frame. Empty delimiters are ignored. CRC32/ISO-HDLC uses reflected polynomial0xEDB88320, init0xFFFFFFFF, final XOR0xFFFFFFFF (same as `zlib.crc32`). CRC covers header+payload. Frames failing bounds/version/CRC are dropped and counted.

Header `<BBHIQ>` (16 bytes): version1 u8, type u8, payload length u16, transport sequence u32, gateway monotonic microseconds u64. CRC is4 bytes. Host payload bound4096; current firmware encoder bound128. Acquisition chunks are bounded128×4096 in UART use, parser partial buffer4160 bytes, discard-until-delimiter on oversize. Simulator submits one bounded 30-node batch per tick. Sequence arithmetic is modulo2^32; duplicate and old frames are rejected, forward gaps counted. Discovery sequence0 with a sufficiently earlier gateway uptime signals MCU reboot and resets measurement sequencing. UART reconnect resets parser/clock/matrix state.

| Type | Payload | Current firmware use |
|---|---|---|
| 1 DISCOVERY | `<HQHHHI>` node u16, FICR UID64, major/minor/patch u16 each, capability bitmap u32 | Gateway forwards actual UIDs and repeats every10 superframes; bit0 means UART range export |
| 2 STATUS | `<HHB>` node, battery in0.01% or65535 unknown, status flags | Host supported; not currently emitted by network firmware |
| 3 RANGE | `<HHiIQHB>` source,destination u16; distance mm signed32; measurement sequence u32; effective gateway us u64; quality0…1000 or65535; flags u8 | Actual MCU DS-TWR results |
| 4 TIMESTAMPS | Diagnostic Broadcast schema below | Host diagnostic/test support; MCU network emits completed RANGE instead |
| 5 IMU | `<HBfff>` node, quality, acceleration norm m/s², gyro norm rad/s, sample age seconds | Optional host motion gate only; not remote vector fusion |
| 6 NETWORK | `<IBBI>` superframe u32, synchronized u8, mode u8, period us u32 | Mode2 = experimental sparse BP |
| 7 HEARTBEAT | `<H>` node | Host support |
| 8 CONFIG_REQUEST | Reserved | Host uses existing ASCII command channel instead |
| 9 CONFIG_RESPONSE | `<HI>` node, neighbor bitmap | Coordinator acceptance of `net assign`; not a follower-delivery ACK |
| 10 ERROR | Reserved | Current firmware errors are ASCII diagnostics |

RANGE flags: bit0 valid, bit1 NLOS diagnostic present, bit2 NLOS/suspect. Unknown bits reject. Quality unknown is distinct from0. MCU network currently emits quality65535, valid1, no NLOS diagnostics. Valid distance bounds are0<mm<=1,000,000; missing/failed ranges never become distance0. Node IDs0 and65535 are reserved. Registry checks UID uniqueness and capacity. Measurement sequence is final superframe; repeated reports are deduplicated per edge.

Gateway uptime/effective time is aligned approximately to host monotonic time using minimum observed receipt offset. This is transport-age alignment, not precision RF synchronization. UART queueing uncertainty remains. ToF never uses a Windows timestamp. Logs preserve raw gateway timestamps and receipt elapsed time. Runtime records worst measurement-to-publication age and publication time separately; render rate is independent.

## On-air sparse BP frame (not UART type4)

Radio payload max512 bytes; hardware FCS is generated/checked by DW3110. Network mode: CH5,6.8Mbps,preamble128,extendedPHR,STSoff. No MAC authentication/encryption is provided by this laboratory protocol.

| Offset | Size | Field |
|---|---:|---|
| 0 | 1 | Magic0xA7 |
| 1 | 1 | Version1 |
| 2 | 1 | Source ID1…30 |
| 3 | 1 | Configured node count2…30 |
| 4 | 4 | Network ID |
| 8 | 4 | Superframe |
| 12 | 5 | Predicted current antenna TX timestamp |
| 17 | 8 | FICR UID |
| 25 | 4 | RX validity bitmap, bit(node−1) |
| 29 | 1 | Completed result count0…4 |
| 30 | 2 | Slot milliseconds |
| 32 | 2 | Superframe milliseconds |
| 34 | 1 | Bit0 prior TX confirmed; bit1 coordinator assignment table present |
| 35 | 4 | Confirmed previous TX superframe |
| 39 | 5 | Actual previous antenna TX timestamp |
| 44 | 2 | Firmware patch version (major1/minor0) |
| 46 | variable | RX records,9 bytes each, in bitmap ID order: frame u32 + local RX40 |
| after RX | variable | Completed results,9 bytes each: lower-peer ID u8 + final frame u32 + mm u32 |
| after results | N×4 when bit1 | Coordinator's per-node lower-neighbor bitmaps |

Maximum coordinator payload46+29×9+30×4=427 bytes. Maximum follower46+29×9+4×9=343 bytes. Bounds, count, exact length, network ID, firmware protocol version, identity consistency, flags, self bits and schedule are validated before accepting timestamps. Packet loss leaves explicit missing timestamps.

For i<j, i(k) is Poll, j(k) Response, i(k+1) Final. Node j saves the six local-clock-owned timestamps, then waits for i(k+2) to confirm the actual Final TX matches its predicted TX. It reports the result in j(k+2). One broadcast participates in multiple exchanges. Only up to four lower-ID neighbors per node produce results; first four form a clique, yielding110 selected edges at30 nodes. This mode does not export all435 radio edges.

Raw delayed TX is quantized to512 DW time units; prediction adds the calibrated TX antenna delay. `radio_tx_delayed` reads back actual TX timestamp and rejects a mismatch. Missing/late slots are skipped, never replaced with immediate TX. The next broadcast carries confirmation. RX timestamps already refer to the configured receive antenna delay. Device units are1/(499.2e6×128) seconds, timestamp width40 bits (~17.2 s rollover).

The DS relation uses local modular intervals Ra=rr−pt, Da=ft−rr on i and Rb=fr−rt, Db=rt−pr on j. ToF=(RaRb−DaDb)/(Ra+Rb+Da+Db). C uses double intermediates; host uses integer products before division. All intervals must be positive and below0.5 s; distances must be physically bounded. Offset/drift/rollover and predicted/actual timestamp consistency have regression tests.

Node1 supplies the epoch; followers estimate relative clock rate from successive coordinator RX timestamps. A missing coordinator beacon prevents another follower transmission; prolonged loss clears synchronization. Rejoining a restarted coordinator clears old exchanges before accepting its reset sequence. Coordinator remains a mobile radio/time source, not a fixed spatial anchor. Propagation time in beacon epoch alignment is not eliminated; guards require physical qualification.

## Timing and capacity evidence

`timing_budget()` computes an optimistic airtime lower bound including preamble/SFD/PHR/payload but excluding FEC and implementation overhead. For30 nodes/427 bytes it is about668 microseconds. The proposed30×3ms+10ms=100ms fits arithmetic, but is rejected by current firmware. Legal firmware slots are10…50ms, period must satisfy N×slot+20<=period<=500ms. Default30×10ms leaves200ms for guard and transport drain in500ms.

UART range frames are about46 bytes;110 reports/500ms →10,120 bytes/s before status/discovery versus11,520 bytes/s at1152008N1. The firmware queue holds64 records; host and firmware drops are observable, not silently treated as success. At fewer nodes a shorter legal period can be tested, but it is not automatically qualified. At30 nodes/100ms even sparse per-range UART reporting would exceed this serial rate.

The optional UART TIMESTAMPS diagnostic `Broadcast` format is13-byte header (u8version,u16node,u32frame,TX40,u8recordcount), followed by records(u16peer,u32frame,RX40). Record presence expresses validity. It is deliberately separate from the actual A7 on-air frame. Host ledger results lack a common effective gateway time, so they are counted as diagnostic unfused ranges; they never silently enter localization.

## Sources of truth

`src/uwb_network.c`, `src/radio.c`, `src/uwb3d_wire.c`, `tools/uwb3d_protocol.py`, vendor DW3000 driver headers and the local hardware schematic/overlay. External reference: [Qorvo DW3xxx API guide](https://forum.qorvo.com/uploads/short-url/xD3TlXKvkujjdUaJWXv2b4E7GQN.pdf). Solver loss semantics follow [SciPy least_squares](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.least_squares.html).
