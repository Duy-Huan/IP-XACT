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

## 3. Verified Execution Reports

See [`examples/ASoC/README.md`](../../examples/ASoC/README.md) for all reproducible CLI commands. Live measured Podman CI simulation metrics are recorded below upon CI completion.
