# Reference source and local modifications

Source: https://github.com/albifache/DWM3001CDK

Pinned commit: `c40ff067ad350e768baab6f11deeab26a01cc145`.
The copied `app/` and `mac/` files retain their original notices and are
licensed under GNU AGPL version 3; see [LICENSE](LICENSE). The reference port
is derived from this code. Preserve these notices and satisfy applicable AGPL
source obligations when distributing or providing access to the derived work.
Other vendor components retain their respective license terms.

Local changes: current DW3000 API includes and non-null output arguments,
22-byte header bounds, frame/payload length and anchor-count checks,
authenticated INIT sequence freshness, bounded scheduler abort hooks,
floating-point DS-TWR products to avoid integer multiplication overflow,
invalid-range rejection, and a role accessor. The PCB adapter, PHY selection,
host provisioning, and telemetry live outside this directory.

See `docs/REFERENCE_TDMA.md` for the implemented scope and limitations.
