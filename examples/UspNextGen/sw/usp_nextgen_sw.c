/* SPDX-License-Identifier: Apache-2.0 */
/* UspNextGen End-to-End Bare-Metal RV64 Verification Firmware.
 * Runs directly on stock qemu-system-riscv64 (TCG/KVM) driving SystemC MultiFabricInterconnect:
 *   1. Heavy >2M-instruction native RV64 compute workload in zero-copy mmap RAM + quantum sync
 *   2. Chipyard/firtool pin-level AXI4 Full 64-bit Euclidean GCD FSM + 64-bit INCR burst SRAM
 *   3. AMBA pin-level AXI4-Lite 32-bit IEEE 802.3 CRC-32 ("123456789" -> 0xCBF43926)
 *   4. OpenTitan pin-level TL-UL 32-bit GPIO & edge-triggered PLIC IRQ
 *   5. Live out-of-process usp-bus-v2 plugin: 64-bit bursts, quantum sync, & autonomous reverse
 *      bus-master DMA reading/writing QEMU shared mmap RAM with L2$ RTL snoops
 *   6. Unmodified legacy usp-mmio32-v1 plugin executing concurrently (backward compatibility)
 */
#include <stdint.h>
#include "soc_map.h"
#include "usp_bus_v2_guest.h"

#define R32(addr) (*(volatile uint32_t *)(uintptr_t)(addr))
#define R64(addr) (*(volatile uint64_t *)(uintptr_t)(addr))
#define R8(addr)  (*(volatile uint8_t  *)(uintptr_t)(addr))

static volatile uint32_t g_gcd_irqs = 0;
static volatile uint32_t g_dma_v2_irqs = 0;
static volatile uint32_t g_crc_irqs = 0;
static volatile uint32_t g_gpio_irqs = 0;

static void uart_puts(const char *s) {
    while (*s) {
        while (!(R8(SOC_PRIMARY_UART_BASE + 5U) & 0x20U)) {}
        R8(SOC_PRIMARY_UART_BASE) = (uint8_t)*s++;
    }
}

static void uart_put_u64(uint64_t v) {
    char buf[24];
    int n = 0;
    if (v == 0ULL) {
        uart_puts("0");
        return;
    }
    while (v > 0ULL) {
        buf[n++] = (char)('0' + (v % 10ULL));
        v /= 10ULL;
    }
    while (n > 0) {
        char c[2] = {buf[--n], '\0'};
        uart_puts(c);
    }
}

static void uart_put_hex32(uint32_t v) {
    static const char hex[] = "0123456789ABCDEF";
    char buf[11];
    buf[0] = '0';
    buf[1] = 'x';
    for (int i = 0; i < 8; ++i) {
        buf[2 + i] = hex[(v >> (28 - i * 4)) & 0xFU];
    }
    buf[10] = '\0';
    uart_puts(buf);
}

static void finish(uint32_t code) {
    while (!(R8(SOC_PRIMARY_UART_BASE + 5U) & 0x40U)) {}
    R32(SOC_FINISHER_BASE) = code;
    for (;;) {
        __asm__ volatile("wfi");
    }
}

uint64_t trap_dispatch(uint64_t mcause, uint64_t mepc, uint64_t mtval) {
    (void)mtval;
    if (mcause == 0x800000000000000bULL) {
        uint32_t irq = R32(SOC_PLIC_BASE + 0x200004U);
        if (irq == SOC_CHIPYARD_GCD_0_IRQ) {
            R32(SOC_CHIPYARD_GCD_0_BASE + 0x00U) = 2U; /* clear IRQ */
            g_gcd_irqs++;
        } else if (irq == SOC_DMA_BURST_V2_0_IRQ) {
            R32(SOC_DMA_BURST_V2_0_BASE + 0x04U) = 1U; /* RW1C IRQ */
            g_dma_v2_irqs++;
        } else if (irq == SOC_AXI4_LITE_CRC32_0_IRQ) {
            R32(SOC_AXI4_LITE_CRC32_0_BASE + 0x00U) = 4U; /* clear IRQ */
            g_crc_irqs++;
        } else if (irq == SOC_OPENTITAN_GPIO_0_IRQ) {
            uint32_t st = R32(SOC_OPENTITAN_GPIO_0_BASE + 0x00U);
            R32(SOC_OPENTITAN_GPIO_0_BASE + 0x00U) = st; /* RW1C */
            g_gpio_irqs++;
        } else {
            finish(0x13333U);
        }
        R32(SOC_PLIC_BASE + 0x200004U) = irq;
        return mepc;
    }
    finish(0x23333U);
    return mepc;
}

