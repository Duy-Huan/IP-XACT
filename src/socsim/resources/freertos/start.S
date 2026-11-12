/* SPDX-License-Identifier: Apache-2.0 */
.section .text.init, "ax"
.balign 16
.global _start
_start:
    csrw mie, zero
    csrw mstatus, zero
    .option push
    .option norelax
    la gp, __global_pointer$
    .option pop
    la sp, __stack_top
    la t0, __bss_start
    la t1, __bss_end
1:  bgeu t0, t1, 2f
    sd zero, 0(t0)
    addi t0, t0, 8
    j 1b
2:  la t0, freertos_risc_v_trap_handler
    csrw mtvec, t0
    call main
    tail usp_fail

/* Two equal-priority tasks never yield or block. Only the timer can preempt
 * them. Distinct saved-register values must survive every context switch. */
.section .text.usp_register_worker, "ax"
.global usp_register_worker
usp_register_worker:
    mv s0, a0
    ld s1, 8(s0)
    addi s2, s1, 2
    addi s3, s1, 3
    addi s4, s1, 4
    addi s5, s1, 5
    addi s6, s1, 6
    addi s7, s1, 7
    addi s8, s1, 8
    addi s9, s1, 9
    addi s10, s1, 10
    addi s11, s1, 11
.macro check_saved reg, offset
    addi t0, s1, \offset
    bne \reg, t0, 4f
.endm
3:  ld t0, 8(s0)
    bne s1, t0, 4f
    check_saved s2, 2
    check_saved s3, 3
    check_saved s4, 4
    check_saved s5, 5
    check_saved s6, 6
    check_saved s7, 7
    check_saved s8, 8
    check_saved s9, 9
    check_saved s10, 10
    check_saved s11, 11
    ld t0, 0(s0)
    addi t0, t0, 1
    sd t0, 0(s0)
    j 3b
4:  tail usp_fail
