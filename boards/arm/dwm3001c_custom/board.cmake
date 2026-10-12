# SPDX-License-Identifier: Apache-2.0
# Generic OpenOCD configs supplied by the tool package. No stale Zephyr module path.
board_runner_args(openocd "--config=interface/stlink.cfg" "--config=target/nrf52.cfg")
include(${ZEPHYR_BASE}/boards/common/openocd.board.cmake)
