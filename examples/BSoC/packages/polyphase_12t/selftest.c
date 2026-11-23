/* SPDX-License-Identifier: Apache-2.0 */
/* Bare-metal driver self-test for bsoc.polyphase_12t (12-tap fixed poly-phase filter). */
#include <stdint.h>

#define REG32(addr) (*(volatile uint32_t *)(uintptr_t)(addr))

int bsoc_polyphase_12t_selftest(uintptr_t base, void (*puts_fn)(const char *)) {
    REG32(base + 0x00U) = 2U; /* reset */
    if (REG32(base + 0x14U) != 1024U) return -1;
    puts_fn("BSOC_POLYPHASE_RESET_OK\n");

    REG32(base + 0x04U) = 1U; /* phase 1 */
    for (unsigned i = 0; i < 12U; ++i) {
        REG32(base + 0x08U) = 256U;
        REG32(base + 0x00U) = 1U;
        REG32(base + 0x10U) = 1U;
    }
    if (REG32(base + 0x0CU) != 256U || REG32(base + 0x00U) != 12U) return -2;
    puts_fn("BSOC_POLYPHASE_FILTER_IRQ_OK\n");
    return 0;
}
