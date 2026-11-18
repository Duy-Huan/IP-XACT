/* SPDX-License-Identifier: Apache-2.0 */
#include <stdint.h>
#include "soc.h"
#include "driver_bindings.h"
#define R32(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define R8(a) (*(volatile uint8_t *)(uintptr_t)(a))
volatile unsigned boot_ready;
static volatile unsigned hits, active;
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
void trap_handler(void) __attribute__((interrupt("machine"),aligned(4)));
void trap_handler(void) {
    uintptr_t cause;
    __asm__ volatile("csrr %0,mcause":"=r"(cause));
    unsigned irq = R32(USP_PLIC_BASE + 0x200004);
    if (cause != 0x800000000000000bUL || irq != usp_driver_irqs[active] ||
        R32(usp_driver_bases[active]) != 41 + active) finish(0x13333);
    R32(usp_driver_bases[active] + 4) = 1;
    R32(USP_PLIC_BASE + 0x200004) = irq;
    ++hits;
}
int main(void) {
    R8(USP_CONSOLE_BASE + 1)=0; R8(USP_CONSOLE_BASE + 3)=0x80;
    R8(USP_CONSOLE_BASE)=2; R8(USP_CONSOLE_BASE + 1)=0;
    R8(USP_CONSOLE_BASE + 3)=3; R8(USP_CONSOLE_BASE + 2)=1;
    unsigned mask=0;
    for(unsigned i=0;i<USP_DRIVER_COUNT;i++) {
        R32(USP_PLIC_BASE + usp_driver_irqs[i]*4)=1;
        mask |= 1u << usp_driver_irqs[i];
        if(R32(usp_driver_bases[i]) != 0 || R32(usp_driver_bases[i]+4) != 0)
            finish(0x13333);
    }
    text("USP_PACKAGE_RESET_OK\n");
    R32(USP_PLIC_BASE + 0x2000)=mask;
    R32(USP_PLIC_BASE + 0x200000)=0;
    __asm__ volatile("csrw mie,%0"::"r"(1UL<<11));
    __asm__ volatile("csrsi mstatus,8");
    for(active=0;active<USP_DRIVER_COUNT;active++) {
        R32(usp_driver_bases[active])=41+active;
        for(unsigned n=0;hits!=active+1 && n<1000000;n++) __asm__ volatile("nop");
        if(hits != active+1 || R32(usp_driver_bases[active]+4) != 0) finish(0x13333);
    }
    text("USP_EXTERNAL_IRQ_OK\n");
    finish(0x5555); return 0;
}
