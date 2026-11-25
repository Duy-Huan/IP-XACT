/* SPDX-License-Identifier: Apache-2.0
 * QEMU M-mode PMP + VirtIO-MMIO v2 `usp-bus-v2` Bridge.
 *
 * Enables stock `qemu-system-riscv64` (TCG or KVM) to act as the real CPU Master directly
 * driving SystemC `MultiFabricInterconnect` + Verilator RTL + live `usp-bus-v2`/`usp-mmio32-v1`
 * plugins:
 *   - Guest RAM [SOC_MAIN_RAM_BASE, SOC_MAIN_RAM_BASE + SOC_MAIN_RAM_SIZE) executes natively
 *     at full QEMU TCG/KVM speed via PMP RWX permission on zero-copy shared mmap RAM.
 *   - All non-RAM accesses trap via PMP to M-mode, are decoded (RV64I/A + RVC + Sv39 walk),
 *     and forwarded over VirtIO-MMIO Console AF_UNIX (`usp-bus-v2`) to SystemC QemuCpuMaster.
 *   - Periodic M-mode CLINT timer interrupts emit `USP_OP_TIME_SYNC` quantum barriers with
 *     real `minstret` instruction counts and deliver SystemC PLIC IRQs to guest `mtvec`.
 */
#include <stddef.h>
#include <stdint.h>

#include "qemu_usp_bridge.h"
#include "soc_map.h"
#include "usp_bus_protocol.hpp"

#define M_R32(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define M_R64(a) (*(volatile uint64_t *)(uintptr_t)(a))
#define M_W32(a, v) (*(volatile uint32_t *)(uintptr_t)(a) = (uint32_t)(v))
#define M_W64(a, v) (*(volatile uint64_t *)(uintptr_t)(a) = (uint64_t)(v))

/* QEMU virt built-in hardware addresses (accessible only from M-mode, hidden from S-mode by PMP) */
#define QEMU_VIRT_FINISHER   0x00100000ULL
#define QEMU_VIRT_CLINT_CMP  0x02004000ULL
#define QEMU_VIRT_CLINT_TIME 0x0200BFF8ULL
#define QEMU_VIRT_MMIO_START 0x10001000ULL
#define QEMU_VIRT_MMIO_END   0x10008000ULL
#define QEMU_VIRT_MMIO_STEP  0x00001000ULL

/* VirtIO-MMIO v2 register offsets */
#define VIO_MAGIC           0x000U
#define VIO_VERSION         0x004U
#define VIO_DEVICE_ID       0x008U
#define VIO_DEV_FEAT        0x010U
#define VIO_DEV_FEAT_SEL    0x014U
#define VIO_DRV_FEAT        0x020U
#define VIO_DRV_FEAT_SEL    0x024U
#define VIO_QUEUE_SEL       0x030U
#define VIO_QUEUE_NUM_MAX   0x034U
#define VIO_QUEUE_NUM       0x038U
#define VIO_QUEUE_READY     0x044U
#define VIO_QUEUE_NOTIFY    0x050U
#define VIO_INTR_STATUS     0x060U
#define VIO_INTR_ACK        0x064U
#define VIO_STATUS          0x070U
#define VIO_QUEUE_DESC_LO   0x080U
#define VIO_QUEUE_DESC_HI   0x084U
#define VIO_QUEUE_AVAIL_LO  0x090U
#define VIO_QUEUE_AVAIL_HI  0x094U
#define VIO_QUEUE_USED_LO   0x0A0U
#define VIO_QUEUE_USED_HI   0x0A4U

#define VQ_SIZE 8U
#define VQ_BUF_BYTES 4224U /* >= 48-byte UspBusV2Frame + 4096-byte burst */
#define VRING_DESC_F_WRITE 2U

typedef struct __attribute__((packed)) VirtqDesc {
    uint64_t addr;
    uint32_t len;
    uint16_t flags;
    uint16_t next;
} VirtqDesc;

typedef struct __attribute__((packed)) VirtqAvail {
    uint16_t flags;
    uint16_t idx;
    uint16_t ring[VQ_SIZE];
    uint16_t used_event;
} VirtqAvail;

typedef struct __attribute__((packed)) VirtqUsedElem {
    uint32_t id;
    uint32_t len;
} VirtqUsedElem;

typedef struct __attribute__((packed)) VirtqUsed {
    uint16_t flags;
    uint16_t idx;
    VirtqUsedElem ring[VQ_SIZE];
    uint16_t avail_event;
} VirtqUsed;

static VirtqDesc  g_rx_desc[VQ_SIZE] __attribute__((aligned(16)));
static VirtqAvail g_rx_avail __attribute__((aligned(4)));
static VirtqUsed  g_rx_used __attribute__((aligned(4)));
static uint8_t    g_rx_bufs[VQ_SIZE][VQ_BUF_BYTES] __attribute__((aligned(16)));

