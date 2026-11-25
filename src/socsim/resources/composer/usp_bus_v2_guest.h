/* SPDX-License-Identifier: Apache-2.0
 * Guest C helper header for `usp-bus-v2` 64-bit bursts and quantum time synchronization.
 * Works seamlessly on BOTH CPU Masters:
 *   - `QemuCpuMaster` (real qemu-system-riscv64 TCG/KVM): invokes M-mode bridge hypercalls
 *     (`ecall` with a7 = USP_HC_MAGIC) to emit single-frame 64-bit bursts (`USP_OP_BURST_READ` /
 *     `USP_OP_BURST_WRITE`) and `USP_OP_TIME_SYNC` quantum barriers over AF_UNIX to SystemC.
 *   - `Rv64CpuMaster` (SystemC interpreter): falls back transparently to direct 64-bit TLM
 *     loads/stores and CLINT mtime reads when USP_QEMU_MASTER is not defined.
 */
#ifndef USP_BUS_V2_GUEST_H
#define USP_BUS_V2_GUEST_H

#include <stdint.h>
#include "soc_map.h"
#include "usp_bus_protocol.hpp"

static inline int usp_bus_burst_write64(uint64_t target_addr, const uint64_t *src,
                                        uint32_t len_bytes) {
#ifdef USP_QEMU_CPU_MASTER
    register uint64_t a0 __asm__("a0") = USP_HC_BURST_WRITE;
    register uint64_t a1 __asm__("a1") = target_addr;
    register uint64_t a2 __asm__("a2") = (uint64_t)(uintptr_t)src;
    register uint64_t a3 __asm__("a3") = (uint64_t)len_bytes;
    register uint64_t a7 __asm__("a7") = USP_HC_MAGIC;
    __asm__ volatile("ecall"
                     : "+r"(a0)
                     : "r"(a1), "r"(a2), "r"(a3), "r"(a7)
                     : "memory");
    return (int)a0;
#else
    uint32_t beats = len_bytes / 8U;
    for (uint32_t i = 0U; i < beats; ++i) {
        *(volatile uint64_t *)(uintptr_t)(target_addr + (uint64_t)i * 8ULL) = src[i];
    }
    return 0;
#endif
}

static inline int usp_bus_burst_read64(uint64_t target_addr, uint64_t *dst,
                                       uint32_t len_bytes) {
#ifdef USP_QEMU_CPU_MASTER
    register uint64_t a0 __asm__("a0") = USP_HC_BURST_READ;
    register uint64_t a1 __asm__("a1") = target_addr;
    register uint64_t a2 __asm__("a2") = (uint64_t)(uintptr_t)dst;
    register uint64_t a3 __asm__("a3") = (uint64_t)len_bytes;
    register uint64_t a7 __asm__("a7") = USP_HC_MAGIC;
    __asm__ volatile("ecall"
                     : "+r"(a0)
                     : "r"(a1), "r"(a2), "r"(a3), "r"(a7)
                     : "memory");
    return (int)a0;
#else
    uint32_t beats = len_bytes / 8U;
    for (uint32_t i = 0U; i < beats; ++i) {
        dst[i] = *(volatile uint64_t *)(uintptr_t)(target_addr + (uint64_t)i * 8ULL);
    }
    return 0;
#endif
}

static inline uint64_t usp_bus_time_sync(uint64_t quantum_ns) {
#ifdef USP_QEMU_CPU_MASTER
    register uint64_t a0 __asm__("a0") = USP_HC_TIME_SYNC;
    register uint64_t a1 __asm__("a1") = quantum_ns;
    register uint64_t a7 __asm__("a7") = USP_HC_MAGIC;
    __asm__ volatile("ecall"
                     : "+r"(a0)
                     : "r"(a1), "r"(a7)
                     : "memory");
    return a0;
#else
    (void)quantum_ns;
    return (*(volatile uint64_t *)(uintptr_t)(SOC_CLINT_BASE + 0xBFF8ULL)) * 100000ULL;
#endif
}

static inline uint32_t usp_bus_poll_irq(void) {
#ifdef USP_QEMU_CPU_MASTER
    register uint64_t a0 __asm__("a0") = USP_HC_POLL_IRQ;
    register uint64_t a7 __asm__("a7") = USP_HC_MAGIC;
    __asm__ volatile("ecall"
                     : "+r"(a0)
                     : "r"(a7)
                     : "memory");
    return (uint32_t)a0;
#else
    return 0U;
#endif
}

#endif /* USP_BUS_V2_GUEST_H */
