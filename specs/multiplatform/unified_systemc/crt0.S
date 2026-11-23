/* SPDX-License-Identifier: Apache-2.0 */
/* Multi-Cluster / SMP RV64 bare-metal startup and M-mode trap trampoline.
 * Supports both 2-hart SMP (Unified_Hetero_MultiPlatform_SoC) and 4-hart 2-cluster
 * SMP (ASoC: Cluster 0 = hart0..1, Cluster 1 = hart2..3).
 */
.section .text.init, "ax"
.globl _start
.globl trap_entry
.weak hart1_main
.weak hart2_main
.weak hart3_main

_start:
    csrr t0, mhartid
    li t1, 1
    beq t0, t1, hart1_boot
    li t1, 2
    beq t0, t1, hart2_boot
    li t1, 3
    beq t0, t1, hart3_boot

    /* Hart 0 (Cluster 0 Supervisor): stack + zero .bss + trap vector + main */
    la sp, _stack_top
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

hart1_boot:
    /* Hart 1 (Cluster 0 Worker): dedicated stack + trap vector + weak hart1_main */
    la sp, _hart1_stack_top
    la t0, trap_entry
    csrw mtvec, t0
    call hart1_main
4:
    wfi
    j 4b

hart2_boot:
    /* Hart 2 (Cluster 1 AI/RT Control Worker): dedicated stack + trap vector + weak hart2_main */
    la sp, _hart2_stack_top
    la t0, trap_entry
    csrw mtvec, t0
    call hart2_main
5:
    wfi
    j 5b

hart3_boot:
    /* Hart 3 (Cluster 1 Telemetry/Verification Worker): dedicated stack + weak hart3_main */
    la sp, _hart3_stack_top
    la t0, trap_entry
    csrw mtvec, t0
    call hart3_main
6:
    wfi
    j 6b

hart1_main:
    wfi
    j hart1_main

hart2_main:
    wfi
    j hart2_main

hart3_main:
    wfi
    j hart3_main

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
