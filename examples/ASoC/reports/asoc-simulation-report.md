# Unified Multi-Platform SystemC/TLM-2.0 + 7× Verilator RTL Report (ASoC)

> **Provenance:** Captured verbatim from GitHub Actions `Podman SDK and OCI release` run `45151702324` (`dev` @ `2fa29afcad80d2cd02db39035d1d4093d191ed5e`) inside the offline Rootless Podman SDK (`c++ 14.2.0`, `SystemC 3.0.0 / libsystemc-dev 3.0.1-1`, `Verilator 5.032 2025-01-01 rev`, `riscv64-unknown-elf-gcc 14.2.0`).
> **Fidelity Disclosure:** The 7 Verilator-compiled blocks execute clean-room synthesizable SystemVerilog RTL cycle-by-cycle; the 4 RV64IMA_Zicsr harts, 2×2 Mesh NoC + 3 hierarchical bridges, DMA, IOPMP firewall, mailbox, Chipyard GCD, and OpenTitan GPIO execute as SystemC/TLM-2.0 behavioral models (`silicon_signoff: false`).

- **Contract:** `unified-systemc-soc-1`
- **Status:** **PASSED**
- **Fidelity:** `hybrid-systemc-tlm2-multicluster-smp-and-7x-verilator-rtl-annotated-noc`
- **CPU Topology:** `2` cluster(s) / `4` RV64IMA_Zicsr hart(s)
- **RTL Backend:** `verilator-rtl`
- **Functional Coverage:** **100.0%** (`30/30` bins)
- **Timestamp (UTC):** `2026-11-23T10:04:01.562216+00:00`

## Multi-Cluster SMP & 7× Verilator RTL Metrics (`unified_sw.elf`)

- Instructions executed: `22539` (hart0=`9573`, hart1=`4218`, hart2=`4765`, hart3=`3983`)
- RV64A Atomic ops / CLINT IPIs: `67` / `3`
- Traps / IRQs taken: `25`
- SystemC simulated time (ps): `396369000`
- NoC 2×2 tx / hops / backpressure stalls: `26871` / `19736` / `11237`
- NoC Router Hits: `R(0,0)=25489`, `R(1,0)=1219`, `R(0,1)=80`, `R(1,1)=83`
- IR routing table entries: `20` | DMA master tx: `18`
- Bus Bridge Traffic: `noc_backbone=25507`, `axi_ai_bridge=83`, `axi_sec_bridge=62`, `apb_periph_bridge=1219`
- Verilator RTL cycles: `mac_npu=37`, `lpu=33`, `sha256=95`, `aes128=26`, `spi=38`, `i2c=28`, `l2_cache=25489`
- L2 Cache: `hits=25230`, `misses=242`, `dma_snoops=13` (99.05% hit rate)

| Mapped IP | Transactions |
|---|---:|
| `qemu_uart_0` (`uart_console_0`) | `1069` |
| `uart_debug_1` | `56` |
| `sys_clint` | `8` |
| `sys_plic` | `55` |
| `chipyard_gcd_0` | `12` |
| `opentitan_gpio_0` | `11` |
| `dma_engine_0` | `18` |
| `sha256_crypto_0` | `29` |
| `aes128_crypto_0` | `15` |
| `spi_ctrl_0` | `5` |
| `i2c_ctrl_0` | `7` |
| `l2_cache_0` | `3` |
| `mac_npu_0` | `31` |
| `lpu_engine_0` | `28` |
| `cluster_mailbox_0` | `19` |
| `ai_scratch_sram` | `12` |
| `pmp_guard_0` | `7` |
| `pmp_guard_blocked_writes` | `2` |

## Cryptographic Artifact Provenance (CI Run `45151702324`)

| Artifact | SHA-256 |
|---|---|
| `examples/ASoC/asoc_unified.yaml` | `d2338207fa8ce31dcf4f8032a83accd2e9572e5257e2b5cb1d73b0a37e716477` |
| `unified_systemc_soc.cpp` | `8b23db50a0255b821415146f9a68bf5e0194df2071de5240d4508b5b915d13ea` |
| `unified-soc-emu` | `ff69784fb9180bacdac10db55514c8f9ba66b88180d55e8e6b1e57817e41595d` |
| `unified_sw.elf` (positive) | `663b250d6e89c317417b68213d0b0b89c2563e77abac4abbb08e8f885e7a9c84` |
| `fail_sw.elf` (negative) | `59260a070bbcb16bce5da88c7cac82ae8dd244ea6f55ef2371d4f8e95a605f6b` |
| `console.log` (`25/25` markers) | `ba31a0d6c3d9dcf70a56af03e7ea49752bb6886c1ac8198f31cceffc5a0fb97a` |
| `noc-trace.jsonl` | `525971426c16a6a8f62cba53858b8e7016964ed849ec93cfefaa6cc6f1a78d58` |
| `unified-soc.vcd` | `7b4a89efd053f750abc7baaad73591cb2b330157043a9d328efe28a2131041f2` |
| `asoc_fabric.yaml` Native IR (`20` instances) | `b324b7ee319165ee4f3a3897635ca3874c84e6b4062932542ec0c8ae83058e52` |
| `asoc-dashboard.html` | `049e0264f8b24af12964ed089b48bff623d69f4973c421550ed6ead13fb8429f` |
