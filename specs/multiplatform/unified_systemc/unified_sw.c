/* SPDX-License-Identifier: Apache-2.0 */
/* End-to-end RISC-V64 bare-metal software exercising EVERY mapped IP in the unified SystemC SoC:
 *   1. QEMU-compatible NS16550 UART, CLINT, PLIC, and Finisher
 *   2. CPU-internal RISC-V PMP CSRs (pmpaddr0 / pmpcfg0)
 *   3. Chipyard GCD accelerator (MMIO + PLIC IRQ)
 *   4. OpenTitan TL-UL GPIO (loopback + rising-edge PLIC IRQ + masked output clear)
 *   5. SystemC MAC NPU-lite (3-step dot-product accumulation + PLIC IRQ + RO fault check)
 *   6. SystemC PMP/IOPMP Bus Guard (protected scratch region write block + Store Fault + PLIC IRQ)
 */
#include <stdint.h>
#include "unified_soc_map.h"

#define R32(addr) (*(volatile uint32_t *)(uintptr_t)(addr))
#define R64(addr) (*(volatile uint64_t *)(uintptr_t)(addr))
#define R8(addr)  (*(volatile uint8_t  *)(uintptr_t)(addr))

static volatile uint32_t g_gcd_result = 0;
static volatile uint32_t g_gcd_irqs = 0;
static volatile uint32_t g_gpio_irqs = 0;
static volatile uint32_t g_gpio_state = 0;
static volatile uint32_t g_npu_irqs = 0;
static volatile uint32_t g_npu_acc = 0;
static volatile uint32_t g_pmp_guard_irqs = 0;
static volatile uint32_t g_store_faults = 0;
static volatile uint64_t g_last_fault_addr = 0;

static void uart_puts(const char *s) {
    while (*s) {
        while (!(R8(USOC_QEMU_UART_0_BASE + 5U) & 0x20U)) {}
        R8(USOC_QEMU_UART_0_BASE) = (uint8_t)*s++;
    }
}

static void finish(uint32_t code) {
    while (!(R8(USOC_QEMU_UART_0_BASE + 5U) & 0x40U)) {}
    R32(USOC_SIM_FINISHER_BASE) = code;
    for (;;) {
        __asm__ volatile("wfi");
    }
}

uint64_t trap_dispatch(uint64_t mcause, uint64_t mepc, uint64_t mtval) {
    if (mcause == 0x800000000000000bULL) {
        uint32_t irq = R32(USOC_SYS_PLIC_BASE + 0x200004U);
        if (irq == USOC_CHIPYARD_GCD_0_IRQ) {
            g_gcd_result = R32(USOC_CHIPYARD_GCD_0_BASE + 0x0CU);
            g_gcd_irqs++;
        } else if (irq == USOC_OPENTITAN_GPIO_0_IRQ) {
            uint32_t pending = R32(USOC_OPENTITAN_GPIO_0_BASE + 0x00U);
            g_gpio_state = pending;
            R32(USOC_OPENTITAN_GPIO_0_BASE + 0x00U) = pending;
            g_gpio_irqs++;
        } else if (irq == USOC_MAC_NPU_0_IRQ) {
            g_npu_acc = R32(USOC_MAC_NPU_0_BASE + 0x0CU);
            R32(USOC_MAC_NPU_0_BASE + 0x10U) = 1U;
            g_npu_irqs++;
        } else if (irq == USOC_PMP_GUARD_0_IRQ) {
            R32(USOC_PMP_GUARD_0_BASE + 0x10U) = 1U;
            g_pmp_guard_irqs++;
        } else {
            finish(0x13333U);
        }
        R32(USOC_SYS_PLIC_BASE + 0x200004U) = irq;
        return mepc;
    }
    if (mcause == 7ULL) {
        /* Synchronous Store Access Fault (e.g. from PMP Bus Guard or RO register write). */
        g_store_faults++;
        g_last_fault_addr = mtval;
        return mepc + 4ULL;
    }
    finish(0x13333U);
    return mepc;
}

