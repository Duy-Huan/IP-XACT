/* SPDX-License-Identifier: Apache-2.0 */
/* Original acceptance firmware: actual upstream Rocket PTW/PMP/Sv39/S/U. */
#include <stdint.h>
#include "soc.h"
#define R32(a) (*(volatile uint32_t *)(a))
#define UART(n) (*(volatile uint8_t *)(USP_UART_BASE+(n)))
static uint64_t root[512] __attribute__((aligned(4096)));
static uint64_t middle[512] __attribute__((aligned(4096)));
static uint64_t user_leaf[512] __attribute__((aligned(4096)));
static volatile uint64_t user_data[512] __attribute__((aligned(4096)));
static volatile uint64_t shared=0x1020304050607080UL;
static volatile unsigned supervisor_faults,user_faults,illegal_faults,supervisor_done;
static void puts_uart(const char *s) {
 while(*s) { while(!(UART(5)&0x20)){} UART(0)=*s++; }
}
static void finish(uint32_t code) __attribute__((noreturn));
static void finish(uint32_t code) {
 while(!(UART(5)&0x40)){} R32(USP_FINISHER_BASE)=code;
 for(;;) __asm__ volatile("wfi");
}
static void fail(void) __attribute__((noreturn));
static void fail(void) {puts_uart("USP_ROCKET_MMU_FAIL\n");finish(0x13333);}
void trap_handler(void) __attribute__((interrupt("machine"),aligned(4)));
void trap_handler(void) {
 uint64_t cause,status,value,pc,satp;
 __asm__ volatile("csrr %0,mcause":"=r"(cause));
 __asm__ volatile("csrr %0,mstatus":"=r"(status));
 __asm__ volatile("csrr %0,mtval":"=r"(value));
 __asm__ volatile("csrr %0,mepc":"=r"(pc));
 __asm__ volatile("csrr %0,satp":"=r"(satp));
 unsigned previous=(status>>11)&3;
 if((satp>>60)!=8) fail();
 if(cause==13 && previous==1 && value==0x60000000UL) ++supervisor_faults;
 else if(cause==13 && previous==0 && value==0x80000000UL) ++user_faults;
 else if(cause==2 && previous==0) ++illegal_faults;
 else if(cause==9 && previous==1) {
  if(supervisor_faults!=1 || shared!=0x8877665544332211UL) fail();
  supervisor_done=1; puts_uart("USP_ROCKET_SV39_OK\n");
 } else if(cause==8 && previous==0) {
  if(!supervisor_done || user_faults!=1 || illegal_faults!=1 || user_data[0]!=0x2468) fail();
  puts_uart("USP_ROCKET_USER_PROTECTION_OK\nUSP_ROCKET_USER_ATOMIC_OK\nUSP_TEST_PASS\n");
  finish(0x5555);
 } else fail();
 pc+=4; __asm__ volatile("csrw mepc,%0"::"r"(pc));
}
void user_entry(void) __attribute__((naked,section(".text.user"),aligned(4096)));
void user_entry(void) {
 __asm__ volatile(
  ".option push\n.option norvc\n"
  "li t0,0x50001000\nli t1,0x1234\nsd t1,0(t0)\namoadd.d t2,t1,(t0)\n"
  "li t0,0x80000000\nld t1,0(t0)\ncsrr t1,mstatus\necall\n1: j 1b\n.option pop\n");
}
static void supervisor_entry(void) __attribute__((noreturn));
static void supervisor_entry(void) {
 volatile uint64_t *alias=(volatile uint64_t *)(0x40000000UL+((uintptr_t)&shared-0x80000000UL));
 if(*alias!=0x1020304050607080UL) fail();
 *alias=0x8877665544332211UL;
 __asm__ volatile(".option push; .option norvc; ld zero,0(%0); .option pop"::"r"(0x60000000UL):"memory");
 __asm__ volatile("ecall":::"memory");
 __asm__ volatile("csrw sepc,%0; csrc sstatus,%1; sret"::"r"(0x50000000UL),"r"(1UL<<8):"memory");
 __builtin_unreachable();
}
int main(void) {
 UART(1)=0; UART(3)=0x80; UART(0)=2; UART(1)=0; UART(3)=3; UART(2)=1;
 puts_uart("USP_ROCKET_MMU_BOOT_OK\n");
 /* S-mode identity mapping and an explicit two-level alias. No U access. */
 root[2]=((0x80000000UL>>12)<<10)|0xcf;
 root[1]=(((uintptr_t)middle>>12)<<10)|1;
 middle[0]=((0x80000000UL>>12)<<10)|0xc7;
 middle[128]=(((uintptr_t)user_leaf>>12)<<10)|1;
 user_leaf[0]=(((uintptr_t)user_entry>>12)<<10)|0xdb;
 user_leaf[1]=(((uintptr_t)user_data>>12)<<10)|0xd7;
 __asm__ volatile("csrw pmpaddr0,%0; csrw pmpcfg0,%1"::"r"(~0UL),"r"(0xfUL):"memory");
 __asm__ volatile("csrw satp,%0; sfence.vma"::"r"((8UL<<60)|((uintptr_t)root>>12)):"memory");
 __asm__ volatile("csrw mepc,%0; csrw mstatus,%1; mret"::"r"(supervisor_entry),"r"(1UL<<11):"memory");
 __builtin_unreachable();
}
