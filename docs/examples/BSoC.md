# BSoC Official Telecom Example Guide & Verified Simulation Report

| Metadata Field | Value |
|---|---|
| **Document ID** | `SOCSIM-DOC-EX-BSOC` |
| **Current Version** | `0.3.0a5` |
| **Status** | Active / CI-Verified (`Podman SDK and OCI release`) |
| **Normative Authority** | `examples/BSoC/bsoc_composed.yaml`, `src/socsim/multiplatform_composer.py`, `upgrade/bsoc-multiplatform-audit.md`, `tests/test_bsoc_and_composer.py` |

---

## Document Revision History

| Version | Release Date | Milestone / Gate | Author / Scope | Detailed Summary of Changes |
|---|---|---|---|---|
| `0.3.0a5` | `2026-11-23` | Official `BSoC` Telecom Example | Multi-Platform Composer & Verification | Added official `BSoC` documentation guide, white-box telecom DSP/Vector walkthrough, and verified CI report section. |

---

## 1. Overview & Artifacts

The official **BSoC** example lives in [`examples/BSoC/`](../../examples/BSoC/README.md) and validates the upgraded **generic multi-platform composition & cross-binding engine** (`usp compose` / `multiplatform-soc-1`) documented in [`upgrade/bsoc-multiplatform-audit.md`](../../upgrade/bsoc-multiplatform-audit.md):

| Artifact | Purpose |
|---|---|
| [`examples/BSoC/bsoc_composed.yaml`](../../examples/BSoC/bsoc_composed.yaml) | Complete 22-device, 2-clock-domain, 3-crossbar, 5-stage AXI4-Stream telecom SoC spec |
| [`examples/BSoC/rtl/hwacha_vec4_rtl.sv`](../../examples/BSoC/rtl/hwacha_vec4_rtl.sv) | 4-lane parallel Hwacha-style vector accelerator Verilator RTL |
| [`examples/BSoC/rtl/tuner_32ph_rtl.sv`](../../examples/BSoC/rtl/tuner_32ph_rtl.sv) | 32-phase quadrature NCO mixer Verilator RTL |
| [`examples/BSoC/rtl/fir_decim8_rtl.sv`](../../examples/BSoC/rtl/fir_decim8_rtl.sv) | 136-tap programmable FIR & `/8` decimator folded-MAC Verilator RTL |
| [`examples/BSoC/rtl/fft_128pt_rtl.sv`](../../examples/BSoC/rtl/fft_128pt_rtl.sv) | 128-point radix-2 Cooley-Tukey complex FFT Verilator RTL (704 cycles/frame) |
| [`examples/BSoC/packages/polyphase_12t/package.yaml`](../../examples/BSoC/packages/polyphase_12t/package.yaml) | Standalone reusable `native-model-package-1` (`usp-mmio32-v1`) live socket plugin |
| [`examples/BSoC/sw/bsoc_telecom_sw.c`](../../examples/BSoC/sw/bsoc_telecom_sw.c) | Bare-metal RV64 telecom end-to-end baseband verification workload |
| [`examples/BSoC/sw/bsoc_qemu_crosscheck.c`](../../examples/BSoC/sw/bsoc_qemu_crosscheck.c) | Shared RV64 fixed-point DSP math cross-engine check executed live on `qemu-system-riscv64` |
| [`tests/test_bsoc_and_composer.py`](../../tests/test_bsoc_and_composer.py) | Unit, negative validation, MCP, and full live Podman SDK integration regression suite |

---

## 2. White-Box End-to-End Telecom Baseband Execution Walkthrough

1. **Dual-Clock Bring-Up (`core_clk 800 MHz` ↔ `dsp_clk 250 MHz`):**
   - `rocket_cpu_0` initializes `uart_console_0` across the `mmio_cdc_mgr_0` async CDC bridge (`+12 ns` sync) and verifies monotonic `sys_clint` timebase.
2. **JTAG-to-AXI4 Stimulus Injection (`patgen_la_0` 512KB Pattern Gen):**
   - Loads a 1,024-sample complex IF carrier tone (`f_IF = 3/64 * f_s`, 48 cycles / 1,024 samples) into the 512 KiB Pattern Generator, with sample `0` injected by the independent **`jtag_to_axi4_0`** 3rd bus master directly over `axi4_data_xbar` + PLIC IRQ `12`.
3. **18 KiB TISAR ADC Calibration LUT & 5-Stage AXI4-Stream DSP Chain:**
   - Programs all 5 SCR banks over `axi4_ctrl_xbar` (Tuner `phase_step = 1` → `f_LO = 1/32 * f_s`; all **136 programmable FIR taps** & `/8` decimation; Polyphase bank select) and fires the 1,024-sample AXI4-Stream burst:
     - **Stage 1 (`tisar_adc_0`):** calibrates 1,024 samples through the real **18 KiB LUT** → `sam_adc_160k`.
     - **Stage 2 (`tuner_32ph_0` Verilator RTL):** mixes `3/64 * f_s` down to baseband `1/64 * f_s` (16 cycles / 1,024 samples) → `sam_tuner_256k`.
     - **Stage 3 (`fir_decim8_0` Verilator RTL):** runs 136-tap folded MAC every 8th sample (17,408+ RTL cycles) → 128 decimated samples in `sam_fir_64k`.
     - **Stage 4 (`polyphase_12t_0` LIVE `usp-mmio32-v1` Unix-Socket Plugin):** streams 128 samples over real OS socket IPC to the standalone `bsoc.polyphase_12t` SystemC process → `sam_poly_64k`.
     - **Stage 5 (`fft_128pt_0` Verilator RTL):** executes the 7-stage 128-point Radix-2 Cooley-Tukey FFT (704 RTL cycles), detects exact baseband tone peak (`bin 16` / conjugate `bin 112`, >23 dB SNR), writes spectrum to `sam_fft_64k` AND captures probe trace in the **512 KiB Logic Analyzer**, raising PLIC IRQ `19`.
4. **Cross-CDC DMA, Hwacha 4-Lane Vector RTL, JTAG Logic Analyzer & L2$ Coherency:**
   - `dma_accel_0` copies 512 bytes (128 FFT bins) from `sam_fft_64k` across `axi4_data_xbar` → `mmio_cdc_mgr_0` CDC → `tilelink_xbar` → `main_sram_8m` (`l2_cache_512k` records 128+ DMA coherency snoops!).
   - `hwacha_vec_0` (Verilator 4-lane RTL) computes 4-lane parallel spectral power `I² + Q²` on bins `14..17` and confirms >10× peak power dominance on bin `16` + PLIC IRQ `20`.
   - `jtag_to_axi4_0` reads back FFT bin `16` from the 512 KiB Logic Analyzer, `serial_adapter_0` sends the telemetry lock frame + PLIC IRQ `11`, and `qemu-system-riscv64` runs the live cross-engine RV64 DSP math check.

---

## 3. Verified Live Execution Reports

See [`examples/BSoC/README.md`](../../examples/BSoC/README.md) for all reproducible CLI commands. Live measured Podman SDK CI numbers and verbatim exported reports in `examples/BSoC/reports/` are populated immediately upon CI execution.
