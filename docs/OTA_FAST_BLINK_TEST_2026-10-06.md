# Faster LED3 OTA — 2026-10-06

Result: both PCB_76A10829 and PCB_866D6F7F were UPDATED on their first attempt to signed version 1.0.3. The batch verified the intended active image, explicitly confirmed it, rebooted and verified confirmation for each board. Zero failures.

LED3 P0.06 now toggles every 100 ms (100 ms on / 100 ms off), nominal 5 Hz versus the previous 1 Hz. Main-loop diagnostics can delay toggles. Visual observation was not available to the agent.

Image hash: e74b1d943fa693b07b724b173594fec3c8ceab8899a55b354dd2c5d5fdbc61b7.
Signed image: 192880 bytes. Firmware build: PASS, 188072 bytes reported flash and 47883 static RAM. RSA verification: PASS.

Changes: src/main.c interval, src/board.c status description, platformio.ini version 1.0.3, tools/sign_image.py expected version. Operational runbook/batch documentation updated. Previous 1.0.2 image preserved in build/releases/1.0.2.

Evidence: build/batch-ota/20261006T081650436433Z/results.json and build/fast-blink-test/. Update_Two_Boards.cmd selects this current signed B image for the enrolled pair.
