# Multi-Platform Heterogeneous SoC QA Matrix

This document records the heterogeneous multi-platform SoC test matrix under
`specs/multiplatform/` and the real-execution QA gate `usp qa run --suite multiplatform`.

## Design Matrix

| Design | CPU / Security / PMP | Interconnect / NoC | Accelerators & Peripherals | Backing IO & Engines | Honest Status |
|---|---|---|---|---|---|
| **Design 1** (`MP_Design1_Rocket_OT_QEMU`) | Chipyard `RocketTile` RTL (`chipyard.rocket64-imac-sv39-axi32`, hardware RV64 PMP/Sv39 CSRs) | AXI4-32 (`axi4-32-pa32`) | Chipyard `GCD` + 2× OpenTitan TL-UL `GPIO` | QEMU/gem5 RAM, ROM, CLINT, PLIC, Finisher, 2× NS16550 UART | Qualified native flow + IR + BSP + dual-engine lowering |
| **Design 2** (`MP_Design2_Gem5NoC_SMP`) | 4-hart RV64 SMP (`qemu.rv64` / `gem5.RiscvTimingSimpleCPU`) | IR-derived gem5 Ruby/Garnet 2×2 mesh (`derive_noc_topology`, explicit PIO-bypass disclosure) | 2× Chipyard `GCD` + 2× OpenTitan `GPIO` | >4 GiB physical RAM/SRAM + 2× QEMU UART/IO | Qualified SMP IR + NoC topology + calibration + dual-engine lowering |
| **Design 3** (`MP_Design3_MacNpu_Hetero`) | RV64 (`qemu.rv64` / `gem5.RiscvTimingSimpleCPU`) | Flat MMIO + `usp-mmio32-v1` external socket bridge | 2× original SystemC/TLM-2.0 MAC NPU-lite (`user.mac_npu`) + Chipyard `GCD` + OpenTitan `GPIO` | QEMU/gem5 UART, CLINT, PLIC, Finisher, RAM, ROM | Real C++17 SystemC build + 17-vector conformance + `riscv64-unknown-elf-gcc` driver compile in rootless Podman SDK |
| **Design 4** (`MP_Design4_Full_Gemmini_OT_PMP_Target`) | Chipyard `RocketTile` + OpenTitan `rv_core_ibex` / `earlgrey_full` | Concurrent gem5 Garnet NoC + QEMU IO | Upstream Chipyard `gemmini-candidate` + OpenTitan ePMP/PMP | Combined single-simulator target | **Explicitly BLOCKED (tested negative gate)**: rejects `chipyard.gemmini`, `opentitan.pmp`, and `opentitan.earlgrey_full` without silent ISS/stub substitution |

## Important Architectural Boundaries

1. **Original `user.mac_npu` vs. Upstream Chipyard Gemmini:**
   `specs/multiplatform/packages/mac_npu/` is an original synthetic 32-bit fixed-point MAC
   accelerator built on SystemC/TLM-2.0 (`usp-mmio32-v1`). Upstream Chipyard Gemmini
   (`generators/gemmini`) remains honestly cataloged as `pinned-source-needs-adapter` in
   `src/socsim/ip_catalog.py`.
2. **RISC-V PMP / OpenTitan ePMP Architecture:**
   Physical Memory Protection (PMP/ePMP) is an internal CPU-core CSR protection unit inside
   Chipyard `RocketTile` (RV64 PMP/Sv39) or OpenTitan `rv_core_ibex` (RV32 ePMP), not a
   standalone MMIO peripheral. Design 1 exercises Rocket's RV64 core contract alongside
   OpenTitan TL-UL GPIO peripherals; Design 4 verifies that requesting a fake standalone
   `opentitan.pmp` device or unadapted `opentitan.earlgrey_full` core fails cleanly.

## Execution Commands

```bash
# Run the 4-design multi-platform QA matrix and verify tamper-proof scorecard
usp qa run --suite multiplatform --out results/qa-multiplatform
usp qa scorecard results/qa-multiplatform

# Compile and run the real SystemC MAC NPU-lite binary (inside rootless Podman SDK)
usp package build specs/multiplatform/packages/mac_npu/package.yaml --out results/mac-npu-build
```