static VirtqDesc  g_tx_desc[VQ_SIZE] __attribute__((aligned(16)));
static VirtqAvail g_tx_avail __attribute__((aligned(4)));
static VirtqUsed  g_tx_used __attribute__((aligned(4)));
static uint8_t    g_tx_buf[VQ_BUF_BYTES] __attribute__((aligned(16)));

static uint64_t g_vio_base = 0ULL;
static uint16_t g_rx_used_last = 0U;
static uint16_t g_tx_used_last = 0U;
static uint32_t g_rx_cur_id = 0U;
static uint32_t g_rx_cur_off = 0U;
static uint32_t g_rx_cur_len = 0U;
static int      g_rx_has_cur = 0;

static uint32_t g_seq = 0U;
static uint32_t g_last_irq_mask = 0U;
static uint64_t g_quantum_ns = 100000ULL; /* updated by USP_OP_RESET response */
static uint64_t g_quantum_ticks = 1000ULL;
static uint64_t g_t0_ticks = 0ULL;
static uint64_t g_instret_base = 0ULL;
static uint64_t g_bridge_instret = 0ULL;
static uint64_t g_trap_entry_instret = 0ULL;
static uint64_t g_bridge_ticks = 0ULL;
static uint64_t g_trap_entry_ticks = 0ULL;
static int      g_in_trap = 0;

/* Virtualized guest M-mode CSR state */
static uint64_t g_v_mstatus = 0x00006000ULL;
static uint64_t g_v_mie = 0ULL;
static uint64_t g_v_mtvec = 0ULL;
static uint64_t g_v_mscratch = 0ULL;
static uint64_t g_v_mepc = 0ULL;
static uint64_t g_v_mcause = 0ULL;
static uint64_t g_v_mtval = 0ULL;
static uint64_t g_v_fcsr = 0ULL;
static uint64_t g_v_pmpcfg0 = 0ULL;
static uint64_t g_v_pmpaddr[4];

extern char _start[];
extern void _usp_m_trap_entry(void);

static inline void mem_fence(void) {
    __asm__ volatile("fence rw, rw" ::: "memory");
}

static void b_memcpy(void *dst, const void *src, size_t n) {
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    for (size_t i = 0; i < n; ++i) d[i] = s[i];
}

static inline uint64_t read_minstret(void) {
    uint64_t v = 0;
    __asm__ volatile("csrr %0, minstret" : "=r"(v));
    return v;
}

static inline uint64_t guest_retired_instret(void) {
    uint64_t cur = g_in_trap ? g_trap_entry_instret : read_minstret();
    uint64_t total = (cur >= g_instret_base) ? (cur - g_instret_base) : 0ULL;
    return (total >= g_bridge_instret) ? (total - g_bridge_instret) : 0ULL;
}

static inline uint64_t read_mcycle(void) {
    uint64_t v = 0;
    __asm__ volatile("csrr %0, mcycle" : "=r"(v));
    return v;
}

static inline uint64_t qemu_now_ps(void) {
    uint64_t cur = g_in_trap ? g_trap_entry_ticks : M_R64(QEMU_VIRT_CLINT_TIME);
    uint64_t total = (cur >= g_t0_ticks) ? (cur - g_t0_ticks) : 0ULL;
    uint64_t dt = (total >= g_bridge_ticks) ? (total - g_bridge_ticks) : 0ULL;
    /* QEMU virt CLINT timebase = 10 MHz => 100 ns = 100,000 ps per tick */
    return dt * 100000ULL + 1000ULL;
}

static void qemu_exit(uint32_t exit_code) {
    uint32_t code = (exit_code == 0U) ? 0x5555U : ((exit_code << 16) | 0x3333U);
    M_W32(QEMU_VIRT_FINISHER, code);
    for (;;) {
        __asm__ volatile("wfi");
    }
}

static void vio_setup_queue(uint32_t q, VirtqDesc *desc, VirtqAvail *avail, VirtqUsed *used) {
    M_W32(g_vio_base + VIO_QUEUE_SEL, q);
    M_W32(g_vio_base + VIO_QUEUE_NUM, VQ_SIZE);
    uint64_t d_pa = (uint64_t)(uintptr_t)desc;
    uint64_t a_pa = (uint64_t)(uintptr_t)avail;
    uint64_t u_pa = (uint64_t)(uintptr_t)used;
    M_W32(g_vio_base + VIO_QUEUE_DESC_LO, (uint32_t)(d_pa & 0xFFFFFFFFULL));
    M_W32(g_vio_base + VIO_QUEUE_DESC_HI, (uint32_t)(d_pa >> 32));
    M_W32(g_vio_base + VIO_QUEUE_AVAIL_LO, (uint32_t)(a_pa & 0xFFFFFFFFULL));
    M_W32(g_vio_base + VIO_QUEUE_AVAIL_HI, (uint32_t)(a_pa >> 32));
    M_W32(g_vio_base + VIO_QUEUE_USED_LO, (uint32_t)(u_pa & 0xFFFFFFFFULL));
    M_W32(g_vio_base + VIO_QUEUE_USED_HI, (uint32_t)(u_pa >> 32));
    mem_fence();
    M_W32(g_vio_base + VIO_QUEUE_READY, 1U);
}

