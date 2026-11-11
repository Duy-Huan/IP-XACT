# Capability matrix and acceptance roadmap

| Area | Current status | Required next evidence |
|---|---|---|
| IP-XACT 2022 XSD | Implemented offline | Keep bundle checksums/license intact |
| Semantic elaboration | Strict flat component/design subset | Registers, fields, buses, parameters, views and hierarchy tests |
| Canonical IR | Implemented, deterministic | Versioned richer bus/IRQ/clock/reset schema |
| Model binding | Exact VLNV + builtin QEMU identifiers | Reusable, versioned per-IP capability registry |
| QEMU | Fixed RV64 virt bare-metal profile | Custom machine and MMIO/IRQ tests |
| gem5 | Planned; execution rejects selection | Pinned real SE workload; separately validated FS boot |
| Chipyard | Planned source/generator adapter | Pinned submodules, Chisel/CIRCT, Verilator test |
| OpenTitan | Planned IP/SoC import | Selected IP CSR, TileLink, reset and lifecycle contracts |
| SystemC | Planned; no bridge | Versioned TLM transactors and MMIO/readback/IRQ test |
| Bare-metal | Real UART + finisher smoke | Generated headers/linker and peripheral semantics |
| Zephyr | Planned primary RTOS | BSP generation, west build and real boot regression |
| FreeRTOS / µC/OS | Planned | Specific port, product/license, tick/IRQ tests |
| Yocto/Linux | Planned | Pinned layers, MACHINE, OpenSBI/bootloader/kernel/DTB image and boot |
| Cross-backend equivalence/security | Unimplemented | Real matched traces, negative security tests; no invented claims |

## Milestones

M0/M1: CLI, official schema bundle, strict metadata subset, IR, fail-closed bindings,
QEMU reference smoke, CI and development rules. This release addresses that slice.
It does not satisfy the complete platform objective.

M2: add register and field contracts, bus definitions/interconnections, views and
fileSets. Resolve parameters with a constrained integer evaluator, not `eval`.
Introduce one custom MMIO peripheral and native QEMU machine integration. Generate
headers/linker from validated topology and verify reset/read/write/IRQ in firmware.

M3: pin gem5 source/build flags, implement an SE RISC-V test and real statistics
capture. Add FS board boot independently; do not use SE to claim MMIO/OS validation.
Test matched functional device contracts across engines; performance remains
uncalibrated unless correlated to a defensible reference.

M4: real Verilator peripheral test, then Chipyard generator and OpenTitan IP
integration. Source provenance is separate from simulator implementation. Require
protocol, clock, reset, interrupt and lifecycle compatibility per selected IP.

M5: Zephyr first, then specific FreeRTOS/µC/OS ports and Yocto bring-up. Every OS
must really build and execute its acceptance workload. Do not generate cosmetic
BSP/layer files and call them bootable.

M6: selective hybrid simulation, first MMIO/readback/IRQ, then DMA and protection.
Define memory owner, endian/order, abort/error/reset, backpressure and time mapping.
No universal QEMU↔gem5 lockstep or automatic RTL behavioral translation is promised.
