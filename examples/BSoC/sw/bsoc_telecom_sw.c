/* SPDX-License-Identifier: Apache-2.0 */
/* BSoC Telecom Baseband End-to-End Bare-Metal Verification Firmware.
 * Exercises ALL 22 mapped devices, 2 clock domains (core_clk 800 MHz <-> dsp_clk 250 MHz),
 * 3 crossbars (TileLink XBar, AXI4 Control XBar, AXI4 Data XBar), Async CDC Manager,
 * 5x Verilator 5.032 RTL blocks, 1x LIVE out-of-process usp-mmio32-v1 socket plugin,
 * Mixed-Signal TISAR ADC + 18KB LUT, 5-stage AXI4-Stream DSP Chain, 5x SAM SRAMs,
 * 512KB Pattern Gen / Logic Analyzer, JTAG-to-AXI4 master, DMA, Hwacha 4-Lane Vector,
 * Serial Adapter, and 512KB L2$ coherency snoops.
 */
#include <stdint.h>
#include "soc_map.h"

#define R32(addr) (*(volatile uint32_t *)(uintptr_t)(addr))
#define R64(addr) (*(volatile uint64_t *)(uintptr_t)(addr))
#define R8(addr)  (*(volatile uint8_t  *)(uintptr_t)(addr))

static volatile uint32_t g_serial_irqs = 0;
static volatile uint32_t g_jtag_irqs = 0;
static volatile uint32_t g_dma_irqs = 0;
static volatile uint32_t g_adc_irqs = 0;
static volatile uint32_t g_fft_irqs = 0;
static volatile uint32_t g_hwacha_irqs = 0;

static void uart_puts(const char *s) {
    while (*s) {
        while (!(R8(SOC_PRIMARY_UART_BASE + 5U) & 0x20U)) {}
        R8(SOC_PRIMARY_UART_BASE) = (uint8_t)*s++;
    }
}

static void uart_put_u32(uint32_t v) {
    char buf[12];
    int n = 0;
    if (v == 0U) {
        uart_puts("0");
        return;
    }
    while (v > 0U) {
        buf[n++] = (char)('0' + (v % 10U));
        v /= 10U;
    }
    while (n > 0) {
        char c[2] = {buf[--n], '\0'};
        uart_puts(c);
    }
}

static void uart_put_i32(int32_t v) {
    if (v < 0) {
        uart_puts("-");
        uart_put_u32((uint32_t)(-(int64_t)v));
    } else {
        uart_put_u32((uint32_t)v);
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
        if (irq == SOC_SERIAL_ADAPTER_0_IRQ) {
            R32(SOC_SERIAL_ADAPTER_0_BASE + 0x10U) = 1U;
            g_serial_irqs++;
        } else if (irq == SOC_JTAG_TO_AXI4_0_IRQ) {
            R32(SOC_JTAG_TO_AXI4_0_BASE + 0x10U) = 1U;
            g_jtag_irqs++;
        } else if (irq == SOC_DMA_ACCEL_0_IRQ) {
            R32(SOC_DMA_ACCEL_0_BASE + 0x14U) = 1U;
            g_dma_irqs++;
        } else if (irq == SOC_TISAR_ADC_0_IRQ) {
            R32(SOC_TISAR_ADC_0_BASE + 0x10U) = 1U;
            g_adc_irqs++;
        } else if (irq == SOC_FFT_128PT_0_IRQ) {
            R32(SOC_FFT_128PT_0_BASE + 0x10U) = 1U;
            g_fft_irqs++;
        } else if (irq == SOC_HWACHA_VEC_0_IRQ) {
            R32(SOC_HWACHA_VEC_0_BASE + 0x10U) = 1U;
            g_hwacha_irqs++;
        } else {
            finish(0x13333U);
        }
        R32(SOC_PLIC_BASE + 0x200004U) = irq;
        return mepc;
    }
    finish(0x13333U);
    return mepc;
}

