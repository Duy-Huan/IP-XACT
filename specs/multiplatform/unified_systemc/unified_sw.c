/* SPDX-License-Identifier: Apache-2.0 */
/* Industrial End-to-End RISC-V64 Bare-Metal Verification & Benchmark Suite for Unified SoC:
 *   1. QEMU-compatible UART, CLINT, PLIC, Finisher
 *   2. CPU-internal RISC-V PMP CSRs (pmpaddr0 / pmpcfg0)
 *   3. CoreMark-style Integer ALU / IEEE 802.3 CRC-32 ("123456789" -> 0xCBF43926) ROI
 *   4. Chipyard GCD accelerator (3 vectors + PLIC IRQ 12)
 *   5. OpenTitan GPIO (loopback + rising-edge PLIC IRQ 13 + masked clear)
 *   6. Verilated MAC + 2x2 MatMul NPU RTL (dot=126 + [[1,2],[3,4]]x[[5,6],[7,8]] + PLIC IRQ 20)
 *   7. Verilated FIPS 180-4 SHA-256 Crypto RTL (NIST KAT SHA256("abc") + PLIC IRQ 15)
 *   8. Verilated 32-bit SPI Master Controller RTL (loopback 0xA55A1234 + PLIC IRQ 16)
 *   9. SystemC PMP Bus Guard (CPU unauthorized write Store Fault + PLIC IRQ 21)
 *  10. Second-master NoC DMA Engine (16B copy + PLIC IRQ 14 + unauthorized DMA PMP block)
 *  11. Verilated 64-line L2 Cache & DMA Coherency Snoop RTL performance counter audit
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
static volatile uint32_t g_spi_irqs = 0;
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
        } else if (irq == USOC_DMA_ENGINE_0_IRQ) {
            g_dma_last_status = R32(USOC_DMA_ENGINE_0_BASE + 0x10U);
            R32(USOC_DMA_ENGINE_0_BASE + 0x14U) = 1U;
            g_dma_irqs++;
        } else if (irq == USOC_SHA256_CRYPTO_0_IRQ) {
            R32(USOC_SHA256_CRYPTO_0_BASE + 0x04U) = 1U;
            g_sha_irqs++;
        } else if (irq == USOC_SPI_CTRL_0_IRQ) {
            R32(USOC_SPI_CTRL_0_BASE + 0x10U) = 1U;
            g_spi_irqs++;
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

static void enable_plic_irqs(void) {
    const uint32_t irqs[7] = {
        USOC_CHIPYARD_GCD_0_IRQ,
        USOC_OPENTITAN_GPIO_0_IRQ,
        USOC_DMA_ENGINE_0_IRQ,
        USOC_SHA256_CRYPTO_0_IRQ,
        USOC_SPI_CTRL_0_IRQ,
        USOC_MAC_NPU_0_IRQ,
        USOC_PMP_GUARD_0_IRQ,
    };
    uint32_t mask = 0;
    for (unsigned i = 0; i < 7U; ++i) {
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

    /* 4. Chipyard GCD over 2x2 NoC + PLIC IRQ 12 */
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

    /* 5. OpenTitan GPIO over 2x2 NoC + PLIC IRQ 13 */
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

    /* 6a. Verilated MAC NPU RTL scalar dot product (6*7 + 5*8 + 11*4 = 126) + PLIC IRQ 20 */
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

    /* 6b. Verilated MAC NPU RTL 2x2 Matrix Multiply: [[1,2],[3,4]] x [[5,6],[7,8]] = [[19,22],[43,50]] */
    uint32_t npu_before = g_npu_irqs;
    R32(USOC_MAC_NPU_0_BASE + 0x20U) = 1U; R32(USOC_MAC_NPU_0_BASE + 0x24U) = 2U;
    R32(USOC_MAC_NPU_0_BASE + 0x28U) = 3U; R32(USOC_MAC_NPU_0_BASE + 0x2CU) = 4U;
    R32(USOC_MAC_NPU_0_BASE + 0x30U) = 5U; R32(USOC_MAC_NPU_0_BASE + 0x34U) = 6U;
    R32(USOC_MAC_NPU_0_BASE + 0x38U) = 7U; R32(USOC_MAC_NPU_0_BASE + 0x3CU) = 8U;
    R32(USOC_MAC_NPU_0_BASE + 0x00U) = 3U; /* MATMUL_CMD */
    for (unsigned w = 0; g_npu_irqs == npu_before && w < 1000U; ++w) __asm__ volatile("nop");
    if (g_npu_irqs != npu_before + 1U ||
        R32(USOC_MAC_NPU_0_BASE + 0x40U) != 19U ||
        R32(USOC_MAC_NPU_0_BASE + 0x44U) != 22U ||
        R32(USOC_MAC_NPU_0_BASE + 0x48U) != 43U ||
        R32(USOC_MAC_NPU_0_BASE + 0x4CU) != 50U) finish(0x13333U);
    uart_puts("NPU_MATMUL_2X2_IRQ_OK\n");

    /* 7. Verilated FIPS 180-4 SHA-256 RTL: NIST KAT SHA-256("abc") + PLIC IRQ 15 */
    for (unsigned i = 0; i < 16U; ++i) R32(USOC_SHA256_CRYPTO_0_BASE + 0x10U + i * 4U) = 0U;
    R32(USOC_SHA256_CRYPTO_0_BASE + 0x10U) = 0x61626380U; /* "abc" + 0x80 pad */
    R32(USOC_SHA256_CRYPTO_0_BASE + 0x4CU) = 24U;         /* bit length = 24 */
    R32(USOC_SHA256_CRYPTO_0_BASE + 0x00U) = 1U;          /* start_first_block */
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

    /* 8. Verilated 32-bit SPI Master RTL loopback transfer + PLIC IRQ 16 */
    R32(USOC_SPI_CTRL_0_BASE + 0x08U) = 0xA55A1234U;
    R32(USOC_SPI_CTRL_0_BASE + 0x00U) = 0x00000107U; /* enable | loopback | start | div=1 */
    for (unsigned w = 0; g_spi_irqs == 0U && w < 1000U; ++w) __asm__ volatile("nop");
    if (g_spi_irqs != 1U ||
        R32(USOC_SPI_CTRL_0_BASE + 0x0CU) != 0xA55A1234U ||
        R32(USOC_SPI_CTRL_0_BASE + 0x14U) != 1U) finish(0x13333U);
    uart_puts("SPI_CTRL_RTL_IRQ_OK\n");

    /* 9. SystemC PMP Bus Guard blocking unauthorized CPU write (Store Fault + PLIC IRQ 21) */
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

    /* 10. Second-master NoC DMA Engine: valid 16B copy + PLIC IRQ 14 + unauthorized DMA PMP block */
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

    /* 11. Verilated 64-line L2 Cache & DMA Coherency Snoop RTL counter audit */
    uint32_t l2_hits   = R32(USOC_L2_CACHE_0_BASE + 0x00U);
    uint32_t l2_misses = R32(USOC_L2_CACHE_0_BASE + 0x04U);
    uint32_t l2_snoops = R32(USOC_L2_CACHE_0_BASE + 0x08U);
    if (l2_hits == 0U || l2_misses == 0U || l2_snoops < 8U) finish(0x13333U);
    uart_puts("L2_CACHE_COHERENCY_OK\n");

    uart_puts("UNIFIED_SOC_ALL_PASS\n");
    finish(0x5555U);
    return 0;
}
