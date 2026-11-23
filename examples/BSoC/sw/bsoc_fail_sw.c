/* SPDX-License-Identifier: Apache-2.0 */
/* Negative guest verification binary for BSoC: proves non-zero finisher fault exits non-zero. */
#include <stdint.h>
#include "soc_map.h"

#define R32(addr) (*(volatile uint32_t *)(uintptr_t)(addr))
#define R8(addr)  (*(volatile uint8_t  *)(uintptr_t)(addr))

uint64_t trap_dispatch(uint64_t mcause, uint64_t mepc, uint64_t mtval) {
    (void)mcause; (void)mtval;
    return mepc;
}

int main(void) {
    const char *msg = "BSOC_NEGATIVE_FAILURE_TRIGGERED\n";
    while (*msg) {
        while (!(R8(SOC_PRIMARY_UART_BASE + 5U) & 0x20U)) {}
        R8(SOC_PRIMARY_UART_BASE) = (uint8_t)*msg++;
    }
    R32(SOC_FINISHER_BASE) = 0x13333U;
    for (;;) {
        __asm__ volatile("wfi");
    }
}
