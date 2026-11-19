# SPDX-License-Identifier: Apache-2.0
zephyr_sources(reset.S)
zephyr_include_directories(.)
zephyr_linker_sources(ROM_START SORT_KEY 0x0reset reset.ld)
zephyr_link_libraries(-Wl,-e,usp_reset)
set(SOC_LINKER_SCRIPT ${ZEPHYR_BASE}/include/zephyr/arch/riscv/common/linker.ld CACHE INTERNAL "")