static void vio_post_rx(uint32_t id) {
    g_rx_desc[id].addr = (uint64_t)(uintptr_t)g_rx_bufs[id];
    g_rx_desc[id].len = VQ_BUF_BYTES;
    g_rx_desc[id].flags = VRING_DESC_F_WRITE;
    g_rx_desc[id].next = 0U;
    g_rx_avail.ring[g_rx_avail.idx & (VQ_SIZE - 1U)] = (uint16_t)id;
    mem_fence();
    g_rx_avail.idx++;
    mem_fence();
    M_W32(g_vio_base + VIO_QUEUE_NOTIFY, 0U);
}

static int vio_init_console(void) {
    for (uint64_t a = QEMU_VIRT_MMIO_START; a <= QEMU_VIRT_MMIO_END; a += QEMU_VIRT_MMIO_STEP) {
        if (M_R32(a + VIO_MAGIC) == 0x74726976U && M_R32(a + VIO_DEVICE_ID) == 3U &&
            M_R32(a + VIO_VERSION) == 2U) {
            g_vio_base = a;
            break;
        }
    }
    if (g_vio_base == 0ULL) return -1;

    M_W32(g_vio_base + VIO_STATUS, 0U); /* reset */
    M_W32(g_vio_base + VIO_STATUS, 1U | 2U); /* ACKNOWLEDGE | DRIVER */
    /* Disable multiport/emerg-write so queue 0 = RX0, queue 1 = TX0 directly */
    M_W32(g_vio_base + VIO_DRV_FEAT_SEL, 0U);
    M_W32(g_vio_base + VIO_DRV_FEAT, 0U);
    M_W32(g_vio_base + VIO_DRV_FEAT_SEL, 1U);
    M_W32(g_vio_base + VIO_DRV_FEAT, 1U); /* VIRTIO_F_VERSION_1 */
    M_W32(g_vio_base + VIO_STATUS, 1U | 2U | 8U); /* FEATURES_OK */
    if ((M_R32(g_vio_base + VIO_STATUS) & 8U) == 0U) return -2;

    vio_setup_queue(0U, g_rx_desc, &g_rx_avail, &g_rx_used);
    vio_setup_queue(1U, g_tx_desc, &g_tx_avail, &g_tx_used);

    M_W32(g_vio_base + VIO_STATUS, 1U | 2U | 8U | 4U); /* DRIVER_OK */
    for (uint32_t i = 0U; i < VQ_SIZE; ++i) {
        vio_post_rx(i);
    }
    return 0;
}

static void vio_send_exact(const void *buf, uint32_t len) {
    const uint8_t *p = (const uint8_t *)buf;
    while (len > 0U) {
        uint32_t chunk = (len > VQ_BUF_BYTES) ? VQ_BUF_BYTES : len;
        b_memcpy(g_tx_buf, p, chunk);
        g_tx_desc[0].addr = (uint64_t)(uintptr_t)g_tx_buf;
        g_tx_desc[0].len = chunk;
        g_tx_desc[0].flags = 0U;
        g_tx_desc[0].next = 0U;
        g_tx_avail.ring[g_tx_avail.idx & (VQ_SIZE - 1U)] = 0U;
        mem_fence();
        g_tx_avail.idx++;
        mem_fence();
        M_W32(g_vio_base + VIO_QUEUE_NOTIFY, 1U);
        while (*(volatile uint16_t *)&g_tx_used.idx == g_tx_used_last) {
            uint32_t st = M_R32(g_vio_base + VIO_INTR_STATUS);
            if (st) M_W32(g_vio_base + VIO_INTR_ACK, st);
            mem_fence();
        }
        g_tx_used_last++;
        p += chunk;
        len -= chunk;
    }
}

static void vio_recv_exact(void *dst, uint32_t len) {
    uint8_t *out = (uint8_t *)dst;
    uint32_t done = 0U;
    while (done < len) {
        if (g_rx_has_cur && g_rx_cur_off < g_rx_cur_len) {
            uint32_t avail = g_rx_cur_len - g_rx_cur_off;
            uint32_t take = (len - done < avail) ? (len - done) : avail;
            b_memcpy(out + done, g_rx_bufs[g_rx_cur_id] + g_rx_cur_off, take);
            done += take;
            g_rx_cur_off += take;
            if (g_rx_cur_off >= g_rx_cur_len) {
                uint32_t finished_id = g_rx_cur_id;
                g_rx_has_cur = 0;
                g_rx_cur_off = 0U;
                g_rx_cur_len = 0U;
                vio_post_rx(finished_id);
            }
            continue;
        }
        while (*(volatile uint16_t *)&g_rx_used.idx == g_rx_used_last) {
            uint32_t st = M_R32(g_vio_base + VIO_INTR_STATUS);
            if (st) M_W32(g_vio_base + VIO_INTR_ACK, st);
            mem_fence();
        }
        mem_fence();
        VirtqUsedElem elem = g_rx_used.ring[g_rx_used_last & (VQ_SIZE - 1U)];
        g_rx_used_last++;
        if (elem.len == 0U) {
            vio_post_rx(elem.id);
        } else {
            g_rx_cur_id = elem.id;
            g_rx_cur_off = 0U;
            g_rx_cur_len = elem.len;
            g_rx_has_cur = 1;
        }
    }
}

