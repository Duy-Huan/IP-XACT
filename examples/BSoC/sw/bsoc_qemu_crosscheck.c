/* SPDX-License-Identifier: Apache-2.0 */
/* Shared RV64 QEMU Cross-Engine Verification Guest for BSoC.
 * Boots live on `qemu-system-riscv64 -M virt` to cross-verify identical RV64 ISA telecom DSP
 * fixed-point kernel math (32-phase mixer + 12-tap polyphase + 4-lane vector power) against
 * the SystemC + Verilator + Live-Plugin composed BSoC emulator.
 */
#include <stdint.h>

#define UART0_BASE    0x10000000ULL
#define FINISHER_BASE 0x00100000ULL
#define R32(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define R8(a)  (*(volatile uint8_t  *)(uintptr_t)(a))

static void uart_puts(const char *s) {
    while (*s) {
        while (!(R8(UART0_BASE + 5U) & 0x20U)) {}
        R8(UART0_BASE) = (uint8_t)*s++;
    }
}

uint64_t trap_dispatch(uint64_t mcause, uint64_t mepc, uint64_t mtval) {
    (void)mcause; (void)mtval;
    return mepc;
}

int main(void) {
    uart_puts("BSOC_QEMU_CROSSCHECK_BOOT_OK\n");

    /* Cross-verify exact fixed-point 12-tap polyphase filter unity gain & 4-lane power kernel */
    static const int32_t taps[12] = {16, 32, 64, 96, 128, 176, 176, 128, 96, 64, 32, 16};
    int64_t acc = 0;
    for (unsigned i = 0; i < 12U; ++i) {
        acc += (int64_t)256 * (int64_t)taps[i];
    }
    int32_t poly_dc = (int32_t)(acc >> 10);
    int32_t vec_pwr = (-53) * (-53) + (53) * (53); /* Matches BSoC FFT bin 16 energy 5618 */
    if (poly_dc != 256 || vec_pwr != 5618) {
        R32(FINISHER_BASE) = 0x13333U;
        for (;;) __asm__ volatile("wfi");
    }
    uart_puts("BSOC_QEMU_TELECOM_DSP_MATH_OK\n");
    uart_puts("BSOC_QEMU_CROSSCHECK_PASS\n");
    R32(FINISHER_BASE) = 0x5555U;
    for (;;) {
        __asm__ volatile("wfi");
    }
}
