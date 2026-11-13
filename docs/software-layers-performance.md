# Software layers and performance on the unified virtual SoC

The five-layer software scope below applies to native RV64 ISS CPUs. External
RV32 RTL masters currently support bare-metal, with actual clock/RVFI CPI/IPC.
The passive gem5 IRQ context does not execute instructions. See rtl-cpu-adapters.md.

The platform now supports independently selected bare-metal, FreeRTOS, Zephyr,
Linux and boot-sequence workloads on QEMU or gem5. These are alternative software
images, not an assertion that the kernels are stacked or run concurrently.
CPU provider/model, engine and selected workloads are separate configuration data;
see cpu-platforms.md. The fixed five-by-two matrix is a regression reference.

| Layer | Real acceptance |
|---|---|
| Bare-metal | RV64 startup/BSS/stack, memory arithmetic, CLINT ISR, real GCD/GPIO PLIC interrupts |
| FreeRTOS 11.2.0 | Actual upstream port, tick preemption, saved registers, task progress, UART FromISR |
| Zephyr 4.2.0 | Generated board/DTS, timer/UART/PLIC, actual SystemC GCD/GPIO ISR/readback/W1C |
| Linux 6.12 | OpenSBI 1.6, MMU/S-mode kernel, initramfs PID 1, shared workload, clean poweroff |
| Boot sequence | Ordered OpenSBI → kernel → /init → successful userspace → poweroff |

All runs retain the actual Chipyard/OpenTitan SystemC model process. Bare-metal
and Zephyr exercise both custom IP drivers. FreeRTOS and Linux currently test
engine-native CPU/OS/peripheral behavior; GCD/GPIO OS drivers for those layers
remain unverified. µC/OS and other RTOS products need separate source/license/BSP
adapters. A boot-sequence pass is not secure boot or a manufacturing/security claim.

## Build and run

Use the same pinned QEMU, gem5, native/SystemC tools and verified model bundle as
unified-soc.md. Prepare the generated project and Zephyr as usual, then:

```bash
usp layers enable-qemu examples/rv64-ips --generated build/rv64-ips \
  --source-qemu /path/to/qemu --jobs 2
usp layers counter-build --source-qemu /path/to/qemu --out build/performance-counter
usp layers baremetal-build examples/rv64-ips --generated build/rv64-ips
usp software build examples/rv64-ips --os freertos --generated build/rv64-ips \
  --source-freertos /path/to/FreeRTOS-Kernel --cross-compile riscv64-unknown-elf-
usp software build examples/rv64-ips --os linux --generated build/rv64-ips \
  --source-linux /path/to/linux --source-opensbi /path/to/opensbi \
  --cross-compile riscv64-linux-gnu- --jobs 2

usp layers run examples/rv64-ips --engine gem5 --os linux \
  --generated build/rv64-ips --models build/source-ip-models \
  --bridge build/unified-gem5 --out results/linux-gem5-001
usp layers matrix examples/rv64-ips --generated build/rv64-ips \
  --models build/source-ip-models --bridge build/unified-gem5 \
  --counter build/performance-counter --out results/software-matrix-001
usp layers verify results/software-matrix-001
```

Use fresh build/result output directories where required. enable-qemu explicitly
reconfigures and actually rebuilds the generated machine with --enable-plugins;
it refreshes the actual QEMU build record. A -plugin help entry alone does not prove
the feature was compiled. Counter identity is checked against its source and binary.
The gem5 extension must include the verified syscon finisher read/modify/write
contract required by Linux poweroff; rebuild after extension source changes.

For gem5 Linux, actual MMU page-table walkers attach to the memory bus. The exact
verified DTB is linked into a loadable ELF RAM image. A real reset trampoline loads
the DTB pointer into a1 and starts OpenSBI; kernel/userspace are not simulated by
host marker output. Both full software images and their hashes are retained in
portable matrix bundles. Repeat with systems/AI_SOC/bringup or AIZ_SOC/bringup and
their matching generated builds; do not mix their software/hardware identities.

## Counter meaning and limitations

Gem5 performance comes directly from its native statistics:

```text
instructions = system.cpu.commitStats0.numInsts
cycles       = system.cpu.numCycles
CPI          = cycles / instructions
IPC          = instructions / cycles
```

Raw stats, source keys, simulation ticks/seconds and global simInsts are retained.
These gem5 counters can have different scopes; they are not forced to equal one
another. Repeated dumps are not summed. The selected CPU model, clock, memory
mode and latency assumptions remain in the plan and reports. These are native
model cycles, not calibrated silicon measurements.

QEMU uses an original native plugin through its public plugin API. Inline per-vCPU
instrumentation counts actual instruction execution callbacks and data-access
callbacks. Instruction callbacks precede execution: these are attempts, not
retirement counters. The reference analytical model is explicitly configurable:

```text
estimated_cycles = instructions * base_cpi + data_accesses * memory_extra_cycles
estimated_CPI    = estimated_cycles / instructions
estimated_IPC    = instructions / estimated_cycles
```

Default costs are 1 and 1. They are uncalibrated reference parameters, not a Rocket,
BOOM or silicon model. QEMU native_cycles/native_CPI/native_IPC remain null. No host
wall time or icount virtual-clock scale becomes hardware CPU cycles. Uniform
memory costs do not implement caches, branch prediction, out-of-order overlap,
pipeline hazards or MMIO latency. Pinned QEMU 10 physical-address lookup was
unreliable for some valid RISC-V helper accesses, so no physical/MMIO classification
is inferred. Full failed probes remain in local history.

The current interval is the whole guest, including boot and, for gem5, idle/WFI
time. Thus a high boot CPI can reflect waiting rather than poor instruction
throughput. ROI/microbenchmark calibration and CPU-specific cost plugins are
additional work. Cross-engine ratios must not be presented as hardware-equivalent
performance. SystemC RTL time remains transaction-annotated, as in unified-soc.md.

## Required integration gate

USP_REQUIRE_SW_LAYERS=1 requires actual native source artifacts. Set
USP_UNIFIED_GEM5_BUILD, USP_COUNTER_BUILD_DIR, USP_IP_MODELS_DIR,
USP_IP_GENERATED_DIR and USP_DUAL_BUILD_ROOT. Tests execute all five layer checks
on both engines for rv64-ips and both system bring-up maps, plus alternate CPU
profiles. Missing native artifacts fail; they are not successful skips. Verification
checks raw counter/stat hashes, recomputes metrics and checks common ELF/IR/model
identities. Native execution, schematic metadata and physical signoff remain distinct.
