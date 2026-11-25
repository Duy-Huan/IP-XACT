/* SPDX-License-Identifier: Apache-2.0
 * Universal SoC Bus Protocol Header (`usp-mmio32-v1` + `usp-bus-v2`).
 * Shared C11 / C++17 wire contract across QEMU, SystemC/TLM-2.0, gem5, and out-of-process IP plugins.
 *
 * Wire compatibility:
 *   - `usp-mmio32-v1`: 20-byte fixed frame starting with magic 0x31505355 ("USP1")
 *   - `usp-bus-v2`   : 48-byte fixed header starting with magic 0x32425355 ("USB2"),
 *                      optionally followed by `length` payload bytes for 64-bit bursts and
 *                      reverse bus-master DMA reads/writes.
 */
#ifndef USP_BUS_PROTOCOL_HPP
#define USP_BUS_PROTOCOL_HPP

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define USP_MMIO32_V1_MAGIC 0x31505355u /* "USP1" */
#define USP_BUS_V2_MAGIC    0x32425355u /* "USB2" */
#define USP_BUS_V2_HDR_SIZE 48u
#define USP_BUS_V2_MAX_BURST_BYTES 4096u

/* Opcodes (responses OR with USP_OP_RESPONSE = 0x8000) */
#define USP_OP_READ        0x0000u
#define USP_OP_WRITE       0x0001u
#define USP_OP_RESET       0x0002u
#define USP_OP_BURST_READ  0x0003u
#define USP_OP_BURST_WRITE 0x0004u
#define USP_OP_TIME_SYNC   0x0005u
#define USP_OP_DMA_READ    0x0010u
#define USP_OP_DMA_WRITE   0x0011u
#define USP_OP_IRQ_NOTIFY  0x0012u
#define USP_OP_RESPONSE    0x8000u

/* Status codes */
#define USP_STATUS_OK      0x0000u
#define USP_STATUS_BUS_ERR 0x0001u
#define USP_STATUS_CMD_ERR 0x0002u
#define USP_STATUS_DENIED  0x0003u
#define USP_STATUS_TIMEOUT 0x0004u
#define USP_STATUS_HALTED  0x0005u

/* Frame flags */
#define USP_FLAG_HAS_PAYLOAD     (1u << 0)
#define USP_FLAG_BURST64         (1u << 1)
#define USP_FLAG_COHERENT_SNOOP  (1u << 2)
#define USP_FLAG_QUANTUM_BARRIER (1u << 3)

/* Capability bits exchanged on USP_OP_RESET */
#define USP_CAP_MMIO32       (1ULL << 0)
#define USP_CAP_MMIO64       (1ULL << 1)
#define USP_CAP_BURST64      (1ULL << 2)
#define USP_CAP_DMA_MASTER   (1ULL << 3)
#define USP_CAP_QUANTUM_SYNC (1ULL << 4)
#define USP_CAP_V1_COMPAT    (1ULL << 5)
#define USP_CAP_ALL          (USP_CAP_MMIO32 | USP_CAP_MMIO64 | USP_CAP_BURST64 | \
                              USP_CAP_DMA_MASTER | USP_CAP_QUANTUM_SYNC | USP_CAP_V1_COMPAT)

/* Guest hypercall ABI (ecall with a7 = USP_HC_MAGIC) */
#define USP_HC_MAGIC       0x55535032ULL /* "USP2" */
#define USP_HC_BURST_READ  1ULL
#define USP_HC_BURST_WRITE 2ULL
#define USP_HC_TIME_SYNC   3ULL
#define USP_HC_POLL_IRQ    4ULL

typedef struct UspBusV2Frame {
    uint32_t magic;    /* 0x32425355 ("USB2") */
    uint32_t seq;      /* Monotonic request/response sequence number */
    uint16_t op;       /* USP_OP_* (response sets bit 15) */
    uint16_t status;   /* USP_STATUS_* */
    uint32_t length;   /* Transfer byte count (1/2/4/8 scalar, N*8 <= 4096 burst/DMA) */
    uint32_t irq_mask; /* Active IRQ level bitmask */
    uint32_t flags;    /* USP_FLAG_* */
    uint64_t addr;     /* 64-bit physical address / offset / telemetry */
    uint64_t data;     /* 64-bit scalar data / capabilities / quantum_ps / exit_code */
    uint64_t time_ps;  /* Synchronized simulation timestamp in picoseconds */
} UspBusV2Frame;

#ifdef __cplusplus
static_assert(sizeof(UspBusV2Frame) == USP_BUS_V2_HDR_SIZE, "UspBusV2Frame must be 48 bytes");
} /* extern "C" */
#else
_Static_assert(sizeof(UspBusV2Frame) == USP_BUS_V2_HDR_SIZE, "UspBusV2Frame must be 48 bytes");
#endif

#endif /* USP_BUS_PROTOCOL_HPP */