static uint8_t g_tx_coalesce[USP_BUS_V2_HDR_SIZE + USP_BUS_V2_MAX_BURST_BYTES];
static int g_timer_armed = 0;

static void rearm_quantum_timer(void) {
    uint64_t now = M_R64(QEMU_VIRT_CLINT_TIME);
    M_W64(QEMU_VIRT_CLINT_CMP, now + g_quantum_ticks);
}

static UspBusV2Frame usp_rpc(uint16_t op, uint64_t addr, uint64_t data, uint32_t length,
                             uint32_t flags, const void *tx_payload, void *rx_payload) {
    UspBusV2Frame req;
    req.magic = USP_BUS_V2_MAGIC;
    req.seq = ++g_seq;
    req.op = op;
    req.status = 0U;
    req.length = length;
    req.irq_mask = 0U;
    req.flags = flags;
    req.addr = addr;
    req.data = data;
    req.time_ps = qemu_now_ps();

    if (tx_payload && length > 0U && length <= USP_BUS_V2_MAX_BURST_BYTES) {
        /* Coalesce header + payload into one single VirtIO TX buffer! */
        b_memcpy(g_tx_coalesce, &req, USP_BUS_V2_HDR_SIZE);
        b_memcpy(g_tx_coalesce + USP_BUS_V2_HDR_SIZE, tx_payload, length);
        vio_send_exact(g_tx_coalesce, USP_BUS_V2_HDR_SIZE + length);
    } else {
        vio_send_exact(&req, USP_BUS_V2_HDR_SIZE);
    }

    UspBusV2Frame res;
    vio_recv_exact(&res, USP_BUS_V2_HDR_SIZE);
    if ((res.flags & USP_FLAG_HAS_PAYLOAD) && res.length > 0U &&
        res.length <= USP_BUS_V2_MAX_BURST_BYTES) {
        if (rx_payload) {
            vio_recv_exact(rx_payload, res.length);
        } else {
            uint8_t discard[64];
            uint32_t rem = res.length;
            while (rem > 0U) {
                uint32_t c = rem > sizeof(discard) ? (uint32_t)sizeof(discard) : rem;
                vio_recv_exact(discard, c);
                rem -= c;
            }
        }
    }
    g_last_irq_mask = res.irq_mask;
    if (g_timer_armed) {
        rearm_quantum_timer();
    }
    return res;
}

static void sync_quantum_with_systemc(uint64_t q_ns) {
    uint64_t retired = guest_retired_instret();
    UspBusV2Frame res = usp_rpc(
        USP_OP_TIME_SYNC, retired, q_ns * 1000ULL, 0U, USP_FLAG_QUANTUM_BARRIER, NULL, NULL);
    if (res.status == USP_STATUS_HALTED) {
        qemu_exit((uint32_t)res.data);
    }
}

static void maybe_deliver_guest_ext_irq(UspQemuTrapFrame *tf) {
    if (g_last_irq_mask == 0U) return;
    if ((g_v_mstatus & 8ULL) == 0ULL) return;
    if ((g_v_mie & (1ULL << 11)) == 0ULL) return;
    if (g_v_mtvec == 0ULL) return;

    g_v_mepc = tf->mepc;
    g_v_mcause = 0x800000000000000bULL;
    g_v_mtval = 0ULL;
    uint64_t old_mie = (g_v_mstatus & 8ULL) ? 1ULL : 0ULL;
    g_v_mstatus = (g_v_mstatus & ~(1ULL << 7)) | (old_mie << 7);
    g_v_mstatus &= ~8ULL;
    tf->mepc = g_v_mtvec & ~3ULL;
}

static void inject_guest_sync_trap(UspQemuTrapFrame *tf, uint64_t cause, uint64_t tval) {
    if (g_v_mtvec == 0ULL) {
        qemu_exit(2U);
    }
    g_v_mepc = tf->mepc;
    g_v_mcause = cause;
    g_v_mtval = tval;
    uint64_t old_mie = (g_v_mstatus & 8ULL) ? 1ULL : 0ULL;
    g_v_mstatus = (g_v_mstatus & ~(1ULL << 7)) | (old_mie << 7);
    g_v_mstatus &= ~8ULL;
    tf->mepc = g_v_mtvec & ~3ULL;
}

