# ASoC Official Example Guide & Verified Simulation Report

| Metadata Field | Value |
|---|---|
| **Document ID** | `SOCSIM-DOC-EX-ASOC` |
| **Current Version** | `0.3.0a5` |
| **Status** | Active / CI-Verified (`Podman SDK and OCI release`) |
| **Normative Authority** | `examples/ASoC/asoc_unified.yaml`, `examples/ASoC/asoc_fabric.yaml`, `tests/test_asoc_example.py`, `containers/smoke.sh` |

---

## Document Revision History

| Version | Release Date | Milestone / Gate | Author / Scope | Detailed Summary of Changes |
|---|---|---|---|---|
| `0.3.0a5` | `2026-11-23` | Official `ASoC` Example | Architecture & Verification | Added official `ASoC` documentation guide, white-box multi-cluster execution walkthrough, and verified live simulation report structure. |

---

## 1. Overview & Files

The official **ASoC** example lives in [`examples/ASoC/`](../../examples/ASoC/README.md) and demonstrates a production-style **20-IP, 2-CPU-Cluster (4-Hart), 4-Tier Hierarchical Bus + Coherent 2×2 Mesh NoC** RISC-V64 platform with **NPU**, **LPU**, **Dual-Layer PMP/IOPMP**, **Cross-Cluster Hardware Mailbox**, **AI Scratchpad SRAM**, **5th-Master DMA**, and **7× Verilator 5.032 RTL blocks**:

| Artifact | Purpose |
|---|---|
| [`examples/ASoC/asoc_unified.yaml`](../../examples/ASoC/asoc_unified.yaml) | 20-IP, 2-cluster/4-hart, 4-tier bus+bridge SystemC + 7× Verilator RTL specification |
| [`examples/ASoC/asoc_fabric.yaml`](../../examples/ASoC/asoc_fabric.yaml) | `native-fabric-1` 5-space, 4-bridge static address-decoder specification for IEEE 1685-2022 XML & QEMU/gem5 lowering |
| [`examples/ASoC/asoc_flow.yaml`](../../examples/ASoC/asoc_flow.yaml) | `native-flow-1` 4-hart SMP lifecycle manifest (`baremetal` + `linux`) |
| [`specs/multiplatform/unified_systemc/rtl/lpu_rtl.sv`](../../specs/multiplatform/unified_systemc/rtl/lpu_rtl.sv) | Clean-room synthesizable LPU RTL (4-lane Linear+Bias+ReLU + 4-token Attention Argmax) |
| [`tests/test_asoc_example.py`](../../tests/test_asoc_example.py) | Automated unit + full live SystemC/Verilator/Calibration/DSE/CHIA-Loop/Dashboard regression suite |

---

## 2. White-Box Multi-Cluster HW/SW Execution Walkthrough

1. **Cluster 0 Boot & Security Init (`hart0` @ `[0,0]`):**
   - Initializes primary `uart_console_0` across `apb_periph_bridge`, verifies monotonic `sys_clint` timer, configures CPU-internal RISC-V PMP CSRs (`pmpaddr0`/`pmpcfg0 = 0x1F`), and validates IEEE 802.3 CRC-32 compute ROI (`0xCBF43926`).
2. **Intra-Cluster & Cross-Cluster SMP Synchronization (`hart0..hart3`):**
   - `hart0` wakes Cluster 0 `hart1` (`msip[1]`), then wakes Cluster 1 `hart2` (`msip[2]`) and `hart3` (`msip[3]`) across the 2×2 NoC.
   - All 4 harts execute concurrent RV64A `amoadd.w` and `lr.w`/`sc.w` atomic barriers reaching exact count `48`, plus parallel CRC-32 checks across both clusters.
3. **Cross-Cluster Hardware Mailbox IPC (`cluster_mailbox_0`):**
   - Cluster 0 (`hart0`) writes AI job token `0xA50C0001` into `cluster_mailbox_0` behind `apb_periph_bridge`; Cluster 1 (`hart2`) consumes it, responds with `0xA50C1111`, and fires doorbell PLIC IRQ `23` back to Cluster 0.
4. **AI Compute Domain (`axi_ai_bridge`, `+4 ns`):**
   - **NPU (`mac_npu_0`, Verilator RTL):** Scalar dot product (`126`) + 2×2 MatMul (`[[19,22],[43,50]]`) + PLIC IRQ `20`.
   - **LPU (`lpu_engine_0`, Verilator RTL):** 4-lane INT32 Linear+Bias+ReLU (`+46` and negative clamp `0`) + 4-token Attention Argmax (`argmax_idx=1`, `max_logit=485`) + PLIC IRQ `22`.
   - **Cross-Bridge DMA (`dma_engine_0` → `axi_ai_bridge` → `ai_scratch_sram`):** 16-byte DRAM-to-DRAM and 16-byte DRAM-to-AI-Scratchpad-SRAM transfers + PLIC IRQ `14`.
   - **Chipyard GCD (`chipyard_gcd_0`):** 3 Euclidean vectors + PLIC IRQ `12`.
