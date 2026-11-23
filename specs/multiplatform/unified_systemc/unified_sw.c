/* SPDX-License-Identifier: Apache-2.0 */
/* Industrial End-to-End Dual-Hart SMP RISC-V64 Bare-Metal Verification & Benchmark Suite:
 *   1. QEMU-compatible UART, CLINT (multi-hart IPI), PLIC (9 IRQs), Finisher
 *   2. CPU-internal RISC-V PMP CSRs (pmpaddr0 / pmpcfg0)
 *   3. CoreMark-style Integer ALU / IEEE 802.3 CRC-32 ("123456789" -> 0xCBF43926) ROI
 *   4. Dual-Hart SMP: Hart 0 triggers CLINT msip[1] IPI -> wakes Hart 1 -> concurrent RV64A
 *      amoadd.w + lr.w/sc.w atomic barrier + parallel CRC-32 coherency verification
 *   5. Chipyard GCD accelerator (3 vectors + PLIC IRQ 12)
 *   6. OpenTitan GPIO (loopback + rising-edge PLIC IRQ 13 + masked clear)
 *   7. Verilated MAC + 2x2 MatMul NPU RTL (dot=126 + 2x2 MatMul + PLIC IRQ 20)
 *   8. Verilated FIPS 180-4 SHA-256 Crypto RTL (NIST KAT SHA256("abc") + PLIC IRQ 15)
 *   9. Verilated FIPS 197 AES-128 Crypto RTL (NIST Appendix B KAT + PLIC IRQ 17)
 *  10. Verilated 32-bit SPI Master Controller RTL (loopback 0xA55A1234 + PLIC IRQ 16)
 *  11. Verilated I2C Master + Loopback Slave FSM RTL (addr 0x50 + byte 0xC3 + PLIC IRQ 18)
 *  12. SystemC PMP Bus Guard (CPU unauthorized write Store Fault + PLIC IRQ 21)
 *  13. Third-master NoC DMA Engine (16B copy + PLIC IRQ 14 + unauthorized DMA PMP block)
 *  14. Verilated 64-line L2 Cache & SMP/DMA Coherency Snoop RTL counter audit
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
static volatile uint32_t g_dma_irqs = 0;
static volatile uint32_t g_dma_last_status = 0;
static volatile uint32_t g_sha_irqs = 0;
static volatile uint32_t g_aes_irqs = 0;
static volatile uint32_t g_spi_irqs = 0;
static volatile uint32_t g_i2c_irqs = 0;
static volatile uint32_t g_npu_irqs = 0;
static volatile uint32_t g_npu_acc = 0;
static volatile uint32_t g_pmp_guard_irqs = 0;
static volatile uint32_t g_store_faults = 0;
static volatile uint64_t g_last_fault_addr = 0;
static volatile uint32_t g_hart1_ipi_seen = 0;

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

static uint32_t crc32_ieee(const uint8_t *data, unsigned len) {
    uint32_t crc = 0xFFFFFFFFU;
    for (unsigned i = 0; i < len; ++i) {
        crc ^= (uint32_t)data[i];
        for (unsigned b = 0; b < 8U; ++b) {
            if (crc & 1U) {
                crc = (crc >> 1) ^ 0xEDB88320U;
            } else {
                crc >>= 1;
            }
        }
    }
    return ~crc;
}

static inline uint32_t amo_add_w(volatile uint32_t *ptr, uint32_t delta) {
    uint32_t prev;
    __asm__ volatile("amoadd.w %0, %2, (%1)" : "=r"(prev) : "r"(ptr), "r"(delta) : "memory");
    return prev;
}

static inline void lrsc_inc_w(volatile uint32_t *ptr) {
    uint32_t val, status;
    do {
        __asm__ volatile("lr.w %0, (%1)" : "=r"(val) : "r"(ptr) : "memory");
        val += 1U;
        __asm__ volatile("sc.w %0, %2, (%1)" : "=r"(status) : "r"(ptr), "r"(val) : "memory");
    } while (status != 0U);
}

uint64_t trap_dispatch(uint64_t mcause, uint64_t mepc, uint64_t mtval) {
    /* Machine Software Interrupt (CLINT IPI) */
    if (mcause == 0x8000000000000003ULL) {
        uint64_t hid = 0;
        __asm__ volatile("csrr %0, mhartid" : "=r"(hid));
        if (hid == 1ULL) {
            R32(USOC_SYS_CLINT_BASE + 4U) = 0U; /* Clear msip[1] */
            g_hart1_ipi_seen++;
        } else {
            R32(USOC_SYS_CLINT_BASE + 0U) = 0U; /* Clear msip[0] */
        }
        return mepc;
    }
    /* Machine External Interrupt (PLIC) */
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
        } else if (irq == USOC_DMA_ENGINE_0_IRQ) {
            g_dma_last_status = R32(USOC_DMA_ENGINE_0_BASE + 0x10U);
            R32(USOC_DMA_ENGINE_0_BASE + 0x14U) = 1U;
            g_dma_irqs++;
        } else if (irq == USOC_SHA256_CRYPTO_0_IRQ) {
            R32(USOC_SHA256_CRYPTO_0_BASE + 0x04U) = 1U;
            g_sha_irqs++;
        } else if (irq == USOC_AES128_CRYPTO_0_IRQ) {
            R32(USOC_AES128_CRYPTO_0_BASE + 0x04U) = 1U;
            g_aes_irqs++;
        } else if (irq == USOC_SPI_CTRL_0_IRQ) {
            R32(USOC_SPI_CTRL_0_BASE + 0x10U) = 1U;
            g_spi_irqs++;
        } else if (irq == USOC_I2C_CTRL_0_IRQ) {
            R32(USOC_I2C_CTRL_0_BASE + 0x14U) = 1U;
            g_i2c_irqs++;
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
        g_store_faults++;
        g_last_fault_addr = mtval;
        return mepc + 4ULL;
    }
    finish(0x13333U);
    return mepc;
}

