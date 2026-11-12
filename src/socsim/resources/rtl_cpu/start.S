/* SPDX-License-Identifier: Apache-2.0 */
.section .text.init,"ax"
.global _start
.option norelax
_start:
    .option push
    .option norvc
    j rtl_start
    .space 124
    .option pop
rtl_start:
    csrw mie, zero
    csrw mstatus, zero
    la sp, __stack_top
    la t0, __bss_start
    la t1, __bss_end
1:  bgeu t0, t1, 2f
    sw zero, 0(t0)
    addi t0, t0, 4
    j 1b
2:  la t0, trap_vector
    ori t0, t0, 1
    csrw mtvec, t0
    call main
3:  wfi
    j 3b
.section .vectors,"ax"
.balign 256
.option norvc
trap_vector:
.rept 32
    j trap_handler
.endr