5. **Security Domain (`axi_sec_bridge`, `+6 ns`):**
   - **FIPS 180-4 SHA-256 RTL** (`SHA256("abc")` KAT + PLIC IRQ `15`), **FIPS 197 AES-128 RTL** (NIST Appendix B KAT + PLIC IRQ `17`), **OpenTitan GPIO** (IRQ `13`), and **IOPMP Bus Firewall (`pmp_guard_0`, IRQ `21`)** blocking unauthorized writes from BOTH CPU (`Store Access Fault` mcause `7`) AND DMA Master!
6. **Peripheral & Coherency Verification (`apb_periph_bridge`, `+8 ns` + `l2_cache_0`):**
   - **SPI 32-bit RTL** (`0xA55A1234` loopback + IRQ `16`), **I2C Master/Slave FSM RTL** (`0x50`/`0xC3` + IRQ `18`), **Secondary Telemetry UART (`uart_debug_1`)**, and **64-line L2 Cache Snoop RTL** (`hits > 0`, `misses > 0`, `dma_snoops >= 8`).
7. **30-Bin Functional Coverage, VCD, GDB RSP, Calibration & CHIA Loop:**
   - Captures `unified-soc.vcd`, verifies `30/30` (100.0%) coverage bins, runs live TCP GDB RSP single-step + continue, enforces negative guest failure detection (`fail_sw.elf`), validates 7× RTL vs TLM bit-exact parity, and optimizes NoC placement via `usp loop run`.

---

## 3. Verified Live Execution Reports (Podman SDK CI Run `45151702324`)

