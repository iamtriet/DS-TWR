# ST-Link initial programming and fast blink test

The desktop **New Board / ST-Link** action was tested with the probe selected explicitly at USB location **1-5**, corresponding to the user's Windows `Port_#0005.Hub_#0002` connection.

- Target: nRF52833, 512 KiB flash, 128 KiB RAM; FICR `7B158204E91F5334`, BLE identity `PCB_E91F5334`.
- Full 512 KiB flash and 4 KiB UICR backups were captured before programming.
- Trusted MCUboot plus signed application 1.0.6 were programmed and verified. Existing storage and UICR matched their pre-programming bytes afterward. No mass erase, debug protection change or OTP write was used.
- BLE inspection after SWD programming found the exact 1.0.6 image active, bootable and reported confirmed by image management.
- The GUI then updated both powered boards (`PCB_76A10829`, `PCB_E91F5334`) to signed 1.0.7, LED3 100 ms on / 100 ms off. Both were UPDATED on their first attempt. Complete OTA time was **205.47 seconds**, including discovery, trial, confirmation and reboot.

Evidence:

- `build/provisioning/20261007T041732095291Z/`: backup, FICR, exact HEX, readbacks, OpenOCD logs and successful result.
- `tmp/new-board-gui-program.log`, `tmp/new-board-gui-test.log`: actual desktop programming action.
- `tmp/new-board-ble-state.log`: booted application state.
- `build/batch-ota/20261007T041913572164Z/results.json`: two-board OTA result.
- `tmp/fast-powered-ota.log`: desktop OTA progress.

The faster blink was verified by source/build identity and active confirmed firmware; LED visibility is not inferred from GPIO return codes. Further powered boards were subsequently included in the five-board tracking preparation.
