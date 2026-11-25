/* SPDX-License-Identifier: Apache-2.0 */
/* UspNextGen Negative Verification Firmware:
 * Deliberately accesses an unmapped register (0xFC) on the AMBA AXI4-Lite CRC-32 RTL
 * to verify pin-level AXI4-Lite SLVERR propagates through Verilator -> SystemC -> usp-bus-v2
 * -> QEMU M-mode bridge -> guest synchronous load access fault -> non-zero failure exit.
 */
#include <stdint.h>
#include "soc_map.h"

#define R32(addr) (*(volatile uint32_t *)(uintptr_t)(addr))
#define R8(addr)  (*(volatile uint8_t  *)(uintptr_t)(addr))

static void finish(uint32_t code) {
    while (!(R8(SOC_PRIMARY_UART_BASE + 5U) & 0x40U)) {}
    R32(SOC_FINISHER_BASE) = code;
    for (;;) {
        __asm__ volatile("wfi");
    }
}

uint64_t trap_dispatch(uint64_t mcause, uint64_t mepc, uint64_t mtval) {
    (void)mcause;
    (void)mtval;
    finish(0x13333U);
    return mepc;
}

int main(void) {
    /* Must return AXI4-Lite SLVERR -> synchronous Load Access Fault trap (mcause=5) */
    volatile uint32_t bad = R32(SOC_AXI4_LITE_CRC32_0_BASE + 0xFCU);
    (void)bad;
    /* If SLVERR were silently ignored, we would falsely return PASS — never allowed! */
    finish(0x5555U);
    return 0;
}
