/* SPDX-License-Identifier: Apache-2.0
 * QEMU M-mode PMP + VirtIO-MMIO v2 `usp-bus-v2` Bridge TrapFrame & Entry Declarations.
 */
#ifndef USP_QEMU_BRIDGE_H
#define USP_QEMU_BRIDGE_H

#include <stdint.h>

typedef struct UspQemuTrapFrame {
    uint64_t x[32]; /* x[0] always 0, x[1..31] guest GPRs (x[2] = guest sp) */
    uint64_t mepc;
    uint64_t mstatus;
} UspQemuTrapFrame;

void usp_qemu_bridge_init_and_enter(void);
void usp_qemu_trap_handler(UspQemuTrapFrame *tf, uint64_t mcause, uint64_t mtval);

#endif /* USP_QEMU_BRIDGE_H */
