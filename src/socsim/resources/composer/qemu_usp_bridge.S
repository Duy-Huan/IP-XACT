/* SPDX-License-Identifier: Apache-2.0
 * QEMU M-mode PMP + VirtIO-MMIO `usp-bus-v2` Boot & Trap Trampoline.
 * Runs in M-mode inside dedicated monitor RAM above guest RAM (protected via PMP).
 * Drops unmodified guest firmware into S-mode at `_start` (SOC_CPU_ENTRY) where RAM
 * runs at native QEMU TCG/KVM speed and all SoC MMIO traps cleanly to SystemC!
 */
.section .usp_bridge.init, "ax"
.globl _usp_qemu_entry
.globl _usp_m_trap_entry

_usp_qemu_entry:
    /* Secondary harts park in M-mode WFI */
    csrr t0, mhartid
    bnez t0, .Lpark

    /* Set up dedicated M-mode monitor stack and mscratch */
    la sp, _usp_bridge_stack_top
    csrw mscratch, sp

    /* Zero bridge .bss */
    la t0, _usp_bridge_bss_start
    la t1, _usp_bridge_bss_end
.Lbss_loop:
    bgeu t0, t1, .Lbss_done
    sd zero, 0(t0)
    addi t0, t0, 8
    j .Lbss_loop
.Lbss_done:
    call usp_qemu_bridge_init_and_enter

.Lpark:
    wfi
    j .Lpark

.section .usp_bridge.text, "ax"
.balign 4
_usp_m_trap_entry:
    /* Atomically swap guest sp <-> M-mode stack pointer in mscratch */
    csrrw sp, mscratch, sp
    addi sp, sp, -272

    sd zero,  0(sp)
    sd x1,    8(sp)
    /* x2 (guest sp) saved from mscratch below */
    sd x3,   24(sp)
    sd x4,   32(sp)
    sd x5,   40(sp)
    sd x6,   48(sp)
    sd x7,   56(sp)
    sd x8,   64(sp)
    sd x9,   72(sp)
    sd x10,  80(sp)
    sd x11,  88(sp)
    sd x12,  96(sp)
    sd x13, 104(sp)
    sd x14, 112(sp)
    sd x15, 120(sp)
    sd x16, 128(sp)
    sd x17, 136(sp)
    sd x18, 144(sp)
    sd x19, 152(sp)
    sd x20, 160(sp)
    sd x21, 168(sp)
    sd x22, 176(sp)
    sd x23, 184(sp)
    sd x24, 192(sp)
    sd x25, 200(sp)
    sd x26, 208(sp)
    sd x27, 216(sp)
    sd x28, 224(sp)
    sd x29, 232(sp)
    sd x30, 240(sp)
    sd x31, 248(sp)

    csrr t0, mscratch
    sd t0, 16(sp)
    csrr t1, mepc
    sd t1, 256(sp)
    csrr t2, mstatus
    sd t2, 264(sp)

    /* Restore mscratch to top of M-mode stack while in C handler */
    addi t0, sp, 272
    csrw mscratch, t0

    mv a0, sp
    csrr a1, mcause
    csrr a2, mtval
    call usp_qemu_trap_handler

    /* Apply updated mepc & mstatus */
    ld t1, 256(sp)
    csrw mepc, t1
    ld t2, 264(sp)
    csrw mstatus, t2

    /* Put guest sp back into mscratch for final swap */
    ld t0, 16(sp)
    csrw mscratch, t0

    ld x1,    8(sp)
    ld x3,   24(sp)
    ld x4,   32(sp)
    ld x5,   40(sp)
    ld x6,   48(sp)
    ld x7,   56(sp)
    ld x8,   64(sp)
    ld x9,   72(sp)
    ld x10,  80(sp)
    ld x11,  88(sp)
    ld x12,  96(sp)
    ld x13, 104(sp)
    ld x14, 112(sp)
    ld x15, 120(sp)
    ld x16, 128(sp)
    ld x17, 136(sp)
    ld x18, 144(sp)
    ld x19, 152(sp)
    ld x20, 160(sp)
    ld x21, 168(sp)
    ld x22, 176(sp)
    ld x23, 184(sp)
    ld x24, 192(sp)
    ld x25, 200(sp)
    ld x26, 208(sp)
    ld x27, 216(sp)
    ld x28, 224(sp)
    ld x29, 232(sp)
    ld x30, 240(sp)
    ld x31, 248(sp)

    addi sp, sp, 272
    csrrw sp, mscratch, sp
    mret
