<!-- Verbatim report from 100%-GREEN Podman SDK CI run 45194062413 (commit 36c1ed3) -->
# Multi-Platform Composed SoC Report (BSoC)

- **Contract:** `multiplatform-soc-1`
- **Status:** **PASSED**
- **Execution Mode:** `hybrid-systemc-verilator-and-live-usp-mmio32-plugins`
- **Functional Coverage:** **100.0%** (`18/18` bins)
- **Timestamp (UTC):** `2026-11-23T14:35:34.476055+00:00`

## System-Level Multi-Clock & Interconnect Metrics

- Instructions executed: `29131` | Traps/IRQs: `7`
- SystemC simulated time (ps): `1532856000`
- Total bus transactions: `50346` | Async CDC crossings (`core_clk <-> dsp_clk`): `4019`
- DMA transferred bytes: `512` | JTAG-to-AXI4 host transactions: `2`
- L2 Cache: `hits=32976`, `misses=235`, `dma_snoops=128`
- AXI4-Stream Pipeline Packets: total=`2432` stages=`[1024, 1024, 128, 128, 128]`

### Fabric Traffic

| Fabric / Crossbar | Transactions |
|---|---:|
| `tilelink_xbar` | `33392` |
| `axi4_ctrl_xbar` | `12215` |
| `axi4_data_xbar` | `4739` |

### Bus Master Traffic

| Master | Transactions |
|---|---:|
| `rocket_cpu_0` | `37155` |
| `dma_accel_0` | `256` |
| `jtag_to_axi4_0` | `2` |
| `tisar_adc_0_stream` | `12933` |

### Heterogeneous Device Bindings, Traffic & Verilator RTL Cycles

| Device Instance | Runtime Backend | Bus Hits | Verilator RTL Cycles |
|---|---|---:|---:|
| `boot_rom` | `systemc-tlm` | `0` | `0` |
| `sim_finisher` | `systemc-tlm` | `1` | `0` |
| `sys_clint` | `systemc-tlm` | `2` | `0` |
| `sys_plic` | `systemc-tlm` | `22` | `0` |
| `main_sram_8m` | `systemc-tlm` | `33339` | `0` |
| `l2_cache_512k` | `verilator-rtl` | `3` | `33343` |
| `hwacha_vec_0` | `verilator-rtl` | `16` | `17` |
| `dma_accel_0` | `systemc-tlm` | `6` | `0` |
| `serial_adapter_0` | `systemc-tlm` | `3` | `0` |
| `uart_console_0` | `systemc-tlm` | `2567` | `0` |
| `jtag_to_axi4_0` | `systemc-tlm` | `8` | `0` |
| `tisar_adc_0` | `mixed-signal-tlm` | `7` | `0` |
| `tuner_32ph_0` | `verilator-rtl` | `4099` | `4100` |
| `fir_decim8_0` | `verilator-rtl` | `4628` | `22037` |
| `polyphase_12t_0` | `usp-mmio32-v1-live-plugin` | `516` | `0` |
| `fft_128pt_0` | `verilator-rtl` | `390` | `1095` |
| `sam_adc_160k` | `systemc-tlm` | `1024` | `0` |
| `sam_tuner_256k` | `systemc-tlm` | `1024` | `0` |
| `sam_fir_64k` | `systemc-tlm` | `128` | `0` |
| `sam_poly_64k` | `systemc-tlm` | `128` | `0` |
| `sam_fft_64k` | `systemc-tlm` | `257` | `0` |
| `patgen_la_0` | `systemc-tlm` | `2178` | `0` |
