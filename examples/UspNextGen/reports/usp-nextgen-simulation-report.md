# Multi-Platform Composed SoC Report (UspNextGen)

- **Contract:** `multiplatform-soc-1`
- **Status:** **PASSED**
- **Execution Mode:** `qemu-tcg-cpu-master-systemc-verilator-and-usp-bus-v2`
- **CPU Master Engine:** `qemu` (`accel=tcg`)
- **Functional Coverage:** **100.0%** (`21/21` bins)
- **Timestamp (UTC):** `2026-11-25T04:42:03.792951+00:00`

## System-Level Multi-Clock, Quantum Sync & Interconnect Metrics

- Instructions retired/executed: `3493318` | Traps/IRQs: `4`
- Wall-clock run time (ms): `442.3` | Wall-time emulation throughput (MIPS, host wall clock): `7.898`
- SystemC simulated time (ps): `27933801000` | Quantum (`ns`): `100000` | Quantum sync barriers: `272` | Max time drift (ps): `461752000`
- Total bus transactions: `1570` | Async CDC crossings: `1257`
- 64-bit Burst transactions: `16` (`960` bytes) | Plugin Reverse Bus-Master DMA bytes: `256`
- SoC DMA transferred bytes: `0` | JTAG-to-AXI4 host transactions: `0`
- L2 Cache: `hits=300`, `misses=4`, `dma_snoops=32`

### Fabric Traffic

| Fabric / Crossbar | Transactions |
|---|---:|
| `tilelink_xbar` | `294` |
| `axi4_full_xbar` | `19` |
| `tlul_periph_xbar` | `1257` |

### Bus Master Traffic

| Master | Transactions |
|---|---:|
| `qemu_rv64_cpu_0` | `1568` |
| `dma_burst_v2_0_dma` | `2` |

### Heterogeneous Device Bindings, Pin-Level Handshakes & Verilator RTL Cycles

| Device Instance | Runtime Backend | Bus Hits | Pin Handshakes | Verilator RTL Cycles |
|---|---|---:|---:|---:|
| `boot_rom` | `systemc-tlm` | `0` | `0` | `0` |
| `sim_finisher` | `systemc-tlm` | `1` | `0` | `0` |
| `sys_clint` | `systemc-tlm` | `0` | `0` | `0` |
| `sys_plic` | `systemc-tlm` | `14` | `0` | `0` |
| `main_sram_8m` | `systemc-tlm` | `276` | `0` | `0` |
| `l2_cache_512k` | `verilator-rtl` | `3` | `0` | `340` |
| `chipyard_gcd_0` | `verilator-axi4-full` | `8` | `35` | `17402` |
| `dma_burst_v2_0` | `usp-bus-v2-live-plugin` | `11` | `0` | `0` |
| `uart_console_0` | `systemc-tlm` | `1237` | `0` | `0` |
| `axi4_lite_crc32_0` | `verilator-axi4-lite` | `8` | `22` | `17435` |
| `opentitan_gpio_0` | `verilator-tl-ul` | `7` | `14` | `17439` |
| `polyphase_v1_0` | `usp-mmio32-v1-live-plugin` | `5` | `0` | `0` |
