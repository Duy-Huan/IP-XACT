/* SPDX-License-Identifier: Apache-2.0 */
/* RV64 bare-metal startup and M-mode trap trampoline for Unified_Hetero_MultiPlatform_SoC. */
.section .text.init, "ax"
.globl _start
.globl trap_entry

_start:
    la sp, _stack_top
    /* Zero .bss */
    la t0, _bss_start
    la t1, _bss_end
1:
    bgeu t0, t1, 2f
    sd zero, 0(t0)
    addi t0, t0, 8
    j 1b
2:
    la t0, trap_entry
    csrw mtvec, t0
    call main
3:
    wfi
    j 3b

.balign 4
trap_entry:
    addi sp, sp, -144
    sd ra,   0(sp)
    sd t0,   8(sp)
    sd t1,  16(sp)
    sd t2,  24(sp)
    sd t3,  32(sp)
    sd t4,  40(sp)
    sd t5,  48(sp)
    sd t6,  56(sp)
    sd a0,  64(sp)
    sd a1,  72(sp)
    sd a2,  80(sp)
    sd a3,  88(sp)
    sd a4,  96(sp)
    sd a5, 104(sp)
    sd a6, 112(sp)
    sd a7, 120(sp)

    csrr a0, mcause
    csrr a1, mepc
    csrr a2, mtval
    call trap_dispatch
    csrw mepc, a0

    ld ra,   0(sp)
    ld t0,   8(sp)
    ld t1,  16(sp)
    ld t2,  24(sp)
    ld t3,  32(sp)
    ld t4,  40(sp)
    ld t5,  48(sp)
    ld t6,  56(sp)
    ld a0,  64(sp)
    ld a1,  72(sp)
    ld a2,  80(sp)
    ld a3,  88(sp)
    ld a4,  96(sp)
    ld a5, 104(sp)
    ld a6, 112(sp)
    ld a7, 120(sp)
    addi sp, sp, 144
    mret
