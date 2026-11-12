/* SPDX-License-Identifier: Apache-2.0 */
.section .text.init,"ax"
.global _start
_start:
    csrw mie, zero
    csrw mstatus, zero
    la sp, __stack_top
    la t0, __bss_start
    la t1, __bss_end
1:  bgeu t0, t1, 2f
    sd zero, 0(t0)
    addi t0, t0, 8
    j 1b
2:  la t0, trap_handler
    csrw mtvec, t0
    call main
3:  wfi
    j 3b
