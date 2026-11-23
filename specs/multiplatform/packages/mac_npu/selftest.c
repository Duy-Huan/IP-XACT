/* SPDX-License-Identifier: Apache-2.0 */
/* Bare-metal acceptance driver for the original SystemC user.mac_npu package. */
#include <stdint.h>
#include "soc.h"
#include "driver_bindings.h"

#define R32(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define R8(a) (*(volatile uint8_t *)(uintptr_t)(a))

#define MAC_REG_CTRL_CMD   0x00U
#define MAC_REG_OP_A       0x04U
#define MAC_REG_OP_B       0x08U
#define MAC_REG_ACC_RESULT 0x0CU
#define MAC_REG_IRQ_STATUS 0x10U

volatile unsigned boot_ready;
static volatile unsigned hits, active;
static volatile uint32_t expected_acc;

static void text(const char *s) {
    while (*s) {
        while (!(R8(USP_CONSOLE_BASE + 5) & 0x20)) {}
        R8(USP_CONSOLE_BASE) = *s++;
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

void trap_handler(void) __attribute__((interrupt("machine"), aligned(4)));
void trap_handler(void) {
    uintptr_t cause;
    __asm__ volatile("csrr %0,mcause" : "=r"(cause));
    unsigned irq = R32(USP_PLIC_BASE + 0x200004);
    if (cause != 0x800000000000000bUL || irq != usp_driver_irqs[active] ||
        R32(usp_driver_bases[active] + MAC_REG_ACC_RESULT) != expected_acc) {
        finish(0x13333);
    }
    R32(usp_driver_bases[active] + MAC_REG_IRQ_STATUS) = 1U;
    R32(USP_PLIC_BASE + 0x200004) = irq;
    ++hits;
}

int main(void) {
    R8(USP_CONSOLE_BASE + 1) = 0;
    R8(USP_CONSOLE_BASE + 3) = 0x80;
    R8(USP_CONSOLE_BASE) = 2;
    R8(USP_CONSOLE_BASE + 1) = 0;
    R8(USP_CONSOLE_BASE + 3) = 3;
    R8(USP_CONSOLE_BASE + 2) = 1;

    unsigned mask = 0;
    for (unsigned i = 0; i < USP_DRIVER_COUNT; i++) {
        R32(USP_PLIC_BASE + usp_driver_irqs[i] * 4) = 1;
        mask |= 1u << usp_driver_irqs[i];
        if (R32(usp_driver_bases[i] + MAC_REG_CTRL_CMD) != 0 ||
            R32(usp_driver_bases[i] + MAC_REG_ACC_RESULT) != 0 ||
            R32(usp_driver_bases[i] + MAC_REG_IRQ_STATUS) != 0) {
            finish(0x13333);
        }
    }
    text("USP_MAC_NPU_RESET_OK\n");

    R32(USP_PLIC_BASE + 0x2000) = mask;
    R32(USP_PLIC_BASE + 0x200000) = 0;
    __asm__ volatile("csrw mie,%0" : : "r"(1UL << 11));
    __asm__ volatile("csrsi mstatus,8");

    unsigned expected_hits = 0;
    for (active = 0; active < USP_DRIVER_COUNT; active++) {
        uint32_t a1 = 6u + active;
        uint32_t b1 = 7u;
        expected_acc = a1 * b1;
        R32(usp_driver_bases[active] + MAC_REG_OP_A) = a1;
        R32(usp_driver_bases[active] + MAC_REG_OP_B) = b1;
        R32(usp_driver_bases[active] + MAC_REG_CTRL_CMD) = 1u;
        ++expected_hits;
        for (unsigned n = 0; hits != expected_hits && n < 1000000; n++) {
            __asm__ volatile("nop");
        }
        if (hits != expected_hits || R32(usp_driver_bases[active] + MAC_REG_IRQ_STATUS) != 0) {
            finish(0x13333);
        }

        expected_acc += 5u * 8u;
        R32(usp_driver_bases[active] + MAC_REG_OP_A) = 5u;
        R32(usp_driver_bases[active] + MAC_REG_OP_B) = 8u;
        R32(usp_driver_bases[active] + MAC_REG_CTRL_CMD) = 1u;
        ++expected_hits;
        for (unsigned n = 0; hits != expected_hits && n < 1000000; n++) {
            __asm__ volatile("nop");
        }
        if (hits != expected_hits ||
            R32(usp_driver_bases[active] + MAC_REG_CTRL_CMD) != 2u ||
            R32(usp_driver_bases[active] + MAC_REG_ACC_RESULT) != expected_acc ||
            R32(usp_driver_bases[active] + MAC_REG_IRQ_STATUS) != 0) {
            finish(0x13333);
        }
    }
    text("USP_MAC_NPU_COMPUTE_IRQ_OK\n");
    finish(0x5555);
    return 0;
}
