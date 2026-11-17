# Native design capability assessment

This is the native-generator acceptance contract, updated 2026-11-17. A usable
arbitrary-design platform composes implemented models through declared
interfaces; IP-XACT metadata alone does not supply missing hardware behavior.
Unsupported combinations must fail before claiming success. This contract is
separate from the older fixed-profile OS, RTL and physical flows.

| Area | Native generator behavior | Remaining boundary |
|---|---|---|
| Instance identity | Names/counts, memory, UART and peripheral placement come from the input | Maximum 64 instances and 31 unique peripheral IRQs |
| CPU topology | 1..8 homogeneous RV64 harts; real instances/address spaces, shared memory and CLINT/PLIC contexts | Explicit QEMU rv64/max and gem5 TimingSimple/AtomicSimple choices; heterogeneous/RTL CPU lowering is separate |
| Memory map | 48-bit physical validation, two-cell DTS addresses/sizes and 64-bit firmware constants | No general address translations or multiple initiator-visible bus domains |
| Clock and latency | Configured frequency and RAM latency drive gem5; hardware graph records settings | QEMU is functional; global RTL timing, power and silicon calibration are unsupported |
| Models | Built-ins plus registered external executable/VLNV/hash/license descriptors | External v1 is aligned 32-bit MMIO in a 4 KiB window with one level IRQ |
| Software | Generated bare-metal BSP, per-hart startup/atomic/IPI/timer tests; native OpenSBI/Linux/DTB/initramfs path; explicit user ELF | Generic Zephyr/RTOS BSPs, full Yocto image and arbitrary RTL/MMU OS ports remain open |
| Engines | Custom generated QEMU and native gem5 graph; independent concurrent runs share one IR/ELF per design | Engines do not execute the same hart simultaneously |
| Performance | Native instructions/cycles/CPI/IPC per hart | Aggregate cycles are summed core cycles, not system throughput or silicon CPI |
| Interconnect | Flat functional address-decoded fabric; gem5 SystemXBar | General AXI/TileLink/NoC lowering, coherent DMA and global time ownership are separate exits |
| RTL/ASIC | Existing qualified nine-component test-chip flows remain available | A native emulator graph does not emit a complete matching RTL/ASIC implementation |
| Industrial use | Functional architecture/firmware exploration for supported contracts | No universal coverage, full 46/37-instance architectures or production sign-off claim |

## Executable design cases

- specs/AI_SOC-native.yaml and AIZ_SOC-native.yaml preserve the single-hart
  13/11-instance cases.
- specs/AI_SOC-smp.yaml uses four harts, 16 instances, high physical RAM,
  multiple UARTs/GCDs/GPIOs and configured gem5 clock/latency.
- specs/AIZ_SOC-smp.yaml uses two harts, 12 instances, AtomicSimple, different high
  RAM, three GCDs and a different reset/IRQ/timebase map.
- specs/validation/smp-max.yaml is an eight-hart boundary fixture, not a third
  architecture target.
- scripts/native_external_bringup.py builds an original external SystemC/TLM
  peripheral and user interrupt driver, absent from the built-in catalog.

SMP firmware verifies each hart, one-shot timer/IPI delivery and an atomic shared
sum, then source-IP IRQs/results. Linux requires OpenSBI/kernel/userspace output
and clean guest power-off on both engines; it does not qualify unused drivers.
The external case requires actual transactions and matching commands/results.
Bus errors, modified executables and missing adapters fail.

## Failures found through execution

The first Linux run exposed two defects: Linux 6.12 SMP needs modern
riscv,isa-base/isa-extensions properties, and comparing a gem5 Addr parameter
directly with a Python integer omitted the RAM DTB image. The frontend now emits
modern and legacy CPU properties; the RAM image is selected by instance identity.

AtomicSimple bare-metal exposed delayed MTIP deassertion: gem5 CLINT clears pending
timer state at its next RTC callback. The one-shot conformance handler masks a
serviced timer/software interrupt before clearing it. Exact one-interrupt
assertions and the requested Atomic CPU are retained. Failed and corrected runs
remain in ignored local evidence.

## Qualification and open exits

Promotion requires all prior gates plus SMP/Linux, maximum harts and external
model/firmware tests, with no required skips. Explicit kernel reuse checks the
original spec, source pins, compiled inputs and every artifact; DTBs regenerate.
QEMU reuse requires identical generated C, source pin and original binary.
Hand-written hash refreshes are prohibited.

This closes specific topology, address-width, Linux and registration gaps.
Wider coverage needs bus/clock/reset/translation IR, CPU/initiator adapters,
model-specific reset/error/DMA behavior and OS/driver conformance. Those require
engineering interfaces and executable gates, not inference from an IP name or a
successful Linux boot.