/* Sv39 software page-table walker (pass-through when satp.mode == 0 bare) */
static uint64_t translate_guest_va(uint64_t va) {
    uint64_t satp = 0ULL;
    __asm__ volatile("csrr %0, satp" : "=r"(satp));
    if ((satp >> 60) != 8ULL) return va; /* Bare mode */
    uint64_t ppn = satp & 0xFFFFFFFFFFFULL;
    uint64_t table = ppn << 12;
    for (int level = 2; level >= 0; --level) {
        uint64_t vpn = (va >> (12 + level * 9)) & 0x1FFULL;
        uint64_t pte = M_R64(table + vpn * 8ULL);
        if ((pte & 1ULL) == 0ULL) return va;
        if (pte & 0xEULL) {
            uint64_t pte_ppn = (pte >> 10) & 0xFFFFFFFFFFFULL;
            if (level == 2) return ((pte_ppn & ~0x3FFFFULL) << 12) | (va & 0x3FFFFFFFULL);
            if (level == 1) return ((pte_ppn & ~0x1FFULL) << 12) | (va & 0x1FFFFFULL);
            return (pte_ppn << 12) | (va & 0xFFFULL);
        }
        table = ((pte >> 10) & 0xFFFFFFFFFFFULL) << 12;
    }
    return va;
}

static int64_t sign_ext(uint64_t val, unsigned bits) {
    uint64_t shift = 64U - bits;
    return (int64_t)(val << shift) >> shift;
}

static uint64_t read_v_csr(uint32_t csr) {
    switch (csr) {
        case 0x001U: return g_v_fcsr & 0x1FULL;
        case 0x002U: return (g_v_fcsr >> 5) & 7ULL;
        case 0x003U: return g_v_fcsr;
        case 0x300U: return g_v_mstatus;
        case 0x301U: return 0x800000000014112DULL; /* RV64IMAFD + Vector/RoCC */
        case 0x304U: return g_v_mie;
        case 0x305U: return g_v_mtvec;
        case 0x340U: return g_v_mscratch;
        case 0x341U: return g_v_mepc;
        case 0x342U: return g_v_mcause;
        case 0x343U: return g_v_mtval;
        case 0x344U: return g_last_irq_mask ? (1ULL << 11) : 0ULL;
        case 0x3A0U: return g_v_pmpcfg0;
        case 0x3B0U: return g_v_pmpaddr[0];
        case 0x3B1U: return g_v_pmpaddr[1];
        case 0xB00U: return read_mcycle();
        case 0xB02U: return guest_retired_instret();
        case 0xF14U: return 0ULL; /* hart 0 */
        default: return 0ULL;
    }
}

static void write_v_csr(uint32_t csr, uint64_t val) {
    switch (csr) {
        case 0x001U: g_v_fcsr = (g_v_fcsr & ~0x1FULL) | (val & 0x1FULL); break;
        case 0x002U: g_v_fcsr = (g_v_fcsr & ~0xE0ULL) | ((val & 7ULL) << 5); break;
        case 0x003U: g_v_fcsr = val & 0xFFULL; break;
        case 0x300U: g_v_mstatus = val; break;
        case 0x304U: g_v_mie = val; break;
        case 0x305U: g_v_mtvec = val; break;
        case 0x340U: g_v_mscratch = val; break;
        case 0x341U: g_v_mepc = val; break;
        case 0x342U: g_v_mcause = val; break;
        case 0x343U: g_v_mtval = val; break;
        case 0x3A0U: g_v_pmpcfg0 = val; break;
        case 0x3B0U: g_v_pmpaddr[0] = val; break;
        case 0x3B1U: g_v_pmpaddr[1] = val; break;
        default: break;
    }
}

