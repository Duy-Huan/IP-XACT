# Single Unified Multi-Platform SystemC/TLM-2.0 SoC Emulator

This document describes the single unified heterogeneous SoC emulator
(`specs/multiplatform/unified_hetero_soc.yaml`, `specs/multiplatform/unified_systemc/`,
and `src/socsim/unified_systemc_soc.py`) that merges all four multi-platform domains into
**one unified address map, one PLIC interrupt domain, and one SystemC/TLM-2.0 2×2 Mesh NoC
simulation binary** executing real compiled RISC-V64 bare-metal software (`unified_sw.elf`).

## Unified SystemC/TLM-2.0 2×2 NoC Topology

All mapped IP models live inside one `sc_main` top-level connected through `NoC2x2Router`
with Manhattan hop latency annotation (`10 ns/hop + 5 ns link`):

| Router | Mapped IP Model | Platform Family | Base Address | PLIC IRQ |
|---|---|---|---|---|
| `[0, 0]` | `unified_rv64_core` (RV64IM_Zicsr + M-mode PMP CSRs) | Initiator CPU | entry `0x8000_0000` | — |
| `[0, 0]` | `main_dram` (16 MiB), `boot_rom` (4 KiB), `sim_finisher` | QEMU-compatible IO | `0x8000_0000`, `0x0000_1000`, `0x0010_0000` | — |
| `[1, 0]` | `qemu_uart_0` (NS16550), `sys_clint`, `sys_plic` | QEMU-compatible IO | `0x1100_0000`, `0x0200_0000`, `0x0C00_0000` | `10` |
| `[0, 1]` | `chipyard_gcd_0` (`chipyard.gcd`) | Chipyard contract | `0x1300_0000` | `12` |
| `[0, 1]` | `opentitan_gpio_0` (`opentitan.gpio`) | OpenTitan TL-UL contract | `0x1400_0000` | `13` |
| `[1, 1]` | `mac_npu_0` (`user.mac_npu`) | Original SystemC MAC NPU | `0x1500_0000` | `20` |
| `[1, 1]` | `pmp_guard_0` (`user.pmp_guard`) | Original SystemC NoC IOPMP Guard | `0x1600_0000` | `21` |

## Single-Source-of-Truth Generation & Software Execution

1. `usp unified-soc plan` validates `unified_hetero_soc.yaml` and generates `unified_soc_map.h`
   and `link.ld`, which are included by **both** `unified_systemc_soc.cpp` and `unified_sw.c`.
2. `usp unified-soc run` compiles the SystemC 3.0.1 emulator (`unified-soc-emu`), compiles
   both positive (`unified_sw.elf`) and negative (`fail_sw.elf`) RISC-V64 bare-metal ELF images
   with `riscv64-unknown-elf-gcc -march=rv64im_zicsr -mabi=lp64`, executes both on the unified
   SystemC NoC, and verifies:
   - Real UART console markers (`UNIFIED_SOC_BOOT_OK`, `RISCV_PMP_CSR_OK`,
     `CHIPYARD_GCD_IRQ_OK`, `OPENTITAN_GPIO_IRQ_OK`, `MAC_NPU_DOT_IRQ_OK`,
     `PMP_GUARD_FAULT_IRQ_OK`, `UNIFIED_SOC_ALL_PASS`).
   - All 4 NoC routers `[R00, R10, R01, R11]` exercised with real Manhattan hops logged in
     `noc-trace.jsonl`.
   - Real hardware trap handling for both PLIC external interrupts (`mcause = 0x800000000000000b`)
     and synchronous PMP Bus Guard Store Access Fault (`mcause = 7`).
   - Negative guest firmware (`fail_sw.elf`) reported as `FAILED` with non-zero exit code.

## Honest Fidelity Disclosure

- Fidelity is explicitly labeled `systemc-tlm2-functional-annotated-noc`: TLM-2.0 behavioral
  models with annotated NoC hop latencies — distinct from cycle-accurate Verilated RTL, live
  gem5 Garnet, upstream Chipyard Gemmini RTL, or OpenTitan Verilated EarlGrey RTL.
- Hosts without `libsystemc-dev` or `riscv64-unknown-elf-gcc` report `BLOCKED` cleanly; full
  live compilation and execution run inside the rootless Podman SDK (`bash scripts/podman.sh smoke`).
