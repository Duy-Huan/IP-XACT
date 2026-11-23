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
| `[0, 0]` | `unified_rv64_hart0` (`RV64IMA_Zicsr` + PMP CSRs + GDB RSP) | SystemC Initiator CPU (Master 0) | entry `0x8000_0000` | — |
| `[0, 0]` | `main_dram` (16 MiB), `boot_rom` (4 KiB), `sim_finisher` | QEMU-compatible IO | `0x8000_0000`, `0x0000_1000`, `0x0010_0000` | — |
| `[0, 0]` | `l2_cache_0` (`user.l2_cache` — `rtl/l2_cache_rtl.sv`) | **Verilator 5.032 SystemVerilog RTL** (64-line L2 + snoop) | `0x1A00_0000` | — |
| `[1, 0]` | `unified_rv64_hart1` (`RV64IMA_Zicsr` + CLINT IPI + RV64A) | SystemC SMP Initiator CPU (Master 1) | entry `0x8000_0000` | — |
| `[1, 0]` | `qemu_uart_0` (NS16550), `sys_clint` (Dual-Hart IPI), `sys_plic` | QEMU-compatible IO | `0x1100_0000`, `0x0200_0000`, `0x0C00_0000` | `10` |
| `[1, 0]` | `spi_ctrl_0` (`user.spi_ctrl` — `rtl/spi_ctrl_rtl.sv`) | **Verilator 5.032 SystemVerilog RTL** (32-bit SPI Master) | `0x1900_0000` | `16` |
| `[1, 0]` | `i2c_ctrl_0` (`user.i2c_ctrl` — `rtl/i2c_ctrl_rtl.sv`) | **Verilator 5.032 SystemVerilog RTL** (I2C Master/Slave FSM) | `0x1C00_0000` | `18` |
| `[0, 1]` | `chipyard_gcd_0` (`chipyard.gcd`) | Chipyard TLM-2.0 contract | `0x1300_0000` | `12` |
| `[0, 1]` | `opentitan_gpio_0` (`opentitan.gpio`) | OpenTitan TL-UL TLM-2.0 contract | `0x1400_0000` | `13` |
| `[0, 1]` | `dma_engine_0` (`user.dma_engine`) | SystemC 3rd NoC Bus Master (Master 2) | `0x1700_0000` | `14` |
| `[0, 1]` | `sha256_crypto_0` (`user.sha256_crypto` — `rtl/sha256_rtl.sv`) | **Verilator 5.032 SystemVerilog RTL** (FIPS 180-4 64-round) | `0x1800_0000` | `15` |
| `[1, 1]` | `mac_npu_0` (`user.mac_npu` — `rtl/mac_npu_rtl.sv`) | **Verilator 5.032 SystemVerilog RTL** (MAC + 2×2 MatMul) | `0x1500_0000` | `20` |
| `[1, 1]` | `aes128_crypto_0` (`user.aes128_crypto` — `rtl/aes128_rtl.sv`) | **Verilator 5.032 SystemVerilog RTL** (FIPS 197 10-round) | `0x1B00_0000` | `17` |
| `[1, 1]` | `pmp_guard_0` (`user.pmp_guard`) | SystemC NoC IOPMP Bus Guard (CPU & DMA) | `0x1600_0000` | `21` |

## Commands & Enterprise Features

```bash
# 1. Build & run Dual-Hart SMP + 6x Verilator RTL + IR NoC + VCD + 24-bin coverage + GDB RSP
usp unified-soc run specs/multiplatform/unified_hetero_soc.yaml --out results/unified-soc

# 2. Run 6x RTL-vs-TLM parity & cycle calibration on the exact same SMP RV64 ELF
usp unified-soc calibrate specs/multiplatform/unified_hetero_soc.yaml --out results/unified-cal

# 3. Scaffold a new IEEE 1685-2022 + SV RTL + SystemC TLM-2.0 IP plugin & publish to registry
usp ip scaffold xor_accel --out results/xor_accel
usp registry publish results/xor_accel/package.yaml --registry results/reg --version 1.0.0
usp registry verify --registry results/reg

# 4. Run CHIA-style multi-core/Ray parallel DSE sweep & cyclic optimization loop (isolated oracle)
usp dse sweep specs/multiplatform/unified_hetero_soc.yaml --out results/dse --workers 2 --cluster process --cache results/dse-cache
usp loop run specs/multiplatform/unified_hetero_soc.yaml --out results/chia-loop --max-iters 4 --cache results/loop-cache

# 5. Render zero-CDN air-gapped HTML5 Verification, Coverage, DSE & CHIA Convergence Dashboard
usp dashboard generate --out results/dashboard.html \
  --unified results/unified-soc/report.json \
  --calibration results/unified-cal/calibration.json \
  --dse results/dse/dse-report.json \
  --loop results/chia-loop/loop-report.json
```

## Honest Fidelity Disclosure

- In `verilator-rtl` mode (`fidelity: hybrid-systemc-tlm2-smp-and-6x-verilator-rtl-annotated-noc`),
  `mac_npu_0`, `sha256_crypto_0`, `aes128_crypto_0`, `spi_ctrl_0`, `i2c_ctrl_0`, and `l2_cache_0`
  execute real Verilator-compiled clean-room SystemVerilog RTL cycle-by-cycle inside the SystemC
  3.0.1 top-level alongside Dual-Hart SMP RV64IMA_Zicsr cores, an IR-generated NoC routing table
  (`unified_soc_routes.h`), IEEE 1364 VCD waveform dump (`unified-soc.vcd`), and 24-bin functional
  coverage (`coverage.json`), while the CPU/NoC/GCD/GPIO/DMA/PMP models remain SystemC TLM-2.0
  behavioral models (distinct from Verilated upstream Chipyard Rocket/Gemmini RTL, OpenTitan
  EarlGrey RTL, or live gem5 Garnet).


