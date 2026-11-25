/* SPDX-License-Identifier: Apache-2.0 */
/* Bare-metal RV64 selftest for usp.dma_burst_v2 (usp-bus-v2 Reverse DMA + 64-bit Burst). */
#include <stdint.h>
#include "soc.h"
#include "driver_bindings.h"

#define R32(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define R64(a) (*(volatile uint64_t *)(uintptr_t)(a))
#define R8(a)  (*(volatile uint8_t  *)(uintptr_t)(a))

volatile unsigned boot_ready;

static void text(const char *s) {
    while (*s) {
        while (!(R8(USP_CONSOLE_BASE + 5) & 0x20)) {}
        R8(USP_CONSOLE_BASE) = (uint8_t)*s++;
    }
}

static void finish(unsigned code) {
    while (!(R8(USP_CONSOLE_BASE + 5) & 0x40)) {}
    R32(USP_FINISHER_BASE) = code;
    for (;;) __asm__ volatile("wfi");
}

void secondary_main(uintptr_t hart) {
    (void)hart;
    for (;;) __asm__ volatile("wfi");
}

int main(void) {
    const uintptr_t base = usp_driver_bases[0];
    R32(base + 0x00U) = 2U; /* reset */
    if (R32(base + 0x20U) != 0x5A5A5A5AU) finish(0x13333U);
    text("USP_DMA_BURST_V2_RESET_OK\n");
    R32(base + 0x100U) = 0xCAFEBABEU;
    if (R32(base + 0x100U) != 0xCAFEBABEU) finish(0x23333U);
    text("USP_DMA_BURST_V2_SELFTEST_OK\n");
    finish(0x5555U);
    return 0;
}