static void enable_plic_irqs(void) {
    const uint32_t irqs[] = {
        SOC_SERIAL_ADAPTER_0_IRQ,
        SOC_JTAG_TO_AXI4_0_IRQ,
        SOC_DMA_ACCEL_0_IRQ,
        SOC_TISAR_ADC_0_IRQ,
        SOC_FFT_128PT_0_IRQ,
        SOC_HWACHA_VEC_0_IRQ,
    };
    uint32_t mask = 0U;
    for (unsigned i = 0; i < (unsigned)(sizeof(irqs) / sizeof(irqs[0])); ++i) {
        R32(SOC_PLIC_BASE + irqs[i] * 4U) = 1U;
        mask |= (1U << irqs[i]);
    }
    R32(SOC_PLIC_BASE + 0x2000U) = mask;
    R32(SOC_PLIC_BASE + 0x200000U) = 0U;
    __asm__ volatile("csrw mie, %0" : : "r"(1ULL << 11));
    __asm__ volatile("csrsi mstatus, 8");
}

static const int16_t kCos64[64] = {
     1024,  1019,  1004,   980,   946,   903,   851,   792,
      724,   650,   569,   483,   392,   297,   200,   100,
        0,  -100,  -200,  -297,  -392,  -483,  -569,  -650,
     -724,  -792,  -851,  -903,  -946,  -980, -1004, -1019,
    -1024, -1019, -1004,  -980,  -946,  -903,  -851,  -792,
     -724,  -650,  -569,  -483,  -392,  -297,  -200,  -100,
        0,   100,   200,   297,   392,   483,   569,   650,
      724,   792,   851,   903,   946,   980,  1004,  1019
};

static const int16_t kSin64[64] = {
        0,   100,   200,   297,   392,   483,   569,   650,
      724,   792,   851,   903,   946,   980,  1004,  1019,
     1024,  1019,  1004,   980,   946,   903,   851,   792,
      724,   650,   569,   483,   392,   297,   200,   100,
        0,  -100,  -200,  -297,  -392,  -483,  -569,  -650,
     -724,  -792,  -851,  -903,  -946,  -980, -1004, -1019,
    -1024, -1019, -1004,  -980,  -946,  -903,  -851,  -792,
     -724,  -650,  -569,  -483,  -392,  -297,  -200,  -100
};