static void handle_mmio_fault(UspQemuTrapFrame *tf, uint64_t mcause, uint64_t mtval) {
    uint64_t pc_pa = translate_guest_va(tf->mepc);
    uint64_t addr_pa = translate_guest_va(mtval);
    uint16_t hw0 = *(volatile uint16_t *)(uintptr_t)pc_pa;
    uint32_t insn_len = ((hw0 & 3U) == 3U) ? 4U : 2U;
    uint32_t insn = hw0;
    if (insn_len == 4U) {
        uint16_t hw1 = *(volatile uint16_t *)(uintptr_t)(pc_pa + 2ULL);
        insn |= ((uint32_t)hw1 << 16);
    }

    int is_write = 0;
    int is_signed = 0;
    int is_amo = 0;
    uint32_t len = 4U;
    uint32_t rd = 0U;
    uint64_t wdata = 0ULL;
    uint32_t amo_funct5 = 0U;

    if (insn_len == 4U) {
        uint32_t op = insn & 0x7FU;
        uint32_t f3 = (insn >> 12) & 7U;
        rd = (insn >> 7) & 31U;
        uint32_t rs2 = (insn >> 20) & 31U;
        if (op == 0x03U) {
            is_write = 0;
            len = 1U << (f3 & 3U);
            is_signed = (f3 < 3U);
        } else if (op == 0x23U) {
            is_write = 1;
            len = 1U << (f3 & 3U);
            wdata = tf->x[rs2];
        } else if (op == 0x2FU) {
            is_amo = 1;
            len = (f3 == 3U) ? 8U : 4U;
            wdata = tf->x[rs2];
            amo_funct5 = (insn >> 27) & 31U;
        } else {
            inject_guest_sync_trap(tf, mcause, mtval);
            return;
        }
    } else {
        uint32_t quad = hw0 & 3U;
        uint32_t f3 = (hw0 >> 13) & 7U;
        if (quad == 0U) {
            uint32_t rp = 8U + ((hw0 >> 2) & 7U);
            if (f3 == 2U) { is_write = 0; len = 4U; is_signed = 1; rd = rp; }
            else if (f3 == 3U) { is_write = 0; len = 8U; is_signed = 0; rd = rp; }
            else if (f3 == 6U) { is_write = 1; len = 4U; wdata = tf->x[rp]; }
            else if (f3 == 7U) { is_write = 1; len = 8U; wdata = tf->x[rp]; }
            else { inject_guest_sync_trap(tf, mcause, mtval); return; }
        } else if (quad == 2U) {
            if (f3 == 2U) { is_write = 0; len = 4U; is_signed = 1; rd = (hw0 >> 7) & 31U; }
            else if (f3 == 3U) { is_write = 0; len = 8U; is_signed = 0; rd = (hw0 >> 7) & 31U; }
            else if (f3 == 6U) { is_write = 1; len = 4U; wdata = tf->x[(hw0 >> 2) & 31U]; }
            else if (f3 == 7U) { is_write = 1; len = 8U; wdata = tf->x[(hw0 >> 2) & 31U]; }
            else { inject_guest_sync_trap(tf, mcause, mtval); return; }
        } else {
            inject_guest_sync_trap(tf, mcause, mtval);
            return;
        }
    }

    if (is_amo) {
        UspBusV2Frame rres = usp_rpc(USP_OP_READ, addr_pa, 0ULL, len, 0U, NULL, NULL);
        if (rres.status != USP_STATUS_OK) {
            inject_guest_sync_trap(tf, 5ULL, mtval);
            return;
        }
        uint64_t orig = (len == 4U) ? (uint64_t)sign_ext(rres.data, 32U) : rres.data;
        if (amo_funct5 == 0x02U) { /* LR */
            if (rd != 0U) tf->x[rd] = orig;
        } else {
            uint64_t src = (len == 4U) ? (uint64_t)sign_ext(wdata, 32U) : wdata;
            uint64_t nw = src;
            if (amo_funct5 == 0x00U) nw = orig + src;
            else if (amo_funct5 == 0x04U) nw = orig ^ src;
            else if (amo_funct5 == 0x0CU) nw = orig & src;
            else if (amo_funct5 == 0x08U) nw = orig | src;
            UspBusV2Frame wres = usp_rpc(USP_OP_WRITE, addr_pa, nw, len, 0U, NULL, NULL);
            if (wres.status == USP_STATUS_HALTED) {
                sync_quantum_with_systemc(g_quantum_ns);
                qemu_exit((uint32_t)wres.data);
            }
            if (wres.status != USP_STATUS_OK) {
                inject_guest_sync_trap(tf, 7ULL, mtval);
                return;
            }
            if (rd != 0U) tf->x[rd] = (amo_funct5 == 0x03U) ? 0ULL : orig;
        }
        tf->mepc += insn_len;
        maybe_deliver_guest_ext_irq(tf);
        return;
    }

    UspBusV2Frame res = usp_rpc(
        is_write ? USP_OP_WRITE : USP_OP_READ, addr_pa, is_write ? wdata : 0ULL, len, 0U, NULL, NULL);
    if (res.status == USP_STATUS_HALTED) {
        uint32_t code = (uint32_t)res.data;
        /* Report exact final QEMU guest retired instruction count before closing QEMU */
        uint64_t retired = guest_retired_instret();
        (void)usp_rpc(USP_OP_TIME_SYNC, retired, g_quantum_ns * 1000ULL, 0U,
                      USP_FLAG_QUANTUM_BARRIER, NULL, NULL);
        qemu_exit(code);
    }
    if (res.status != USP_STATUS_OK) {
        inject_guest_sync_trap(tf, mcause, mtval);
        return;
    }

    if (!is_write && rd != 0U) {
        uint64_t v = res.data;
        if (len == 1U) v = is_signed ? (uint64_t)sign_ext(v, 8U) : (v & 0xFFULL);
        else if (len == 2U) v = is_signed ? (uint64_t)sign_ext(v, 16U) : (v & 0xFFFFULL);
        else if (len == 4U) v = is_signed ? (uint64_t)sign_ext(v, 32U) : (v & 0xFFFFFFFFULL);
        tf->x[rd] = v;
    }
    tf->mepc += insn_len;
    maybe_deliver_guest_ext_irq(tf);
}

