# Multi-Platform Composed SoC Report (BSoC)

- **Contract:** `multiplatform-soc-1`
- **Status:** **PASSED**
- **Execution Mode:** `qemu-tcg-cpu-master-systemc-verilator-and-usp-bus-v2`
- **CPU Master Engine:** `qemu` (`accel=tcg`)
- **Functional Coverage:** **100.0%** (`19/19` bins)
- **Timestamp (UTC):** `2026-11-25T04:42:27.511777+00:00`

## System-Level Multi-Clock, Quantum Sync & Interconnect Metrics

- Instructions retired/executed: `492990` | Traps/IRQs: `7`
- Wall-clock run time (ms): `372.32` | Wall-time emulation throughput (MIPS, host wall clock): `1.324`
- SystemC simulated time (ps): `3909501000` | Quantum (`ns`): `100000` | Quantum sync barriers: `8` | Max time drift (ps): `1362117000`
- Total bus transactions: `17127` | Async CDC crossings: `4003`
- 64-bit Burst transactions: `2` (`16` bytes) | Plugin Reverse Bus-Master DMA bytes: `0`
- SoC DMA transferred bytes: `512` | JTAG-to-AXI4 host transactions: `2`
- L2 Cache: `hits=7`, `misses=1`, `dma_snoops=128`
- AXI4-Stream Pipeline Packets: total=`2432` stages=`[1024, 1024, 128, 128, 128]`

### Fabric Traffic

| Fabric / Crossbar | Transactions |
|---|---:|
| `tilelink_xbar` | `189` |
| `axi4_ctrl_xbar` | `12199` |
| `axi4_data_xbar` | `4739` |

### Bus Master Traffic

| Master | Transactions |
|---|---:|
| `rocket_cpu_0` | `3936` |
| `dma_accel_0` | `256` |
| `jtag_to_axi4_0` | `2` |
| `tisar_adc_0_stream` | `12933` |

### Heterogeneous Device Bindings, Pin-Level Handshakes & Verilator RTL Cycles

| Device Instance | Runtime Backend | Bus Hits | Pin Handshakes | Verilator RTL Cycles |
|---|---|---:|---:|---:|
| `boot_rom` | `systemc-tlm` | `0` | `0` | `0` |
| `sim_finisher` | `systemc-tlm` | `1` | `0` | `0` |
| `sys_clint` | `systemc-tlm` | `2` | `0` | `0` |
| `sys_plic` | `systemc-tlm` | `22` | `0` | `0` |
| `main_sram_8m` | `systemc-tlm` | `136` | `0` | `0` |
| `l2_cache_512k` | `verilator-rtl` | `3` | `0` | `140` |
| `hwacha_vec_0` | `verilator-rtl` | `16` | `0` | `17` |
| `dma_accel_0` | `systemc-tlm` | `6` | `0` | `0` |
| `serial_adapter_0` | `systemc-tlm` | `3` | `0` | `0` |
| `uart_console_0` | `systemc-tlm` | `2551` | `0` | `0` |
| `jtag_to_axi4_0` | `systemc-tlm` | `8` | `0` | `0` |
| `tisar_adc_0` | `mixed-signal-tlm` | `7` | `0` | `0` |
| `tuner_32ph_0` | `verilator-rtl` | `4099` | `0` | `4100` |
| `fir_decim8_0` | `verilator-rtl` | `4628` | `0` | `22037` |
| `polyphase_12t_0` | `usp-mmio32-v1-live-plugin` | `516` | `0` | `0` |
| `fft_128pt_0` | `verilator-rtl` | `390` | `0` | `1095` |
| `sam_adc_160k` | `systemc-tlm` | `1024` | `0` | `0` |
| `sam_tuner_256k` | `systemc-tlm` | `1024` | `0` | `0` |
| `sam_fir_64k` | `systemc-tlm` | `128` | `0` | `0` |
| `sam_poly_64k` | `systemc-tlm` | `128` | `0` | `0` |
| `sam_fft_64k` | `systemc-tlm` | `257` | `0` | `0` |
| `patgen_la_0` | `systemc-tlm` | `2178` | `0` | `0` |