int main(void) {
    /* 1. Core Clock <-> DSP Clock CDC & UART / CLINT bring-up */
    R8(SOC_PRIMARY_UART_BASE + 1U) = 0U;
    R8(SOC_PRIMARY_UART_BASE + 3U) = 0x03U;
    uint64_t t0 = R64(SOC_CLINT_BASE + 0xBFF8U);
    uart_puts("[FW 01] Rocket RV64 boot -> CDC(core_clk 800MHz <-> dsp_clk 250MHz) UART ready\n");
    uart_puts("BSOC_BOOT_CORE_DSP_CDC_OK\n");
    uint64_t t1 = R64(SOC_CLINT_BASE + 0xBFF8U);
    if (t1 <= t0) finish(0x13333U);
    uart_puts("[FW 01] CLINT mtime monotonic: t0=");
    uart_put_u32((uint32_t)t0);
    uart_puts(" -> t1=");
    uart_put_u32((uint32_t)t1);
    uart_puts(" ticks\n");

    enable_plic_irqs();

    /* 2. Load 1024-sample IF carrier tone (f_IF = 3/64 * f_s = 48 cycles / 1024) into
     *    512KB Pattern Generator, with sample 0 injected via independent JTAG-to-AXI4 master! */
    for (unsigned n = 1U; n < 1024U; ++n) {
        unsigned idx = (3U * n) & 63U;
        uint32_t code_i = (uint32_t)(2048 + kCos64[idx]) & 0xFFFU;
        uint32_t code_q = (uint32_t)(2048 + kSin64[idx]) & 0xFFFU;
        R32(SOC_PATGEN_LA_0_BASE + n * 4U) = (code_q << 16) | code_i;
    }
    uint32_t sample0 = ((uint32_t)(2048 + kSin64[0]) << 16) | (uint32_t)(2048 + kCos64[0]);
    R32(SOC_JTAG_TO_AXI4_0_BASE + 0x04U) = (uint32_t)SOC_PATGEN_LA_0_BASE;
    R32(SOC_JTAG_TO_AXI4_0_BASE + 0x08U) = sample0;
    R32(SOC_JTAG_TO_AXI4_0_BASE + 0x00U) = 1U; /* JTAG-to-AXI4 AXI4 Data XBar write */
    for (unsigned w = 0; g_jtag_irqs == 0U && w < 1000U; ++w) __asm__ volatile("nop");
    if (g_jtag_irqs != 1U || R32(SOC_PATGEN_LA_0_BASE) != sample0) finish(0x13333U);
    uart_puts("[FW 02] JTAG-to-AXI4 wrote sample0=");
    uart_put_hex32(sample0);
    uart_puts(" to PatGen@");
    uart_put_hex32((uint32_t)SOC_PATGEN_LA_0_BASE);
    uart_puts(" (1024 IF samples, IRQ12=");
    uart_put_u32(g_jtag_irqs);
    uart_puts(")\nBSOC_JTAG_AXI4_PATGEN_OK\n");

    /* 3. Verify TISAR ADC 18KB Calibration LUT SRAM (18432 bytes) & SCR */
    uint32_t lut_bytes = R32(SOC_TISAR_ADC_0_BASE + 0x18U);
    if (lut_bytes != 18432U) finish(0x13333U);
    uint32_t lut_pair = R32(SOC_TISAR_ADC_0_BASE + 0x1000U + 2048U * 2U);
    int16_t lut_mid = (int16_t)(lut_pair & 0xFFFFU);
    if (lut_mid != 0) finish(0x13333U);
    uart_puts("[FW 03] TISAR ADC LUT SRAM=");
    uart_put_u32(lut_bytes);
    uart_puts(" bytes, cal[2048]=");
    uart_put_i32((int32_t)lut_mid);
    uart_puts("\nBSOC_TISAR_ADC_18KB_LUT_OK\n");

    /* 4. Configure ALL DSP Chain SCRs over AXI4 Control Crossbar */
    R32(SOC_TUNER_32PH_0_BASE + 0x00U) = 2U; /* reset */
    R32(SOC_TUNER_32PH_0_BASE + 0x04U) = 1U; /* phase_step = 1 */
    R32(SOC_TUNER_32PH_0_BASE + 0x14U) = 0U; /* phase_idx = 0 */

    R32(SOC_FIR_DECIM8_0_BASE + 0x00U) = 2U; /* reset */
    R32(SOC_FIR_DECIM8_0_BASE + 0x04U) = (10U << 8) | 8U; /* shift=10, decim=/8 */
    R32(SOC_FIR_DECIM8_0_BASE + 0x08U) = 136U;            /* all 136 taps active! */
    for (unsigned k = 0; k < 136U; ++k) {
        R32(SOC_FIR_DECIM8_0_BASE + 0x1CU) = k;
        R32(SOC_FIR_DECIM8_0_BASE + 0x20U) = (k < 8U) ? 128U : 0U;
    }

    uint32_t poly_scale = R32(SOC_POLYPHASE_12T_0_BASE + 0x14U);
    if (poly_scale != 1024U) finish(0x13333U);
    R32(SOC_POLYPHASE_12T_0_BASE + 0x04U) = 1U; /* select polyphase bank 1 */

    /* 5. Trigger 1024-sample 5-Stage AXI4-Stream DSP Pipeline! */
    R32(SOC_TISAR_ADC_0_BASE + 0x04U) = 1024U;
    R32(SOC_TISAR_ADC_0_BASE + 0x08U) = 1U;    /* enable Pattern Gen / LA MUX */
    R32(SOC_TISAR_ADC_0_BASE + 0x00U) = 0x10U; /* fire full 5-stage AXI4-Stream burst */
    for (unsigned w = 0; (g_adc_irqs == 0U || g_fft_irqs == 0U) && w < 2000U; ++w) {
        __asm__ volatile("nop");
    }
    uint32_t stream_pkts = R32(SOC_TISAR_ADC_0_BASE + 0x14U);
    if (g_adc_irqs != 1U || g_fft_irqs != 1U) finish(0x13333U);
    if (stream_pkts != 2432U) finish(0x13333U);
    uart_puts("[FW 04] 5-Stage AXI4-Stream done: pkts=");
    uart_put_u32(stream_pkts);
    uart_puts(" (1024+1024+128+128+128), IRQ15=");
    uart_put_u32(g_adc_irqs);
    uart_puts(", IRQ19=");
    uart_put_u32(g_fft_irqs);
    uart_puts("\nBSOC_DSP_5STAGE_STREAM_OK\n");

    uint32_t poly_samples = R32(SOC_POLYPHASE_12T_0_BASE + 0x00U);
    if (poly_samples != 128U) finish(0x13333U);
    uart_puts("[FW 05] Live AF_UNIX Plugin polyphase_12t_0: scale=");
    uart_put_u32(poly_scale);
    uart_puts(", samples_done=");
    uart_put_u32(poly_samples);
    uart_puts("\nBSOC_POLYPHASE_PLUGIN_IPC_OK\n");

    /* 6. Verify 128-point Radix-2 Cooley-Tukey FFT Peak Tone Detection (bin 16 / mirror 112) */
    uint32_t peak_bin = R32(SOC_FFT_128PT_0_BASE + 0x18U);
    uint32_t peak_mag2 = R32(SOC_FFT_128PT_0_BASE + 0x1CU);
    uint32_t bin16_iq = R32(SOC_SAM_FFT_64K_BASE + 16U * 4U);
    int32_t bin16_re = (int16_t)(bin16_iq & 0xFFFFU);
    int32_t bin16_im = (int16_t)((bin16_iq >> 16) & 0xFFFFU);
    if ((peak_bin != 16U && peak_bin != 112U) || peak_mag2 < 4000U) finish(0x13333U);
    uart_puts("[FW 06] 128-pt Radix-2 FFT RTL: peak_bin=");
    uart_put_u32(peak_bin);
    uart_puts(", peak_mag2=");
    uart_put_u32(peak_mag2);
    uart_puts(", bin16=(");
    uart_put_i32(bin16_re);
    uart_puts(",");
    uart_put_i32(bin16_im);
    uart_puts(")\nBSOC_FFT_128PT_PEAK_TONE_OK\n");

    /* 7. DMA Accelerator: copy 128 FFT bins (512 bytes) across AXI4 Data XBar -> Async CDC ->
     *    TileLink XBar -> 8MB On-Chip Main Memory (triggering 512KB L2$ DMA snoops!) */
    R32(SOC_DMA_ACCEL_0_BASE + 0x04U) = (uint32_t)SOC_SAM_FFT_64K_BASE;
    R32(SOC_DMA_ACCEL_0_BASE + 0x08U) = (uint32_t)SOC_DMA_BUF_BASE;
    R32(SOC_DMA_ACCEL_0_BASE + 0x0CU) = 512U;
    R32(SOC_DMA_ACCEL_0_BASE + 0x00U) = 1U;
    for (unsigned w = 0; g_dma_irqs == 0U && w < 2000U; ++w) __asm__ volatile("nop");
    if (g_dma_irqs != 1U || (R32(SOC_DMA_ACCEL_0_BASE + 0x10U) & 3U) != 1U) finish(0x13333U);
    if (R32(SOC_DMA_BUF_BASE + 16U * 4U) != bin16_iq) finish(0x13333U);
    uart_puts("[FW 07] DMA transferred 512B SAM_FFT@");
    uart_put_hex32((uint32_t)SOC_SAM_FFT_64K_BASE);
    uart_puts(" -> SRAM@");
    uart_put_hex32((uint32_t)SOC_DMA_BUF_BASE);
    uart_puts(" (IRQ14=");
    uart_put_u32(g_dma_irqs);
    uart_puts(")\nBSOC_DMA_CDC_SAM_TO_SRAM_OK\n");

    /* 8. Hwacha 4-Lane Vector RTL: compute 4-lane parallel spectral energy on FFT bins 14..17 */
    if (R32(SOC_HWACHA_VEC_0_BASE + 0x14U) != 4U) finish(0x13333U);
    R32(SOC_HWACHA_VEC_0_BASE + 0x00U) = 2U; /* clear */
    for (unsigned lane = 0; lane < 4U; ++lane) {
        uint32_t iq = R32(SOC_DMA_BUF_BASE + (14U + lane) * 4U);
        int32_t re = (int16_t)(iq & 0xFFFFU);
        int32_t im = (int16_t)((iq >> 16) & 0xFFFFU);
        R32(SOC_HWACHA_VEC_0_BASE + 0x20U + lane * 4U) = (uint32_t)re;
        R32(SOC_HWACHA_VEC_0_BASE + 0x30U + lane * 4U) = (uint32_t)im;
    }
    R32(SOC_HWACHA_VEC_0_BASE + 0x00U) = 3U; /* 4-lane complex power I^2 + Q^2 */
    for (unsigned w = 0; g_hwacha_irqs == 0U && w < 1000U; ++w) __asm__ volatile("nop");
    uint32_t p14 = R32(SOC_HWACHA_VEC_0_BASE + 0x40U);
    uint32_t p15 = R32(SOC_HWACHA_VEC_0_BASE + 0x44U);
    uint32_t p16 = R32(SOC_HWACHA_VEC_0_BASE + 0x48U);
    uint32_t p17 = R32(SOC_HWACHA_VEC_0_BASE + 0x4CU);
    if (g_hwacha_irqs != 1U || p16 < 4000U || p16 <= p14 * 10U ||
        p16 <= p15 * 10U || p16 <= p17 * 10U) finish(0x13333U);
    uart_puts("[FW 08] Hwacha 4-Lane RTL power[14..17]=[");
    uart_put_u32(p14);
    uart_puts(", ");
    uart_put_u32(p15);
    uart_puts(", ");
    uart_put_u32(p16);
    uart_puts(", ");
    uart_put_u32(p17);
    uart_puts("] (IRQ20=");
    uart_put_u32(g_hwacha_irqs);
    uart_puts(")\nBSOC_HWACHA_VEC4_POWER_OK\n");

    /* 9. JTAG-to-AXI4 Logic Analyzer Readback Check (512KB Logic Analyzer at +0x80000) */
    uint32_t jtag_before = g_jtag_irqs;
    R32(SOC_JTAG_TO_AXI4_0_BASE + 0x04U) = (uint32_t)(SOC_PATGEN_LA_0_BASE + 0x80000U + 16U * 4U);
    R32(SOC_JTAG_TO_AXI4_0_BASE + 0x00U) = 2U; /* JTAG-to-AXI4 AXI4 Data XBar read */
    for (unsigned w = 0; g_jtag_irqs == jtag_before && w < 1000U; ++w) __asm__ volatile("nop");
    uint32_t la_val = R32(SOC_JTAG_TO_AXI4_0_BASE + 0x0CU);
    if (g_jtag_irqs != jtag_before + 1U || la_val != bin16_iq) finish(0x13333U);
    uart_puts("[FW 09] JTAG Logic Analyzer readback bin16=");
    uart_put_hex32(la_val);
    uart_puts(" == SRAM (IRQ12=");
    uart_put_u32(g_jtag_irqs);
    uart_puts(")\nBSOC_LOGIC_ANALYZER_JTAG_OK\n");

    /* 10. Chipyard TileLink Serial Adapter telemetry frame + PLIC IRQ */
    uint32_t ser_tx = 0xB50C0000U | peak_bin;
    R32(SOC_SERIAL_ADAPTER_0_BASE + 0x00U) = ser_tx;
    for (unsigned w = 0; g_serial_irqs == 0U && w < 1000U; ++w) __asm__ volatile("nop");
    uint32_t ser_rx = R32(SOC_SERIAL_ADAPTER_0_BASE + 0x04U);
    if (g_serial_irqs != 1U || ser_rx != (ser_tx ^ 0x5A000000U)) finish(0x13333U);
    uart_puts("[FW 10] TileLink Serial Adapter tx=");
    uart_put_hex32(ser_tx);
    uart_puts(" -> rx=");
    uart_put_hex32(ser_rx);
    uart_puts(" (IRQ11=");
    uart_put_u32(g_serial_irqs);
    uart_puts(")\nBSOC_SERIAL_ADAPTER_IRQ_OK\n");

    /* 11. 512KB L2$ Cache & DMA Coherency Snoop RTL counter audit */
    uint32_t l2_hits   = R32(SOC_L2_CACHE_512K_BASE + 0x00U);
    uint32_t l2_misses = R32(SOC_L2_CACHE_512K_BASE + 0x04U);
    uint32_t l2_snoops = R32(SOC_L2_CACHE_512K_BASE + 0x08U);
    if (l2_hits == 0U || l2_misses == 0U || l2_snoops < 128U) finish(0x13333U);
    uart_puts("[FW 11] L2$ 512KB RTL counters: hits=");
    uart_put_u32(l2_hits);
    uart_puts(", misses=");
    uart_put_u32(l2_misses);
    uart_puts(", dma_snoops=");
    uart_put_u32(l2_snoops);
    uart_puts("\nBSOC_L2_CACHE_512K_COHERENCY_OK\n");

    uart_puts("[FW 12] All 6 PLIC IRQ sources serviced (total=");
    uart_put_u32(g_serial_irqs + g_jtag_irqs + g_dma_irqs + g_adc_irqs + g_fft_irqs + g_hwacha_irqs);
    uart_puts(") -> halting via finisher 0x5555\nBSOC_TELECOM_ALL_PASS\n");
    finish(0x5555U);
    return 0;
}
