# Two-board OTA test — 2026-10-06

**Result: 2/2 UPDATED on the first attempt; zero failures.** Both independently queried after the batch report firmware 1.0.2, confirmed=true, and active image hash 818b8195f7c9fbcc2c17ee82e3c51b49da0c2651f09713a0855d9c59254d8d45.

| Board | BLE address | Before | After |
|---|---|---|---|
| PCB_76A10829 | D1:3E:A9:6B:F8:8A | 1.0.1 confirmed | 1.0.2 confirmed |
| PCB_866D6F7F | F8:4D:02:6E:3E:56 | 1.0.0 after initial SWD provisioning | 1.0.2 confirmed |

The new board was identified through ST-Link V2 as FICR 30A59A47866D6F7F, target voltage approximately 3.28 V. Its complete 512 KiB flash and 4 KiB UICR were backed up before programming to build/two-board-test/866D6F7F-before-flash.bin and 866D6F7F-uicr.bin. OpenOCD programmed and verified the existing trusted MCUboot + signed A combined image successfully. No mass erase, OTP writing or protection change was requested.

Both boards were enrolled by BLE device name/address and GATT/SMP checks. Signed LED-blink B was advanced to 1.0.2 so both boards received a real OTA transfer. The 1.0.1 release was preserved under build/releases/1.0.1. Build passed (188076 bytes reported flash, 47883 static RAM); signed 1.0.2 is 192880 bytes and passed RSA verification.

The batch uploaded sequentially, requested each trial, observed the expected active image, performed a second health observation, explicitly confirmed, reset again and verified the confirmed image. The fresh post-batch check also verified both expected hashes and confirmation flags. This verifies this two-board OTA workflow; it does not establish a 30-board run, RF/IMU functionality, power-loss rollback or visual LED output on the new board.

LED3 P0.06 is configured for 500 ms on / 500 ms off. The original board's visual LED operation was confirmed by the user in the previous test. This session's verification is SWD/BLE/software evidence.

## Repeat with one click

Power both boards and double-click Update_Two_Boards.cmd. It uses the enrolled boards_ota_two.json and current signed validation_b image. Already-current confirmed images are skipped; a newly built/signed release is uploaded. The separate 30-board roster is unaffected.

## Evidence

- build/batch-ota/20261006T080437763313Z/results.json
- build/two-board-test/results.json
- build/two-board-test/final-states.json
- build/two-board-test/two-board-batch.log
- build/two-board-test/two-board-first-flash.log
- build/two-board-test/two-board-enroll.log
- build/two-board-test/build-two-board-ota.log
- build/two-board-test/sign-two-board-ota.log
