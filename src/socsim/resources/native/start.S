/* SPDX-License-Identifier: Apache-2.0 */
.section .text.init,"ax"
.global _start
_start:
    csrw mie, zero
    csrw mstatus, zero
    .option push
    .option norelax
    la gp, __global_pointer$
    .option pop
    la sp, __stack_top
    csrr a0, mhartid
    slli t0, a0, 12
    sub sp, sp, t0
    bnez a0, 4f
    la t0, __bss_start
    la t1, __bss_end
1:  bgeu t0, t1, 2f
    sd zero, 0(t0)
    addi t0, t0, 8
    j 1b
2:  la t0, trap_handler
    csrw mtvec, t0
    la t0, boot_ready
    li t1, 1
    amoswap.w.rl zero, t1, (t0)
    call main
3:  wfi
    j 3b
4:  la t0, boot_ready
5:  lw t1, 0(t0)
    beqz t1, 5b
    fence r, rw
    la t0, trap_handler
    csrw mtvec, t0
    call secondary_main
    j 3b
