# Single Unified Multi-Platform SystemC/TLM-2.0 + Verilator RTL SoC Emulator

This document describes the single unified heterogeneous SoC emulator
(`specs/multiplatform/unified_hetero_soc.yaml`, `specs/multiplatform/unified_systemc/`,
and `src/socsim/unified_systemc_soc.py`) that merges all multi-platform domains into
**one unified address map, one PLIC interrupt domain, and one hybrid SystemC/TLM-2.0 +
Verilator RTL 2×2 Mesh NoC simulation binary** executing real compiled RISC-V64 bare-metal
software (`unified_sw.elf`).

## Hybrid SystemC/TLM-2.0 + Verilator RTL 2×2 NoC Topology

All mapped IP models live inside one `sc_main` top-level connected through `NoC2x2Router`
(with two bus masters: `Rv64CpuInitiator` and `DmaEngineTlm`) using Manhattan hop latency
annotation (`10 ns/hop + 5 ns link`):

| Router | Mapped IP Model | Backend / Platform Family | Base Address | PLIC IRQ |
|---|---|---|---|---|
| `[0, 0]` | `unified_rv64_core` (`RV64IM_Zicsr` + PMP CSRs + GDB RSP stub) | SystemC Initiator CPU (Master 0) | entry `0x8000_0000` | — |
| `[0, 0]` | `main_dram` (16 MiB), `boot_rom` (4 KiB), `sim_finisher` | QEMU-compatible IO | `0x8000_0000`, `0x0000_1000`, `0x0010_0000` | — |
| `[1, 0]` | `qemu_uart_0` (NS16550), `sys_clint`, `sys_plic` | QEMU-compatible IO | `0x1100_0000`, `0x0200_0000`, `0x0C00_0000` | `10` |
| `[0, 1]` | `chipyard_gcd_0` (`chipyard.gcd`) | Chipyard TLM-2.0 contract | `0x1300_0000` | `12` |
| `[0, 1]` | `opentitan_gpio_0` (`opentitan.gpio`) | OpenTitan TL-UL TLM-2.0 contract | `0x1400_0000` | `13` |
| `[0, 1]` | `dma_engine_0` (`user.dma_engine`) | SystemC 2nd NoC Bus Master (Master 1) | `0x1700_0000` | `14` |
| `[1, 1]` | `mac_npu_0` (`user.mac_npu` — `rtl/mac_npu_rtl.sv`) | **Verilator 5.032 SystemVerilog RTL** (or TLM-2.0) | `0x1500_0000` | `20` |
| `[1, 1]` | `pmp_guard_0` (`user.pmp_guard`) | SystemC NoC IOPMP Bus Guard (CPU & DMA) | `0x1600_0000` | `21` |

## Commands & Enterprise Features

```bash
# 1. Build & run hybrid Verilator RTL + SystemC 2x2 NoC + RV64 SW + live GDB RSP test
usp unified-soc run specs/multiplatform/unified_hetero_soc.yaml --out results/unified-soc

# 2. Run RTL-vs-TLM cycle calibration on the exact same RV64 ELF
usp unified-soc calibrate specs/multiplatform/unified_hetero_soc.yaml --out results/unified-cal

# 3. Publish & resolve versioned IP packages across teams (IP Plugin Registry)
usp registry publish specs/multiplatform/packages/mac_npu/package.yaml --registry results/reg --version 1.0.0
usp registry verify --registry results/reg

# 4. Run CHIA-style parallel DSE sweep with tamper-checked ArtifactCache
usp dse sweep specs/multiplatform/unified_hetero_soc.yaml --out results/dse --workers 2 --cache results/dse-cache
```

## Honest Fidelity Disclosure

- In `verilator-rtl` mode (`fidelity: hybrid-systemc-tlm2-and-verilator-rtl-annotated-noc`),
  `mac_npu_0` executes actual Verilator-compiled SystemVerilog (`mac_npu_rtl.sv`) cycle-by-cycle
  inside the SystemC top-level, while the CPU/NoC/GCD/GPIO/DMA/PMP models remain SystemC TLM-2.0
  behavioral models (distinct from Verilated Chipyard Rocket/Gemmini RTL, OpenTitan EarlGrey RTL,
  or live gem5 Garnet).
