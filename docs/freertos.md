# FreeRTOS RV64 bring-up

The `usp-rv64-v1` profile supports a source-built FreeRTOS Kernel 11.2.0 acceptance
BSP on the generated `usp-rv64` QEMU machine. Kernel source is pinned to commit
`0adc196d4bd52a2d91102b525b0aafc1e14a2386` from the public FreeRTOS-Kernel repository
(tag V11.2.0, MIT license). The adapter compiles the unmodified upstream GCC RISC-V
port with `RISCV_MTIME_CLINT_no_extensions`. No kernel or company source is vendored.

## Build and run

First build the generated machine using `scripts/bringup.sh`. Then acquire the
optional pinned kernel and run its acceptance. No new host dependency is needed
beyond the existing GNU RISC-V unknown-elf GCC 14.2.0/binutils toolchain.

```bash
source .venv/bin/activate
export CROSS_COMPILE=riscv64-unknown-elf-
USP_FETCH_FREERTOS=1 bash scripts/fetch_upstreams.sh
bash scripts/freertos_bringup.sh
```

Use `USP_UPSTREAM_ROOT` and `USP_GENERATED_DIR` to select existing source/hardware
build locations. For explicit lifecycle commands:

```bash
usp software build examples/rv64-minimal --os freertos \
  --generated build/rv64-minimal --source-freertos .tools/upstream/freertos \
  --cross-compile riscv64-unknown-elf-
usp test examples/rv64-minimal --suite freertos-boot \
  --generated build/rv64-minimal --out results/freertos-001
usp report results/freertos-001 --format md
```

`usp run ... --os freertos` selects the same acceptance. Keep each result directory
new. Builds remain bounded and sequential: this small port compiles eight source
files, so the common `--jobs` option does not parallelize this adapter.

## Hardware/software contract

The software build generates `freertos/bsp` from the verified hardware IR and the
existing generated `soc.h` and `memory.ld`. CLINT placement supplies MTIME at
base+0xbff8 and MTIMECMP at base+0x4000; the IR supplies the UART PLIC source number.
UART, PLIC and finisher addresses are never assumed to be upstream virt addresses.
The UART divisor is 2 for the pinned model's 3,686,400 Hz input (115200 baud).

ROM jumps to the ELF entry at the IR RAM base. Original startup code initializes
the stack, gp, BSS and the upstream trap handler. RV64IMAC plus Zicsr/Zifencei and
LP64 are required; only M mode and one hart are exercised. All task/ISR storage is
static, with stack overflow checks and active assertions. There is no heap, hosted
libc, FPU/vector task state, MPU or SMP support in this profile. The small original
freestanding memory functions actually implement memcpy/memset/memcmp. Its minimal
stdlib header supplies types only; no unavailable hosted function is stubbed.

The upstream port calls its timer input `configCPU_CLOCK_HZ`, but uses that value
to divide MTIME into ticks. The adapter therefore uses the IR's 10 MHz **timer**
timebase and a 1000 Hz kernel tick, without claiming a CPU instruction frequency.

Two equal-priority assembly tasks busy-loop without yielding or blocking. Each
keeps distinct s1–s11 values and verifies them continuously. A higher-priority
supervisor blocks for 30 ticks, then verifies that both workers progressed and
that scheduling occurred. The tick hook checks the actual machine-timer `mcause`.
The supervisor then enables the NS16550 TX-empty source and blocks on a task
notification. The external ISR validates machine-external `mcause`, PLIC claim and
UART interrupt ID, disables the source, completes the claim and notifies the task
using the upstream FromISR/yield API. The guest verifies pending/IRQ clear before
writing the success finisher.

Guest success requires these lines, numeric counter evidence and exit zero:

```text
USP_FREERTOS_BOOT_OK
USP_FREERTOS_TIMER_IRQ_OK
USP_FREERTOS_PREEMPTION_OK
USP_FREERTOS_UART_PLIC_IRQ_OK
USP_FREERTOS_METRICS ticks=... timer_irqs=... switches=... worker0=... worker1=... uart_irqs=1
USP_TEST_PASS
```

Loop counts depend on QEMU execution and host scheduling. They prove both tasks
ran; they are not performance measurements or calibrated simulated cycles.

## Regression, identity and limits

`freertos-build.json` records kernel revision/license, compiler version/hash,
compile flags, ELF hash, generated BSP hashes and independent software adapter/
resource hashes. It binds to the hardware generation manifest. The runner verifies
these identities and the actual QEMU binary before executing. Software adapter
changes require a FreeRTOS rebuild; they do not invalidate unrelated hardware,
Zephyr or Linux builds. Kernel MIT notices are copied to BSP/run evidence bundles.
Packaged wheels include all original BSP resources.

The native test suite additionally builds two explicit fault fixtures through
`--test-case assert-failure` and `--test-case tick-disabled`. The first reaches a
guest assertion and exits with the failure finisher; the second suppresses timer
setup and must time out without preemption success. These are expected **failed
guest runs**, never accepted as production passes. Regression also rejects modified
firmware/BSP artifacts, marker-only output, unsupported hybrid profiles and wrong
kernel revisions. Core tests do not require a kernel checkout; full promotion sets
`USP_REQUIRE_FREERTOS=1` and requires `USP_FREERTOS_SOURCE` for actual negative builds.

This is an executable acceptance BSP, not a general FreeRTOS driver SDK. RX,
queue/semaphore APIs, dynamic allocation, tick wraparound, deep sleep, nested
interrupts, full ISA/context stress, hybrid peripherals and other backends remain
unverified. No µC/OS product or license is selected implicitly.

The supervisor waits for both register-checking workers to make progress within
200 guest ticks before collecting acceptance counters. This accommodates overdue
functional-emulator ticks delivered in a burst under host load. Real worker
progress, saved-register correctness, at least 30 timer IRQs, preemption and the
UART/PLIC notification remain required; the no-tick negative still times out.