static void enable_plic_irqs(void) {
    const uint32_t irqs[4] = {
        USOC_CHIPYARD_GCD_0_IRQ,
        USOC_OPENTITAN_GPIO_0_IRQ,
        USOC_MAC_NPU_0_IRQ,
        USOC_PMP_GUARD_0_IRQ,
    };
    uint32_t mask = 0;
    for (unsigned i = 0; i < 4U; ++i) {
        R32(USOC_SYS_PLIC_BASE + irqs[i] * 4U) = 1U;
        mask |= (1U << irqs[i]);
    }
    R32(USOC_SYS_PLIC_BASE + 0x2000U) = mask;
    R32(USOC_SYS_PLIC_BASE + 0x200000U) = 0U;
    __asm__ volatile("csrw mie, %0" : : "r"(1ULL << 11));
    __asm__ volatile("csrsi mstatus, 8");
}

int main(void) {
    /* 1. QEMU-compatible UART init & CLINT monotonic timer check */
    R8(USOC_QEMU_UART_0_BASE + 1U) = 0U;
    R8(USOC_QEMU_UART_0_BASE + 3U) = 0x03U;
    uint64_t t0 = R64(USOC_SYS_CLINT_BASE + 0xBFF8U);
    uart_puts("UNIFIED_SOC_BOOT_OK\n");
    uint64_t t1 = R64(USOC_SYS_CLINT_BASE + 0xBFF8U);
    if (t1 <= t0) {
        finish(0x13333U);
    }

    /* 2. CPU-internal RISC-V PMP CSR configuration & readback (NAPOT region covering RAM) */
    uint64_t pmp_addr_val = (uint64_t)(USOC_MAIN_DRAM_BASE >> 2) | 0x001FFFFFULL;
    uint64_t pmp_cfg_val = 0x1FULL; /* NAPOT | R | W | X */
    uint64_t rd_addr = 0, rd_cfg = 0;
    __asm__ volatile("csrw pmpaddr0, %0" : : "r"(pmp_addr_val));
    __asm__ volatile("csrw pmpcfg0, %0" : : "r"(pmp_cfg_val));
    __asm__ volatile("csrr %0, pmpaddr0" : "=r"(rd_addr));
    __asm__ volatile("csrr %0, pmpcfg0" : "=r"(rd_cfg));
    if (rd_addr != pmp_addr_val || (rd_cfg & 0xFFULL) != pmp_cfg_val) {
        finish(0x13333U);
    }
    uart_puts("RISCV_PMP_CSR_OK\n");

    enable_plic_irqs();

    /* 3. Chipyard GCD over SystemC 2x2 NoC (Router 0,0 -> Router 0,1) + PLIC IRQ 12 */
    static const uint32_t gcd_vecs[3][3] = {
        {48U, 18U, 6U},
        {270U, 192U, 6U},
        {65535U, 255U, 255U},
    };
    for (unsigned i = 0; i < 3U; ++i) {
        if (R32(USOC_CHIPYARD_GCD_0_BASE + 0x00U) != 2U) {
            finish(0x13333U);
        }
        uint32_t before = g_gcd_irqs;
        R32(USOC_CHIPYARD_GCD_0_BASE + 0x04U) = gcd_vecs[i][0];
        R32(USOC_CHIPYARD_GCD_0_BASE + 0x08U) = gcd_vecs[i][1];
        for (unsigned wait = 0; g_gcd_irqs == before && wait < 1000U; ++wait) {
            __asm__ volatile("nop");
        }
        if (g_gcd_irqs != before + 1U || g_gcd_result != gcd_vecs[i][2]) {
            finish(0x13333U);
        }
    }
    uart_puts("CHIPYARD_GCD_IRQ_OK\n");

    /* 4. OpenTitan GPIO over SystemC 2x2 NoC (Router 0,0 -> Router 0,1) + PLIC IRQ 13 */
    if (R32(USOC_OPENTITAN_GPIO_0_BASE + 0x10U) != 0U ||
        R32(USOC_OPENTITAN_GPIO_0_BASE + 0x00U) != 0U) {
        finish(0x13333U);
    }
    R32(USOC_OPENTITAN_GPIO_0_BASE + 0x20U) = 1U; /* DIRECT_OE */
    R32(USOC_OPENTITAN_GPIO_0_BASE + 0x2CU) = 1U; /* INTR_CTRL_EN_RISING */
    R32(USOC_OPENTITAN_GPIO_0_BASE + 0x04U) = 1U; /* INTR_ENABLE */
    R32(USOC_OPENTITAN_GPIO_0_BASE + 0x14U) = 1U; /* DIRECT_OUT rising edge */
    for (unsigned wait = 0; g_gpio_irqs == 0U && wait < 1000U; ++wait) {
        __asm__ volatile("nop");
    }
    if (g_gpio_irqs != 1U || g_gpio_state != 1U ||
        R32(USOC_OPENTITAN_GPIO_0_BASE + 0x10U) != 1U) {
        finish(0x13333U);
    }
    R32(USOC_OPENTITAN_GPIO_0_BASE + 0x18U) = 0x00010000U; /* MASKED_OUT_LOWER clear bit 0 */
    if (R32(USOC_OPENTITAN_GPIO_0_BASE + 0x10U) != 0U) {
        finish(0x13333U);
    }
    uart_puts("OPENTITAN_GPIO_IRQ_OK\n");

    /* 5. SystemC MAC NPU-lite over 2x2 NoC (Router 0,0 -> Router 1,1, 2 hops) + PLIC IRQ 20
     *    Dot product: 6*7 + 5*8 + 11*4 = 42 + 40 + 44 = 126 */
    static const uint32_t mac_ops[3][2] = {{6U, 7U}, {5U, 8U}, {11U, 4U}};
    for (unsigned i = 0; i < 3U; ++i) {
        uint32_t before = g_npu_irqs;
        R32(USOC_MAC_NPU_0_BASE + 0x04U) = mac_ops[i][0];
        R32(USOC_MAC_NPU_0_BASE + 0x08U) = mac_ops[i][1];
        R32(USOC_MAC_NPU_0_BASE + 0x00U) = 1U;
        for (unsigned wait = 0; g_npu_irqs == before && wait < 1000U; ++wait) {
            __asm__ volatile("nop");
        }
        if (g_npu_irqs != before + 1U) {
            finish(0x13333U);
        }
    }
    if (g_npu_acc != 126U || R32(USOC_MAC_NPU_0_BASE + 0x00U) != 3U) {
        finish(0x13333U);
    }
    uart_puts("MAC_NPU_DOT_IRQ_OK\n");

    /* 6. SystemC PMP/IOPMP Bus Guard (Router 1,1) protecting scratch RAM [BASE, BASE+SIZE) */
    R32(USOC_PROTECTED_SCRATCH_BASE) = 0xCAFEBABEU;
    if (R32(USOC_PROTECTED_SCRATCH_BASE) != 0xCAFEBABEU) {
        finish(0x13333U);
    }
    R32(USOC_PMP_GUARD_0_BASE + 0x04U) = (uint32_t)USOC_PROTECTED_SCRATCH_BASE;
    R32(USOC_PMP_GUARD_0_BASE + 0x08U) =
        (uint32_t)(USOC_PROTECTED_SCRATCH_BASE + USOC_PROTECTED_SCRATCH_SIZE);
    R32(USOC_PMP_GUARD_0_BASE + 0x00U) = 3U; /* bit0=enable | bit1=deny_write */

    /* Read must still succeed */
    if (R32(USOC_PROTECTED_SCRATCH_BASE) != 0xCAFEBABEU) {
        finish(0x13333U);
    }
    /* Unauthorized write MUST be blocked by NoC PMP Guard -> Store Fault + PLIC IRQ 21! */
    uint32_t faults_before = g_store_faults;
    R32(USOC_PROTECTED_SCRATCH_BASE) = 0xDEADBEEFU;
    for (unsigned wait = 0; g_pmp_guard_irqs == 0U && wait < 1000U; ++wait) {
        __asm__ volatile("nop");
    }
    if (g_store_faults != faults_before + 1U ||
        g_last_fault_addr != USOC_PROTECTED_SCRATCH_BASE ||
        g_pmp_guard_irqs != 1U ||
        R32(USOC_PMP_GUARD_0_BASE + 0x0CU) != (uint32_t)USOC_PROTECTED_SCRATCH_BASE ||
        R32(USOC_PMP_GUARD_0_BASE + 0x14U) != 1U ||
        R32(USOC_PROTECTED_SCRATCH_BASE) != 0xCAFEBABEU) {
        finish(0x13333U);
    }
    uart_puts("PMP_GUARD_FAULT_IRQ_OK\n");

    uart_puts("UNIFIED_SOC_ALL_PASS\n");
    finish(0x5555U);
    return 0;
}
