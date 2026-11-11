/* Synthetic, project-owned smoke firmware. No company firmware or keys. */
.section .text.init
.globl _start
_start:
    la t0, message
    li t1, 0x10000000
1:
    lbu t2, 0(t0)
    beqz t2, 3f
2:
    lbu t3, 5(t1)       /* NS16550 LSR: wait until transmit register empty */
    andi t3, t3, 0x20
    beqz t3, 2b
    sb t2, 0(t1)
    addi t0, t0, 1
    j 1b
3:
    li t0, 0x100000     /* QEMU virt SiFive test finisher */
    li t1, 0x5555
    sw t1, 0(t0)
4:
    wfi
    j 4b
.section .rodata
message:
    .asciz "SOCSIM_BOOT_OK\n"
