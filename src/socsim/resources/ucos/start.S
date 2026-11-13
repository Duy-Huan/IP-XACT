/* SPDX-License-Identifier: Apache-2.0 */
.section .text.init,"ax"
.global _start
.option norvc
_start:
    j reset
    .space 124
reset:
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
    sw zero, 0(t0)
    addi t0, t0, 4
    j 1b
2:  la t0, vectors
    ori t0, t0, 1
    csrw mtvec, t0
    call main
    tail usp_fail

.section .text.vectors,"ax"
.balign 256
vectors:
.rept 32
    j trap
.endr

/* The upstream RV32 software-IRQ port consumes x1..x31 followed by mepc.
 * No nested interrupts, FPU, vector, privilege change or MMU are supported. */
trap:
    addi sp, sp, -128
.irp r,1,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31
    sw x\r, (\r-1)*4(sp)
.endr
    addi t0, sp, 128
    sw t0, 4(sp)
    csrr t0, mepc
    sw t0, 124(sp)
    csrr t0, mcause
    li t1, 0x80000003
    beq t0, t1, soft_irq
    mv a0, t0
    call usp_interrupt
    lw t0, 124(sp)
    csrw mepc, t0
.irp r,1,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31
    lw x\r, (\r-1)*4(sp)
.endr
    addi sp, sp, 128
    mret
soft_irq:
    mv a2, sp
    tail Software_IRQHandler

/* Never yields: only a timer-induced higher-priority wakeup can preempt this
 * worker. Each resumed iteration checks every callee-saved register. */
.global usp_register_worker
usp_register_worker:
    mv s0, a0
    lw s1, 4(s0)
.irp r,2,3,4,5,6,7,8,9,10,11
    addi s\r, s1, \r
.endr
3:  lw t0, 4(s0)
    bne s1, t0, 4f
.irp r,2,3,4,5,6,7,8,9,10,11
    addi t0, s1, \r
    bne s\r, t0, 4f
.endr
    lw t0, 0(s0)
    addi t0, t0, 1
    sw t0, 0(s0)
    j 3b
4:  tail usp_fail
