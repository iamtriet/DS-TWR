# Dependency provenance and local adaptations

## CEVA SH-2/SHTP

Source: https://github.com/ceva-dsp/sh2 at `b514b1e2586ddc195e553dac89fc94c637b25298`.
Files in `vendor/sh2` retain their Apache-2.0 headers and `NOTICE.txt`; the complete license is in `LICENSE`.

Local change: `sh2.c` operation processing applies a 1-second fallback to operations whose upstream timeout is zero. This prevents an absent/miswired sensor from leaving a CLI command in an unbounded transaction wait. Individual Zephyr I2C calls retain the installed driver's 500 ms timeout. No sensor firmware or feature identifiers were fabricated.

## Qorvo DW3000 driver

Source: https://github.com/br101/dw3000-decadriver-source at `67dfbb7f2c5b1a4157b8c265b19f08776e91b1cc`, driver version 08.02.02. `dwt_uwb_driver` was copied to `vendor/dw3000`; the port's `platform/deca_compat.c` replaces the compatibility layer and `platform/deca_ull.h` is included.

Qorvo files retain their notices and `LICENSES/LicenseRef-QORVO-2.txt`. Platform adaptations retain the Bruno Randolf ISC notice in `LICENSES/ISC.txt`. Observe the Qorvo license's restriction to use with Qorvo devices.

Local changes in `dw3000/dw3000_device.c`:

- Removed unused ioctl/debug dispatch and its unused helper cascade after confirming the selected port's driver table does not use them. Public application APIs remain in use through the supplied driver table.
- Invalid SPI mode and unbounded SAR/PG calibration waits signal `validation_driver_fault()` instead of hanging. SAR/PG waits are bounded to 1000 polls with 10 us delays.
- No OTP writing was added. Antenna, power and PG-count calibration are read from module production OTP according to the supplied DWM3001C datasheet Rev F table 10.

`src/radio_port.c` is the new Zephyr adapter. The main thread owns all driver calls; GPIO IRQ only counts edges, so driver mutex callbacks do not mask interrupts. Sticky SPI/calibration faults invalidate readiness until reinitialization. Only the API paths used by this application were reviewed; unused vendor features are not certified.

## Framework and bootloader

PlatformIO `nordicnrf52@10.11.0`, package Zephyr `2.20701.220422` (Zephyr 2.7.1), GCC ARM 8.2.1. The existing adjacent project's architecture and custom board definitions were reused. Framework packages are not modified.

MCUboot https://github.com/mcu-tools/mcuboot at v1.9.0, commit `c657cbea75f2bb1faf1fceacf972a0537a8d26dd`. The build script clones this revision under ignored `build/`, checks revision/cleanliness, and builds against the local custom board and an explicit local RSA key. Upstream licenses remain in its checkout.

`src/vendor/*.c` are small include wrappers because this PlatformIO integration discovers compilation units under `src/`; dependency source lives in `vendor/`. Every implementation is compiled exactly once.

Boot build compatibility: `include/mcuboot_zephyr27_compat.h` supplies the matching `void sys_clock_disable(void)` declaration missing from Zephyr 2.7.1 headers. It is force-included for boot C compilation; neither upstream checkout is modified.