/* Hart 1 SMP worker: waits for CLINT msip[1] IPI, computes CRC-32, performs RV64A atomics */
void hart1_main(void) {
    volatile uint32_t *smp_atomic = (volatile uint32_t *)(uintptr_t)(USOC_SMP_SHARED_MEM + 0x00U);
    volatile uint32_t *smp_crc    = (volatile uint32_t *)(uintptr_t)(USOC_SMP_SHARED_MEM + 0x04U);
    volatile uint32_t *smp_done   = (volatile uint32_t *)(uintptr_t)(USOC_SMP_SHARED_MEM + 0x08U);

    /* Enable Machine Software Interrupt (MSIE = bit 3) + MIE (mstatus bit 3) */
    __asm__ volatile("csrw mie, %0" : : "r"(1ULL << 3));
    __asm__ volatile("csrsi mstatus, 8");

    while (g_hart1_ipi_seen == 0U) {
        __asm__ volatile("wfi");
    }

    static const uint8_t crc_msg[9] = {'1','2','3','4','5','6','7','8','9'};
    *smp_crc = crc32_ieee(crc_msg, 9U);

    for (unsigned i = 0; i < 8U; ++i) {
        amo_add_w(smp_atomic, 1U);
    }
    for (unsigned i = 0; i < 4U; ++i) {
        lrsc_inc_w(smp_atomic);
    }
    amo_add_w(smp_done, 1U);
    for (;;) {
        __asm__ volatile("wfi");
    }
}