static void handle_illegal_insn(UspQemuTrapFrame *tf, uint64_t mtval) {
    uint64_t pc_pa = translate_guest_va(tf->mepc);
    uint16_t hw0 = *(volatile uint16_t *)(uintptr_t)pc_pa;
    if ((hw0 & 3U) != 3U) {
        inject_guest_sync_trap(tf, 2ULL, mtval);
        return;
    }
    uint16_t hw1 = *(volatile uint16_t *)(uintptr_t)(pc_pa + 2ULL);
    uint32_t insn = (uint32_t)hw0 | ((uint32_t)hw1 << 16);
    if ((insn & 0x7FU) != 0x73U) {
        inject_guest_sync_trap(tf, 2ULL, insn);
        return;
    }
    uint32_t f3 = (insn >> 12) & 7U;
    if (f3 == 0U) {
        if (insn == 0x30200073U) { /* MRET */
            uint64_t mpie = (g_v_mstatus & (1ULL << 7)) ? 1ULL : 0ULL;
            g_v_mstatus = (g_v_mstatus & ~8ULL) | (mpie << 3) | (1ULL << 7);
            tf->mepc = g_v_mepc;
            if (mpie && (g_v_mie & (1ULL << 11))) {
                /* Poll fresh SystemC PLIC level so back-to-back IRQs never wait */
                sync_quantum_with_systemc(g_quantum_ns);
                maybe_deliver_guest_ext_irq(tf);
            }
            return;
        }
        if (insn == 0x10500073U) { /* WFI */
            tf->mepc += 4ULL;
            sync_quantum_with_systemc(g_quantum_ns);
            maybe_deliver_guest_ext_irq(tf);
            return;
        }
        inject_guest_sync_trap(tf, 2ULL, insn);
        return;
    }

    uint32_t rd = (insn >> 7) & 31U;
    uint32_t rs1 = (insn >> 15) & 31U;
    uint32_t csr = (insn >> 20) & 0xFFFU;
    uint64_t old = read_v_csr(csr);
    uint64_t src = (f3 & 4U) ? (uint64_t)rs1 : tf->x[rs1];
    switch (f3 & 3U) {
        case 1U: write_v_csr(csr, src); break;
        case 2U: if (rs1 != 0U) write_v_csr(csr, old | src); break;
        case 3U: if (rs1 != 0U) write_v_csr(csr, old & ~src); break;
        default: break;
    }
    if (rd != 0U) tf->x[rd] = old;
    tf->mepc += 4ULL;
    maybe_deliver_guest_ext_irq(tf);
}

static void handle_ecall(UspQemuTrapFrame *tf) {
    if (tf->x[17] != USP_HC_MAGIC) {
        inject_guest_sync_trap(tf, 9ULL, 0ULL);
        return;
    }
    uint64_t hc = tf->x[10];
    uint64_t a1 = tf->x[11];
    uint64_t a2 = tf->x[12];
    uint32_t a3 = (uint32_t)tf->x[13];
    if (hc == USP_HC_BURST_WRITE) {
        if (a3 == 0U || (a3 & 7U) != 0U || a3 > USP_BUS_V2_MAX_BURST_BYTES) {
            tf->x[10] = (uint64_t)-1;
        } else {
            uint64_t src_pa = translate_guest_va(a2);
            UspBusV2Frame res = usp_rpc(
                USP_OP_BURST_WRITE, a1, 0ULL, a3,
                USP_FLAG_HAS_PAYLOAD | USP_FLAG_BURST64,
                (const void *)(uintptr_t)src_pa, NULL);
            tf->x[10] = (res.status == USP_STATUS_OK) ? 0ULL : (uint64_t)(-(int64_t)res.status);
        }
    } else if (hc == USP_HC_BURST_READ) {
        if (a3 == 0U || (a3 & 7U) != 0U || a3 > USP_BUS_V2_MAX_BURST_BYTES) {
            tf->x[10] = (uint64_t)-1;
        } else {
            uint64_t dst_pa = translate_guest_va(a2);
            UspBusV2Frame res = usp_rpc(
                USP_OP_BURST_READ, a1, 0ULL, a3, USP_FLAG_BURST64,
                NULL, (void *)(uintptr_t)dst_pa);
            tf->x[10] = (res.status == USP_STATUS_OK) ? 0ULL : (uint64_t)(-(int64_t)res.status);
        }
    } else if (hc == USP_HC_TIME_SYNC) {
        uint64_t q = (a1 > 0ULL) ? a1 : g_quantum_ns;
        uint64_t retired = guest_retired_instret();
        UspBusV2Frame res = usp_rpc(
            USP_OP_TIME_SYNC, retired, q * 1000ULL, 0U, USP_FLAG_QUANTUM_BARRIER, NULL, NULL);
        tf->x[10] = res.time_ps;
    } else if (hc == USP_HC_POLL_IRQ) {
        sync_quantum_with_systemc(g_quantum_ns);
        tf->x[10] = (uint64_t)g_last_irq_mask;
    } else {
        tf->x[10] = (uint64_t)-1;
    }
    tf->mepc += 4ULL;
    maybe_deliver_guest_ext_irq(tf);
}

