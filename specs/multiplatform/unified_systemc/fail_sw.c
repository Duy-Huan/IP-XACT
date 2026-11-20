/* SPDX-License-Identifier: Apache-2.0 */
/* Negative RISC-V64 guest software proving real non-zero failure reporting via Finisher. */
#include <stdint.h>
#include "unified_soc_map.h"

#define R32(addr) (*(volatile uint32_t *)(uintptr_t)(addr))
#define R8(addr)  (*(volatile uint8_t  *)(uintptr_t)(addr))

uint64_t trap_dispatch(uint64_t mcause, uint64_t mepc, uint64_t mtval) {
    (void)mcause;
    (void)mtval;
    return mepc + 4ULL;
}

int main(void) {
    const char *msg = "UNIFIED_SOC_EXPECTED_FAIL\n";
    while (*msg) {
        R8(USOC_QEMU_UART_0_BASE) = (uint8_t)*msg++;
    }
    R32(USOC_SIM_FINISHER_BASE) = 0x13333U;
    for (;;) {
        __asm__ volatile("wfi");
    }
    return 1;
}