static void enable_plic_irqs(void) {
    const uint32_t irqs[9] = {
        USOC_CHIPYARD_GCD_0_IRQ,
        USOC_OPENTITAN_GPIO_0_IRQ,
        USOC_DMA_ENGINE_0_IRQ,
        USOC_SHA256_CRYPTO_0_IRQ,
        USOC_AES128_CRYPTO_0_IRQ,
        USOC_SPI_CTRL_0_IRQ,
        USOC_I2C_CTRL_0_IRQ,
        USOC_MAC_NPU_0_IRQ,
        USOC_PMP_GUARD_0_IRQ,
    };
    uint32_t mask = 0;
    for (unsigned i = 0; i < 9U; ++i) {
        R32(USOC_SYS_PLIC_BASE + irqs[i] * 4U) = 1U;
        mask |= (1U << irqs[i]);
    }
    R32(USOC_SYS_PLIC_BASE + 0x2000U) = mask;
    R32(USOC_SYS_PLIC_BASE + 0x200000U) = 0U;
    __asm__ volatile("csrw mie, %0" : : "r"((1ULL << 11) | (1ULL << 3)));
    __asm__ volatile("csrsi mstatus, 8");
}

int main(void) {
    /* 1. QEMU-compatible UART init & CLINT monotonic timer check */
    R8(USOC_QEMU_UART_0_BASE + 1U) = 0U;
    R8(USOC_QEMU_UART_0_BASE + 3U) = 0x03U;
    uint64_t t0 = R64(USOC_SYS_CLINT_BASE + 0xBFF8U);
    uart_puts("UNIFIED_SOC_BOOT_OK\n");
    uint64_t t1 = R64(USOC_SYS_CLINT_BASE + 0xBFF8U);
    if (t1 <= t0) finish(0x13333U);

    /* 2. CPU-internal RISC-V PMP CSRs */
    uint64_t pmp_addr_val = (uint64_t)(USOC_MAIN_DRAM_BASE >> 2) | 0x001FFFFFULL;
    uint64_t pmp_cfg_val = 0x1FULL;
    uint64_t rd_addr = 0, rd_cfg = 0;
    __asm__ volatile("csrw pmpaddr0, %0" : : "r"(pmp_addr_val));
    __asm__ volatile("csrw pmpcfg0, %0" : : "r"(pmp_cfg_val));
    __asm__ volatile("csrr %0, pmpaddr0" : "=r"(rd_addr));
    __asm__ volatile("csrr %0, pmpcfg0" : "=r"(rd_cfg));
    if (rd_addr != pmp_addr_val || (rd_cfg & 0xFFULL) != pmp_cfg_val) finish(0x13333U);
    uart_puts("RISCV_PMP_CSR_OK\n");

    /* 3. CoreMark-style Integer Compute ROI: IEEE 802.3 CRC-32("123456789") == 0xCBF43926 */
    static const uint8_t crc_msg[9] = {'1','2','3','4','5','6','7','8','9'};
    if (crc32_ieee(crc_msg, 9U) != 0xCBF43926U) finish(0x13333U);
    uart_puts("COREMARK_CRC32_ROI_OK\n");

    enable_plic_irqs();

    /* 4. Dual-Hart SMP: trigger CLINT IPI to Hart 1 + concurrent RV64A atomics */
    volatile uint32_t *smp_atomic = (volatile uint32_t *)(uintptr_t)(USOC_SMP_SHARED_MEM + 0x00U);
    volatile uint32_t *smp_crc    = (volatile uint32_t *)(uintptr_t)(USOC_SMP_SHARED_MEM + 0x04U);
    volatile uint32_t *smp_done   = (volatile uint32_t *)(uintptr_t)(USOC_SMP_SHARED_MEM + 0x08U);
    *smp_atomic = 0U;
    *smp_crc = 0U;
    *smp_done = 0U;
    R32(USOC_SYS_CLINT_BASE + 4U) = 1U; /* Fire msip[1] IPI over 2x2 NoC */
    for (unsigned i = 0; i < 8U; ++i) {
        amo_add_w(smp_atomic, 1U);
    }
    for (unsigned i = 0; i < 4U; ++i) {
        lrsc_inc_w(smp_atomic);
    }
    for (unsigned w = 0; *smp_done == 0U && w < 20000U; ++w) {
        __asm__ volatile("nop");
    }
    if (*smp_done != 1U || g_hart1_ipi_seen != 1U) finish(0x13333U);
    uart_puts("SMP_HART1_IPI_WAKE_OK\n");
    if (*smp_atomic != 24U || *smp_crc != 0xCBF43926U) finish(0x13333U);
    uart_puts("SMP_ATOMIC_BARRIER_OK\n");

    /* 5. Chipyard GCD over 2x2 NoC + PLIC IRQ 12 */
    static const uint32_t gcd_vecs[3][3] = {
        {48U, 18U, 6U}, {270U, 192U, 6U}, {65535U, 255U, 255U},
    };
    for (unsigned i = 0; i < 3U; ++i) {
        if (R32(USOC_CHIPYARD_GCD_0_BASE + 0x00U) != 2U) finish(0x13333U);
        uint32_t before = g_gcd_irqs;
        R32(USOC_CHIPYARD_GCD_0_BASE + 0x04U) = gcd_vecs[i][0];
        R32(USOC_CHIPYARD_GCD_0_BASE + 0x08U) = gcd_vecs[i][1];
        for (unsigned w = 0; g_gcd_irqs == before && w < 1000U; ++w) __asm__ volatile("nop");
        if (g_gcd_irqs != before + 1U || g_gcd_result != gcd_vecs[i][2]) finish(0x13333U);
    }
    uart_puts("CHIPYARD_GCD_IRQ_OK\n");

    /* 6. OpenTitan GPIO over 2x2 NoC + PLIC IRQ 13 */
    if (R32(USOC_OPENTITAN_GPIO_0_BASE + 0x10U) != 0U ||
        R32(USOC_OPENTITAN_GPIO_0_BASE + 0x00U) != 0U) finish(0x13333U);
    R32(USOC_OPENTITAN_GPIO_0_BASE + 0x20U) = 1U;
    R32(USOC_OPENTITAN_GPIO_0_BASE + 0x2CU) = 1U;
    R32(USOC_OPENTITAN_GPIO_0_BASE + 0x04U) = 1U;
    R32(USOC_OPENTITAN_GPIO_0_BASE + 0x14U) = 1U;
    for (unsigned w = 0; g_gpio_irqs == 0U && w < 1000U; ++w) __asm__ volatile("nop");
    if (g_gpio_irqs != 1U || g_gpio_state != 1U ||
        R32(USOC_OPENTITAN_GPIO_0_BASE + 0x10U) != 1U) finish(0x13333U);
    R32(USOC_OPENTITAN_GPIO_0_BASE + 0x18U) = 0x00010000U;
    if (R32(USOC_OPENTITAN_GPIO_0_BASE + 0x10U) != 0U) finish(0x13333U);
    uart_puts("OPENTITAN_GPIO_IRQ_OK\n");

    /* 7a. Verilated MAC NPU RTL scalar dot product (6*7 + 5*8 + 11*4 = 126) + PLIC IRQ 20 */
    static const uint32_t mac_ops[3][2] = {{6U, 7U}, {5U, 8U}, {11U, 4U}};
    for (unsigned i = 0; i < 3U; ++i) {
        uint32_t before = g_npu_irqs;
        R32(USOC_MAC_NPU_0_BASE + 0x04U) = mac_ops[i][0];
        R32(USOC_MAC_NPU_0_BASE + 0x08U) = mac_ops[i][1];
        R32(USOC_MAC_NPU_0_BASE + 0x00U) = 1U;
        for (unsigned w = 0; g_npu_irqs == before && w < 1000U; ++w) __asm__ volatile("nop");
        if (g_npu_irqs != before + 1U) finish(0x13333U);
    }
    if (g_npu_acc != 126U || R32(USOC_MAC_NPU_0_BASE + 0x00U) != 3U) finish(0x13333U);
    uart_puts("MAC_NPU_DOT_IRQ_OK\n");

    /* 7b. Verilated MAC NPU RTL 2x2 Matrix Multiply */
    uint32_t npu_before = g_npu_irqs;
    R32(USOC_MAC_NPU_0_BASE + 0x20U) = 1U; R32(USOC_MAC_NPU_0_BASE + 0x24U) = 2U;
    R32(USOC_MAC_NPU_0_BASE + 0x28U) = 3U; R32(USOC_MAC_NPU_0_BASE + 0x2CU) = 4U;
    R32(USOC_MAC_NPU_0_BASE + 0x30U) = 5U; R32(USOC_MAC_NPU_0_BASE + 0x34U) = 6U;
    R32(USOC_MAC_NPU_0_BASE + 0x38U) = 7U; R32(USOC_MAC_NPU_0_BASE + 0x3CU) = 8U;
    R32(USOC_MAC_NPU_0_BASE + 0x00U) = 3U;
    for (unsigned w = 0; g_npu_irqs == npu_before && w < 1000U; ++w) __asm__ volatile("nop");
    if (g_npu_irqs != npu_before + 1U ||
        R32(USOC_MAC_NPU_0_BASE + 0x40U) != 19U ||
        R32(USOC_MAC_NPU_0_BASE + 0x44U) != 22U ||
        R32(USOC_MAC_NPU_0_BASE + 0x48U) != 43U ||
        R32(USOC_MAC_NPU_0_BASE + 0x4CU) != 50U) finish(0x13333U);
    uart_puts("NPU_MATMUL_2X2_IRQ_OK\n");

    /* 8. Verilated FIPS 180-4 SHA-256 RTL: NIST KAT SHA-256("abc") + PLIC IRQ 15 */
    for (unsigned i = 0; i < 16U; ++i) R32(USOC_SHA256_CRYPTO_0_BASE + 0x10U + i * 4U) = 0U;
    R32(USOC_SHA256_CRYPTO_0_BASE + 0x10U) = 0x61626380U;
    R32(USOC_SHA256_CRYPTO_0_BASE + 0x4CU) = 24U;
    R32(USOC_SHA256_CRYPTO_0_BASE + 0x00U) = 1U;
    for (unsigned w = 0; g_sha_irqs == 0U && w < 1000U; ++w) __asm__ volatile("nop");
    static const uint32_t expected_sha256_abc[8] = {
        0xba7816bfU, 0x8f01cfeaU, 0x414140deU, 0x5dae2223U,
        0xb00361a3U, 0x96177a9cU, 0xb410ff61U, 0xf20015adU,
    };
    if (g_sha_irqs != 1U || R32(USOC_SHA256_CRYPTO_0_BASE + 0x08U) != 1U) finish(0x13333U);
    for (unsigned i = 0; i < 8U; ++i) {
        if (R32(USOC_SHA256_CRYPTO_0_BASE + 0x50U + i * 4U) != expected_sha256_abc[i]) {
            finish(0x13333U);
        }
    }
    uart_puts("SHA256_FIPS_KAT_IRQ_OK\n");

    /* 9. Verilated FIPS 197 AES-128 RTL: NIST Appendix B KAT + PLIC IRQ 17 */
    R32(USOC_AES128_CRYPTO_0_BASE + 0x10U) = 0x2b7e1516U;
    R32(USOC_AES128_CRYPTO_0_BASE + 0x14U) = 0x28aed2a6U;
    R32(USOC_AES128_CRYPTO_0_BASE + 0x18U) = 0xabf71588U;
    R32(USOC_AES128_CRYPTO_0_BASE + 0x1CU) = 0x09cf4f3cU;
    R32(USOC_AES128_CRYPTO_0_BASE + 0x20U) = 0x3243f6a8U;
    R32(USOC_AES128_CRYPTO_0_BASE + 0x24U) = 0x885a308dU;
    R32(USOC_AES128_CRYPTO_0_BASE + 0x28U) = 0x313198a2U;
    R32(USOC_AES128_CRYPTO_0_BASE + 0x2CU) = 0xe0370734U;
    R32(USOC_AES128_CRYPTO_0_BASE + 0x00U) = 1U;
    for (unsigned w = 0; g_aes_irqs == 0U && w < 1000U; ++w) __asm__ volatile("nop");
    if (g_aes_irqs != 1U ||
        R32(USOC_AES128_CRYPTO_0_BASE + 0x08U) != 1U ||
        R32(USOC_AES128_CRYPTO_0_BASE + 0x30U) != 0x3925841dU ||
        R32(USOC_AES128_CRYPTO_0_BASE + 0x34U) != 0x02dc09fbU ||
        R32(USOC_AES128_CRYPTO_0_BASE + 0x38U) != 0xdc118597U ||
        R32(USOC_AES128_CRYPTO_0_BASE + 0x3CU) != 0x196a0b32U) finish(0x13333U);
    uart_puts("AES128_FIPS197_IRQ_OK\n");

    /* 10. Verilated 32-bit SPI Master RTL loopback transfer + PLIC IRQ 16 */
    R32(USOC_SPI_CTRL_0_BASE + 0x08U) = 0xA55A1234U;
    R32(USOC_SPI_CTRL_0_BASE + 0x00U) = 0x00000107U;
    for (unsigned w = 0; g_spi_irqs == 0U && w < 1000U; ++w) __asm__ volatile("nop");
    if (g_spi_irqs != 1U ||
        R32(USOC_SPI_CTRL_0_BASE + 0x0CU) != 0xA55A1234U ||
        R32(USOC_SPI_CTRL_0_BASE + 0x14U) != 1U) finish(0x13333U);
    uart_puts("SPI_CTRL_RTL_IRQ_OK\n");

    /* 11. Verilated I2C Master + Loopback Slave FSM RTL (addr 0x50, data 0xC3) + PLIC IRQ 18 */
    R32(USOC_I2C_CTRL_0_BASE + 0x08U) = 0x50U;
    R32(USOC_I2C_CTRL_0_BASE + 0x0CU) = 0xC3U;
    R32(USOC_I2C_CTRL_0_BASE + 0x00U) = 0x07U; /* enable | loopback_slave | start */
    for (unsigned w = 0; g_i2c_irqs == 0U && w < 1000U; ++w) __asm__ volatile("nop");
    if (g_i2c_irqs != 1U ||
        (R32(USOC_I2C_CTRL_0_BASE + 0x04U) & 6U) != 6U ||
        R32(USOC_I2C_CTRL_0_BASE + 0x10U) != 0xC3U ||
        R32(USOC_I2C_CTRL_0_BASE + 0x18U) != 1U) finish(0x13333U);
    uart_puts("I2C_CTRL_RTL_IRQ_OK\n");

    /* 12. SystemC PMP Bus Guard blocking unauthorized CPU write (Store Fault + PLIC IRQ 21) */
    R32(USOC_PROTECTED_SCRATCH_BASE) = 0xCAFEBABEU;
    R32(USOC_PMP_GUARD_0_BASE + 0x04U) = (uint32_t)USOC_PROTECTED_SCRATCH_BASE;
    R32(USOC_PMP_GUARD_0_BASE + 0x08U) =
        (uint32_t)(USOC_PROTECTED_SCRATCH_BASE + USOC_PROTECTED_SCRATCH_SIZE);
    R32(USOC_PMP_GUARD_0_BASE + 0x00U) = 3U;
    if (R32(USOC_PROTECTED_SCRATCH_BASE) != 0xCAFEBABEU) finish(0x13333U);
    uint32_t faults_before = g_store_faults;
    R32(USOC_PROTECTED_SCRATCH_BASE) = 0xDEADBEEFU;
    for (unsigned w = 0; g_pmp_guard_irqs == 0U && w < 1000U; ++w) __asm__ volatile("nop");
    if (g_store_faults != faults_before + 1U ||
        g_last_fault_addr != USOC_PROTECTED_SCRATCH_BASE ||
        g_pmp_guard_irqs != 1U ||
        R32(USOC_PMP_GUARD_0_BASE + 0x14U) != 1U ||
        R32(USOC_PROTECTED_SCRATCH_BASE) != 0xCAFEBABEU) finish(0x13333U);
    uart_puts("PMP_GUARD_FAULT_IRQ_OK\n");

    /* 13. Third-master NoC DMA Engine: valid 16B copy + PLIC IRQ 14 + unauthorized DMA PMP block */
    static const uint32_t dma_pattern[4] = {
        0x11223344U, 0x55667788U, 0x99AABBCCU, 0xDDEEFF00U
    };
    for (unsigned i = 0; i < 4U; ++i) {
        R32(USOC_DMA_SRC_BUF + i * 4U) = dma_pattern[i];
        R32(USOC_DMA_DST_BUF + i * 4U) = 0U;
    }
    R32(USOC_DMA_ENGINE_0_BASE + 0x04U) = (uint32_t)USOC_DMA_SRC_BUF;
    R32(USOC_DMA_ENGINE_0_BASE + 0x08U) = (uint32_t)USOC_DMA_DST_BUF;
    R32(USOC_DMA_ENGINE_0_BASE + 0x0CU) = 16U;
    R32(USOC_DMA_ENGINE_0_BASE + 0x00U) = 1U;
    for (unsigned w = 0; g_dma_irqs == 0U && w < 1000U; ++w) __asm__ volatile("nop");
    if (g_dma_irqs != 1U || (g_dma_last_status & 3U) != 1U) finish(0x13333U);
    for (unsigned i = 0; i < 4U; ++i) {
        if (R32(USOC_DMA_DST_BUF + i * 4U) != dma_pattern[i]) finish(0x13333U);
    }
    uart_puts("NOC_DMA_MASTER_IRQ_OK\n");

    uint32_t dma_irqs_before = g_dma_irqs;
    uint32_t pmp_irqs_before = g_pmp_guard_irqs;
    R32(USOC_DMA_ENGINE_0_BASE + 0x04U) = (uint32_t)USOC_DMA_SRC_BUF;
    R32(USOC_DMA_ENGINE_0_BASE + 0x08U) = (uint32_t)USOC_PROTECTED_SCRATCH_BASE;
    R32(USOC_DMA_ENGINE_0_BASE + 0x0CU) = 4U;
    R32(USOC_DMA_ENGINE_0_BASE + 0x00U) = 1U;
    for (unsigned w = 0;
         (g_dma_irqs == dma_irqs_before || g_pmp_guard_irqs == pmp_irqs_before) && w < 1000U;
         ++w) __asm__ volatile("nop");
    if (g_dma_irqs != dma_irqs_before + 1U ||
        (g_dma_last_status & 2U) == 0U ||
        g_pmp_guard_irqs != pmp_irqs_before + 1U ||
        R32(USOC_PMP_GUARD_0_BASE + 0x14U) != 2U ||
        R32(USOC_PROTECTED_SCRATCH_BASE) != 0xCAFEBABEU) finish(0x13333U);
    uart_puts("DMA_PMP_BLOCK_OK\n");

    /* 14. Verilated 64-line L2 Cache & SMP/DMA Coherency Snoop RTL counter audit */
    uint32_t l2_hits   = R32(USOC_L2_CACHE_0_BASE + 0x00U);
    uint32_t l2_misses = R32(USOC_L2_CACHE_0_BASE + 0x04U);
    uint32_t l2_snoops = R32(USOC_L2_CACHE_0_BASE + 0x08U);
    if (l2_hits == 0U || l2_misses == 0U || l2_snoops < 8U) finish(0x13333U);
    uart_puts("L2_CACHE_COHERENCY_OK\n");

    uart_puts("UNIFIED_SOC_ALL_PASS\n");
    finish(0x5555U);
    return 0;
}