void usp_qemu_trap_handler(UspQemuTrapFrame *tf, uint64_t mcause, uint64_t mtval) {
    uint64_t trap_in = read_minstret();
    uint64_t tick_in = M_R64(QEMU_VIRT_CLINT_TIME);
    g_trap_entry_instret = trap_in;
    g_trap_entry_ticks = tick_in;
    g_in_trap = 1;
    tf->x[0] = 0ULL;
    if (mcause == 0x8000000000000007ULL) {
        /* Periodic M-mode quantum time-sync timer interrupt */
        sync_quantum_with_systemc(g_quantum_ns);
        rearm_quantum_timer();
        maybe_deliver_guest_ext_irq(tf);
    } else if (mcause == 5ULL || mcause == 7ULL) {
        handle_mmio_fault(tf, mcause, mtval);
    } else if (mcause == 2ULL) {
        handle_illegal_insn(tf, mtval);
    } else if (mcause == 9ULL) {
        handle_ecall(tf);
    } else {
        inject_guest_sync_trap(tf, mcause, mtval);
    }
    g_in_trap = 0;
    uint64_t trap_out = read_minstret();
    uint64_t tick_out = M_R64(QEMU_VIRT_CLINT_TIME);
    if (trap_out > trap_in) {
        g_bridge_instret += (trap_out - trap_in);
    }
    if (tick_out > tick_in) {
        g_bridge_ticks += (tick_out - tick_in);
    }
}

void usp_qemu_bridge_init_and_enter(void) {
    if (vio_init_console() != 0) {
        qemu_exit(11U);
    }
    g_t0_ticks = M_R64(QEMU_VIRT_CLINT_TIME);

    /* USP_OP_RESET capability handshake with SystemC QemuCpuMaster */
    UspBusV2Frame init_res = usp_rpc(
        USP_OP_RESET, SOC_MAIN_RAM_BASE, USP_CAP_ALL, 0U, 0U, NULL, NULL);
    if (init_res.status != USP_STATUS_OK) {
        qemu_exit(12U);
    }
    if (init_res.time_ps > 0ULL) {
        /* QemuCpuMaster returns configured quantum_ps in init_res.time_ps */
        g_quantum_ns = init_res.time_ps / 1000ULL;
        if (g_quantum_ns < 1000ULL) g_quantum_ns = 1000ULL;
    }
    /* Map quantum_ns to QEMU 10 MHz CLINT ticks (1 tick = 100 ns, floor 10 ticks) */
    g_quantum_ticks = g_quantum_ns / 100ULL;
    if (g_quantum_ticks < 10ULL) g_quantum_ticks = 10ULL;

    /* Configure 3-entry TOR PMP:
     *   entry 0: [0, SOC_MAIN_RAM_BASE)                           -> NO RWX (traps MMIO to SystemC)
     *   entry 1: [SOC_MAIN_RAM_BASE, SOC_MAIN_RAM_BASE + SIZE)    -> R|W|X  (native QEMU RAM speed)
     *   entry 2: [SOC_MAIN_RAM_BASE + SIZE, max)                  -> NO RWX (protects M-mode bridge)
     */
    uint64_t pmp0 = SOC_MAIN_RAM_BASE >> 2;
    uint64_t pmp1 = (SOC_MAIN_RAM_BASE + SOC_MAIN_RAM_SIZE) >> 2;
    uint64_t pmp2 = 0x003FFFFFFFFFFFFFULL;
    uint64_t cfg0 = (0x08ULL << 0) | (0x0FULL << 8) | (0x08ULL << 16);
    __asm__ volatile("csrw pmpaddr0, %0" : : "r"(pmp0));
    __asm__ volatile("csrw pmpaddr1, %0" : : "r"(pmp1));
    __asm__ volatile("csrw pmpaddr2, %0" : : "r"(pmp2));
    __asm__ volatile("csrw pmpcfg0, %0" : : "r"(cfg0));

    /* Allow guest S-mode to read cycle/time/instret directly without trap overhead */
    __asm__ volatile("csrw mcounteren, %0" : : "r"(7ULL));
    __asm__ volatile("csrw medeleg, zero");
    __asm__ volatile("csrw mideleg, zero");
    __asm__ volatile("csrw mtvec, %0" : : "r"((uint64_t)(uintptr_t)_usp_m_trap_entry));

    /* Arm periodic M-mode quantum sync timer */
    g_timer_armed = 1;
    rearm_quantum_timer();
    __asm__ volatile("csrw mie, %0" : : "r"(1ULL << 7)); /* MTIE */

    /* Drop into S-mode (MPP = 1, FS = Dirty, MPIE = 1, TW = 1 so WFI traps to quantum sync) */
    uint64_t mstatus = (1ULL << 11) | (3ULL << 13) | (1ULL << 7) | (1ULL << 21);
    __asm__ volatile("csrw mstatus, %0" : : "r"(mstatus));
    __asm__ volatile("csrw mepc, %0" : : "r"((uint64_t)(uintptr_t)_start));
    g_t0_ticks = M_R64(QEMU_VIRT_CLINT_TIME);
    g_instret_base = read_minstret();
    __asm__ volatile("sfence.vma" ::: "memory");
    __asm__ volatile("mret");
    for (;;) {}
}