All numbers below are measured verbatim from GitHub Actions `Podman SDK and OCI release` run [`45151702324`](https://github.com/Duy-Huan/IP-XACT/actions/runs/45151702324) on commit `2fa29af` inside the offline Rootless Podman SDK (`c++ 14.2.0`, `SystemC 3.0.0 / libsystemc-dev 3.0.1-1`, `Verilator 5.032 2025-01-01 rev`, `riscv64-unknown-elf-gcc 14.2.0`).

Official exported reports live under `examples/ASoC/reports/`:

- [`examples/ASoC/reports/asoc-simulation-report.md`](../../examples/ASoC/reports/asoc-simulation-report.md)
- [`examples/ASoC/reports/asoc-console.log`](../../examples/ASoC/reports/asoc-console.log)
- [`examples/ASoC/reports/asoc-calibration-report.json`](../../examples/ASoC/reports/asoc-calibration-report.json)
- [`examples/ASoC/reports/asoc-dse-report.md`](../../examples/ASoC/reports/asoc-dse-report.md)
- [`examples/ASoC/reports/asoc-chia-loop-report.md`](../../examples/ASoC/reports/asoc-chia-loop-report.md)

### 3.1 Multi-Cluster SMP, 4-Tier Bus & 7× Verilator RTL Simulation (`asoc_unified.yaml`)

- **Status:** **PASSED** | **Functional Coverage:** **`100.0%` (`30/30` bins)** | **Console Markers:** **`25/25` PASS**
- **GDB RSP Live Check:** **PASSED** | **Negative Guest Failure (`fail_sw.elf`):** **ENFORCED** | **IEEE 1364 VCD:** **CAPTURED**
- **Instructions Executed:** **`22,539`** (`hart0=9,573`, `hart1=4,218`, `hart2=4,765`, `hart3=3,983`)
- **SMP Synchronization:** `67` RV64A atomic ops, `3` CLINT IPIs (`msip[1..3]`), `25` traps/IRQs, `1` hardware mailbox IPC message (`19` MMIO tx)
- **SystemC Simulated Time:** **`396,369,000 ps` (`396.369 us`)**
- **2×2 Mesh NoC & 4-Tier Bridge Traffic:** `26,871` total tx, `19,736` hops, `11,237` backpressure stalls, `18` DMA master tx (`20` IR routes):
  - `noc_backbone` (Tier 0): **`25,507`** tx
  - `axi_ai_bridge` (Tier 1, `+4 ns`): **`83`** tx (`mac_npu_0=31`, `lpu_engine_0=28`, `ai_scratch_sram=12`, `chipyard_gcd_0=12`)
  - `axi_sec_bridge` (Tier 2, `+6 ns`): **`62`** tx (`sha256_crypto_0=29`, `aes128_crypto_0=15`, `opentitan_gpio_0=11`, `pmp_guard_0=7`, `2` blocked writes)
  - `apb_periph_bridge` (Tier 3, `+8 ns`): **`1,219`** tx (`uart_console_0=1,069`, `uart_debug_1=56`, `sys_plic=55`, `cluster_mailbox_0=19`, `sys_clint=8`, `i2c_ctrl_0=7`, `spi_ctrl_0=5`)

| Verilator 5.032 RTL Block | Source File | Measured RTL Cycles | Verified Hardware Output |
|---|---|---:|---|
| `mac_npu_0` (NPU) | `rtl/mac_npu_rtl.sv` | `37` | Scalar dot `= 126`, 2×2 MatMul `= [[19,22],[43,50]]`, IRQ `20` |
| `lpu_engine_0` (LPU) | `rtl/lpu_rtl.sv` | `33` | Linear+ReLU `+46` & clamp `0`, Argmax idx `= 1` (`max_logit=485`), IRQ `22` |
| `sha256_crypto_0` | `rtl/sha256_rtl.sv` | `95` | NIST FIPS 180-4 `SHA256("abc")` exact KAT, IRQ `15` |
| `aes128_crypto_0` | `rtl/aes128_rtl.sv` | `26` | NIST FIPS 197 Appendix B exact KAT, IRQ `17` |
| `spi_ctrl_0` | `rtl/spi_ctrl_rtl.sv` | `38` | 32-bit shift loopback `0xA55A1234`, IRQ `16` |
| `i2c_ctrl_0` | `rtl/i2c_ctrl_rtl.sv` | `28` | Master+Slave FSM addr `0x50`, data `0xC3`, IRQ `18` |
| `l2_cache_0` | `rtl/l2_cache_rtl.sv` | `25,489` | `hits=25,230`, `misses=242` (`99.05%` hit rate), `dma_snoops=13` |

### 3.2 7× Verilator RTL vs. SystemC TLM-2.0 Cycle Calibration

- **Status:** **PASSED** (`functional_parity: true`, `within_budget: true`, `silicon_signoff: false`)
- **RTL Hybrid Time:** `396,369,000 ps` (`19,818.45` cycles @ 20 ns/cycle)
- **TLM Behavioral Time:** `396,260,000 ps` (`19,813.00` cycles @ 20 ns/cycle)
- **Relative Timing Error:** **`0.000275` (`0.0275%`)** vs. budget `<= 10.0%` (`0.1`)

### 3.3 Parallel DSE Sweep (`process`, `workers=2`) & CHIA 4-Iteration Optimization Loop

| Point / Iteration | Hop / Link (ns) | Backend | Status / Oracle | Sim Time (ps) | NoC Hops | Speedup |
|---|---|---|---:|---:|---:|---:|
| DSE `noc_fast_tlm` (`train`) | `5 / 2` | `systemc-tlm` | **PASSED** | `323,194,000` | `18,996` | — |
| DSE `noc_nom_tlm` (`train`) | `10 / 5` | `systemc-tlm` | **PASSED** | `396,260,000` | `19,731` | — |
| DSE `noc_nom_rtl` (`train`) | `10 / 5` | `verilator-rtl` | **PASSED** | `396,369,000` | `19,736` | — |
| DSE `noc_slow_tlm` (`holdout`) | `20 / 8` | `systemc-tlm` | **PASSED** | `495,504,000` | `19,971` | — |
| CHIA Iter 1 `baseline_distributed_2x2` | `10 / 5` | `systemc-tlm` | **PASS** | `396,260,000` | `19,731` | `1.0000×` |
| CHIA Iter 2 `co_locate_crypto_and_npu_near_hart0` | `10 / 5` | `systemc-tlm` | **PASS** | `395,359,000` | `19,611` | `1.0023×` |
| CHIA Iter 3 `co_locate_accelerators_and_tune_noc_8ns` | `8 / 4` | `systemc-tlm` | **PASS** | `368,270,000` | `19,294` | `1.0760×` |
| CHIA Iter 4 `pareto_low_latency_6ns_noc_balanced` | `6 / 3` | `systemc-tlm` | **PASS** | `343,053,000` | `19,076` | **`1.1551×`** |

### 3.4 IEEE 1685-2022 Fabric Lowering, Flow Preflight & HTML5 Dashboard

- `usp soc generate examples/ASoC/asoc_fabric.yaml --input-format fabric --engine both`: **GENERATED**, `20` instances, `ir_sha256=b324b7ee319165ee4f3a3897635ca3874c84e6b4062932542ec0c8ae83058e52`.
- `usp flow preflight examples/ASoC/asoc_flow.yaml`: **READY**, `harts=4`, `workloads=[baremetal, linux]`, `blocking_issues=[]`.
- `usp dashboard generate`: **PASSED**, `coverage_percent=100.0`, `sha256=049e0264f8b24af12964ed089b48bff623d69f4973c421550ed6ead13fb8429f`.
- See [`examples/ASoC/README.md`](../../examples/ASoC/README.md) for all reproducible CLI commands and fidelity boundaries.
