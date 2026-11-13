# Zephyr on instruction-owning RV32 RTL CPUs

`usp rtl-zephyr` builds the pinned unmodified Zephyr kernel for an original
out-of-tree RV32 SoC/board, then executes it with either supported actual RTL
CPU and QEMU qtest or the gem5 external atomic memory master. The RTL owns all
instructions; native engine CPU contexts remain passive interrupt sinks. This
profile is separate from the native RV64 Zephyr board and does not enable Linux.

The BSP derives UART, CLINT, PLIC and source-IP addresses, interrupts, memory and
timer frequency from the verified generated I/O contract. It selects RV32IMC,
Zicsr/Zifencei, M-mode and software atomics. It enables no A, FPU/vector, PMP,
userspace, MMU or SMP. The former RV64/QEMU SoC selector is replaced with an
explicit original RTL SoC definition; the upstream kernel is unchanged.

Ibex starts at RAM+0x80 and only supports vectored mtvec. An original reset shim
contains jumps at RAM and RAM+0x80, before Zephyr's normal startup and 256-byte
aligned IRQ vector table. The linker selects the shim as ELF entry. The build
verifies the resulting RV32 ELF and actual kernel configuration; it does not
change ELF headers after linking or silently run an ISS substitute.

The existing generated conformance application runs real kernel semaphore waits
and wakeups from timer/UART ISRs, four Chipyard GCD results/interrupts and OpenTitan
GPIO readback, loopback, interrupt, W1C and masked output checks. Both IP families
remain actual RTL in the same SystemC process. The negative firmware disables
machine timer IRQ7 after kernel boot; a missing tick must fail the bounded RTL
run even when the boot banner is correct. Verification rejects modified reset/BSP
artifacts and preserves original sources, ELF/config/DTS, commands and licenses.

```sh
usp rtl-zephyr build systems/AI_SOC/bringup --generated build/AI_SOC \
  --source .tools/upstream/zephyr --out build/AI_SOC-rtl-zephyr
usp rtl-zephyr run systems/AI_SOC/bringup --generated build/AI_SOC \
  --firmware build/AI_SOC-rtl-zephyr --manifest models/cpus/chipyard-ibex.yaml \
  --cpu build/rtl-cpu-ibex --models build/source-ip-models --engine gem5 \
  --bridge build/rtl-master-gem5 --out results/AI_SOC-rtl-zephyr
usp rtl-zephyr verify results/AI_SOC-rtl-zephyr
```

Use the pinned native tool and source setup from [rtl-cpu-adapters.md](rtl-cpu-adapters.md).
The compiler is GCC 14.2.0, and Zephyr source remains pinned at
413b789deb391d3a37d06b463288a5fe765ee57e. No external Zephyr modules are selected.
With the existing CPU/model/generated roots activated, set a fresh
USP_RTL_ZEPHYR_ROOT and run `bash scripts/rtl_zephyr_bringup.sh`.

The actual matrix passed 14 tests in 116.03 seconds on 2026-11-13: both CPU
profiles, both memory engines, all three I/O maps and two missing-timer cases.
Actual RVFI/clock counters and zero gem5 ISS instruction/cycle counters remain
mandatory. CPI/IPC covers reset through completion, including kernel idle and
the 20 ms timer interval; it is not calibrated application/silicon performance.
Peripheral timing remains transaction-driven and is not globally correlated.

Promotion requires USP_REQUIRE_RTL_ZEPHYR and USP_RTL_ZEPHYR_ROOT. The manual
RTL-OS workflow builds/tests this matrix alongside FreeRTOS and both µC/OS
kernels. The general `usp platform` YAML RTL workload selector still accepts
bare-metal only; these explicit CLI workflows provide OS execution. Rocket/BOOM,
other CPU protocols/ISA/MMU, Linux and larger full-system architectures remain
separate work.