static void enable_plic_irqs(void) {
    const uint32_t irqs[] = {
        SOC_CHIPYARD_GCD_0_IRQ,
        SOC_DMA_BURST_V2_0_IRQ,
        SOC_AXI4_LITE_CRC32_0_IRQ,
        SOC_OPENTITAN_GPIO_0_IRQ,
    };
    uint32_t mask = 0U;
    for (unsigned i = 0; i < (unsigned)(sizeof(irqs) / sizeof(irqs[0])); ++i) {
        R32(SOC_PLIC_BASE + irqs[i] * 4U) = 1U;
        mask |= (1U << irqs[i]);
    }
    R32(SOC_PLIC_BASE + 0x2000U) = mask;
    R32(SOC_PLIC_BASE + 0x200000U) = 0U;
    uint64_t mie = (1ULL << 11);
    uint64_t mstatus = 0x8ULL;
    __asm__ volatile("csrw mie, %0" :: "r"(mie));
    __asm__ volatile("csrs mstatus, %0" :: "r"(mstatus));
}

int main(void) {
    enable_plic_irqs();
    uart_puts("USP NextGen Boot: QEMU CPU Master + usp-bus-v2 + AXI4/AXI4-Lite/TL-UL RTL\n");

    /* ------------------------------------------------------------------
     * 1. Heavy Native RV64 Compute Workload (>2.5M retired instructions)
     *    Executes at full QEMU TCG speed in zero-copy mmap RAM while
     *    periodic CLINT quantum timers sync QEMU <-> SystemC <-> plugins.
     * ------------------------------------------------------------------ */
    volatile uint64_t acc = 0x123456789ABCDEF0ULL;
    for (uint64_t i = 0; i < 300000ULL; ++i) {
        uint64_t x = acc ^ (i * 0x9E3779B97F4A7C15ULL);
        x = (x << 13) ^ (x >> 7);
        acc = x + (i | 1ULL);
    }
    if (acc == 0ULL) finish(0x30001U);
    uint64_t sync_ps = usp_bus_time_sync(50000ULL);
    uart_puts("Workload acc=");
    uart_put_u64(acc & 0xFFFFULL);
    uart_puts(" sync_ps=");
    uart_put_u64(sync_ps);
    uart_puts("\nNEXTGEN_QEMU_WORKLOAD_OK\n");

    /* ------------------------------------------------------------------
     * 2. Chipyard / firtool Pin-Level AXI4 Full 64-bit RTL (GCD + Burst)
     * ------------------------------------------------------------------ */
    R64(SOC_CHIPYARD_GCD_0_BASE + 0x08U) = 1071ULL;
    R64(SOC_CHIPYARD_GCD_0_BASE + 0x10U) = 462ULL;
    R32(SOC_CHIPYARD_GCD_0_BASE + 0x00U) = 1U; /* Start Euclidean GCD FSM */
    while (g_gcd_irqs == 0U) {
        __asm__ volatile("wfi");
    }
    uint64_t gcd_res = R64(SOC_CHIPYARD_GCD_0_BASE + 0x18U);
    if (gcd_res != 21ULL) finish(0x30002U);

    /* 64-bit 8-beat AXI4 INCR Burst Write + Read (64 bytes) on Chipyard RTL */
    static uint64_t axi_tx[8];
    static uint64_t axi_rx[8];
    uint64_t exp_xor = 0ULL;
    for (unsigned i = 0; i < 8U; ++i) {
        axi_tx[i] = 0xA100000000000000ULL | ((uint64_t)(i + 1U) << 32) | (0x55AA0000ULL + i);
        exp_xor ^= axi_tx[i];
        axi_rx[i] = 0ULL;
    }
    if (usp_bus_burst_write64(SOC_CHIPYARD_GCD_0_BASE + 0x100U, axi_tx, sizeof(axi_tx)) != 0) {
        finish((3U << 16) | 0x3333U);
    }
    if (usp_bus_burst_read64(SOC_CHIPYARD_GCD_0_BASE + 0x100U, axi_rx, sizeof(axi_rx)) != 0) {
        finish((4U << 16) | 0x3333U);
    }
    for (unsigned i = 0; i < 8U; ++i) {
        if (axi_rx[i] != axi_tx[i]) finish((5U << 16) | 0x3333U);
    }
    uint64_t hw_xor = R64(SOC_CHIPYARD_GCD_0_BASE + 0x20U);
    if (hw_xor != exp_xor) finish((6U << 16) | 0x3333U);
    uart_puts("Chipyard AXI4 Full GCD(1071,462)=");
    uart_put_u64(gcd_res);
    uart_puts(" 8x64b INCR burst verified\nNEXTGEN_AXI4_FULL_GCD_BURST_OK\n");

    /* ------------------------------------------------------------------
     * 3. AMBA Pin-Level AXI4-Lite 32-bit RTL (IEEE 802.3 CRC-32 across CDC)
     * ------------------------------------------------------------------ */
    R32(SOC_AXI4_LITE_CRC32_0_BASE + 0x00U) = 1U; /* reset CRC */
    /* Standard IEEE 802.3 vector "123456789" -> 0xCBF43926 */
    R32(SOC_AXI4_LITE_CRC32_0_BASE + 0x10U) = 0x34333231U; /* "1234" */
    R32(SOC_AXI4_LITE_CRC32_0_BASE + 0x10U) = 0x38373635U; /* "5678" */
    R32(SOC_AXI4_LITE_CRC32_0_BASE + 0x04U) = (uint32_t)'9';
    R32(SOC_AXI4_LITE_CRC32_0_BASE + 0x00U) = 2U; /* finalize & raise IRQ */
    while (g_crc_irqs == 0U) {
        __asm__ volatile("wfi");
    }
    uint32_t crc_val = R32(SOC_AXI4_LITE_CRC32_0_BASE + 0x08U);
    uint32_t crc_cnt = R32(SOC_AXI4_LITE_CRC32_0_BASE + 0x0CU);
    if (crc_val != 0xCBF43926U || crc_cnt != 9U) finish((7U << 16) | 0x3333U);
    uart_puts("AXI4-Lite CRC32('123456789')=");
    uart_put_hex32(crc_val);
    uart_puts("\nNEXTGEN_AXI4_LITE_CRC32_OK\n");

    /* ------------------------------------------------------------------
     * 4. OpenTitan Pin-Level TL-UL 32-bit RTL (GPIO + Rising-Edge IRQ)
     * ------------------------------------------------------------------ */
    R32(SOC_OPENTITAN_GPIO_0_BASE + 0x18U) = 0x00FF00FFU; /* DIRECT_OE */
    R32(SOC_OPENTITAN_GPIO_0_BASE + 0x1CU) = 0x00000005U; /* INTR_CTRL_EN_RISING bits 0,2 */
    R32(SOC_OPENTITAN_GPIO_0_BASE + 0x04U) = 0x00000005U; /* INTR_ENABLE */
    R32(SOC_OPENTITAN_GPIO_0_BASE + 0x14U) = 0x00A50055U; /* DIRECT_OUT -> triggers rising IRQ */
    while (g_gpio_irqs == 0U) {
        __asm__ volatile("wfi");
    }
    uint32_t gpio_in = R32(SOC_OPENTITAN_GPIO_0_BASE + 0x10U);
    if (gpio_in != 0x00A50055U) finish((8U << 16) | 0x3333U);
    uart_puts("OpenTitan TL-UL GPIO DATA_IN=");
    uart_put_hex32(gpio_in);
    uart_puts(" irq_cnt=");
    uart_put_u64(g_gpio_irqs);
    uart_puts("\nNEXTGEN_OPENTITAN_TLUL_GPIO_OK\n");

    /* ------------------------------------------------------------------
     * 5. Live Out-of-Process `usp-bus-v2` Plugin:
     *    64-bit Burst + Quantum Sync + Autonomous Reverse Bus-Master DMA
     *    directly into QEMU's zero-copy mmap RAM + Verilator L2$ Snoops!
     * ------------------------------------------------------------------ */
    static uint64_t p_tx[16];
    static uint64_t p_rx[16];
    for (unsigned i = 0; i < 16U; ++i) {
        p_tx[i] = 0xB200000000000000ULL | ((uint64_t)i << 16) | (0x1000ULL + i * 7ULL);
        p_rx[i] = 0ULL;
    }
    if (usp_bus_burst_write64(SOC_DMA_BURST_V2_0_BASE + 0x100U, p_tx, sizeof(p_tx)) != 0) {
        finish((9U << 16) | 0x3333U);
    }
    if (usp_bus_burst_read64(SOC_DMA_BURST_V2_0_BASE + 0x100U, p_rx, sizeof(p_rx)) != 0) {
        finish((10U << 16) | 0x3333U);
    }
    for (unsigned i = 0; i < 16U; ++i) {
        if (p_rx[i] != p_tx[i]) finish((11U << 16) | 0x3333U);
    }

    /* Prepare source & destination buffers in QEMU shared mmap RAM (and prime L2$ RTL!) */
    const uint64_t dma_src = SOC_DMA_BUF_BASE;
    const uint64_t dma_dst = SOC_DMA_BUF_BASE + 0x400ULL;
    const uint64_t xor_key = 0xCAFEBABE12345678ULL;
    static uint64_t dma_seed[16];
    static uint64_t dma_verify_l2[16];
    for (unsigned i = 0; i < 16U; ++i) {
        dma_seed[i] = 0x0102030405060700ULL + (uint64_t)i * 0x0011223344556677ULL;
        R64(dma_dst + i * 8U) = 0ULL;
    }
    /* Write + read via SystemC coherent xbar to populate & hit L2$ RTL before DMA snoop */
    if (usp_bus_burst_write64(dma_src, dma_seed, sizeof(dma_seed)) != 0) {
        finish((12U << 16) | 0x3333U);
    }
    if (usp_bus_burst_read64(dma_src, dma_verify_l2, sizeof(dma_verify_l2)) != 0) {
        finish((13U << 16) | 0x3333U);
    }
    for (unsigned i = 0; i < 16U; ++i) {
        if (dma_verify_l2[i] != dma_seed[i]) finish((14U << 16) | 0x3333U);
    }

    /* Program live usp-bus-v2 plugin and fire autonomous Reverse Bus-Master DMA! */
    R64(SOC_DMA_BURST_V2_0_BASE + 0x08U) = dma_src;
    R64(SOC_DMA_BURST_V2_0_BASE + 0x10U) = dma_dst;
    R32(SOC_DMA_BURST_V2_0_BASE + 0x18U) = 128U; /* 16 x 64-bit words */
    R64(SOC_DMA_BURST_V2_0_BASE + 0x20U) = xor_key;
    R32(SOC_DMA_BURST_V2_0_BASE + 0x00U) = 1U; /* triggers USP_OP_DMA_READ + USP_OP_DMA_WRITE */
    while (g_dma_v2_irqs == 0U) {
        __asm__ volatile("wfi");
    }

    /* Verify transformed words directly inside QEMU native mmap RAM (zero-copy coherence!) */
    uint64_t exp_dma_cksum = 0ULL;
    for (unsigned i = 0; i < 16U; ++i) {
        uint64_t exp_w = (dma_seed[i] ^ xor_key) + 1ULL;
        uint64_t got_w = R64(dma_dst + i * 8U);
        if (got_w != exp_w) finish((15U << 16) | 0x3333U);
        exp_dma_cksum ^= exp_w;
    }
    uint64_t hw_dma_cksum = R64(SOC_DMA_BURST_V2_0_BASE + 0x28U);
    uint32_t hw_dma_bytes = R32(SOC_DMA_BURST_V2_0_BASE + 0x1CU);
    uint32_t hw_qsyncs = R32(SOC_DMA_BURST_V2_0_BASE + 0x30U);
    if (hw_dma_cksum != exp_dma_cksum || hw_dma_bytes < 256U) finish((16U << 16) | 0x3333U);
    uart_puts("usp-bus-v2 Reverse DMA bytes=");
    uart_put_u64(hw_dma_bytes);
    uart_puts(" quantum_syncs=");
    uart_put_u64(hw_qsyncs);
    uart_puts("\nNEXTGEN_USP_BUS_V2_DMA_BURST_SYNC_OK\n");

    /* ------------------------------------------------------------------
     * 6. Backward-Compatible Unmodified `usp-mmio32-v1` Live Plugin
     * ------------------------------------------------------------------ */
    uint32_t tap_ck = R32(SOC_POLYPHASE_V1_0_BASE + 0x14U);
    if (tap_ck != 1024U) finish((17U << 16) | 0x3333U);
    R32(SOC_POLYPHASE_V1_0_BASE + 0x08U) = 64U;
    R32(SOC_POLYPHASE_V1_0_BASE + 0x00U) = 1U;
    uint32_t poly_out = R32(SOC_POLYPHASE_V1_0_BASE + 0x0CU);
    R32(SOC_POLYPHASE_V1_0_BASE + 0x10U) = 1U;
    if (poly_out != 1U) finish((18U << 16) | 0x3333U);
    uart_puts("usp-mmio32-v1 Polyphase tap_ck=");
    uart_put_u64(tap_ck);
    uart_puts(" out=");
    uart_put_u64(poly_out);
    uart_puts("\nNEXTGEN_USP_MMIO32_V1_COMPAT_OK\n");

    /* ------------------------------------------------------------------
     * 7. Verilator L2$ Snoop Monitor Telemetry Check
     * ------------------------------------------------------------------ */
    uint32_t l2_hits = R32(SOC_L2_CACHE_512K_BASE + 0x00U);
    uint32_t l2_misses = R32(SOC_L2_CACHE_512K_BASE + 0x04U);
    uint32_t l2_snoops = R32(SOC_L2_CACHE_512K_BASE + 0x08U);
    if (l2_hits == 0U || l2_misses == 0U || l2_snoops == 0U) finish((19U << 16) | 0x3333U);
    uart_puts("L2$ RTL hits=");
    uart_put_u64(l2_hits);
    uart_puts(" misses=");
    uart_put_u64(l2_misses);
    uart_puts(" dma_snoops=");
    uart_put_u64(l2_snoops);
    uart_puts("\nNEXTGEN_L2_SNOOP_OK\nUSP_NEXTGEN_PASS\n");
    finish(0x5555U);
    return 0;
}
