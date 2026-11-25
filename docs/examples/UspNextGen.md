# UspNextGen Official Showcase Guide & Verified Simulation Report

| Metadata Field | Value |
|---|---|
| **Document ID** | `SOCSIM-DOC-EX-NEXTGEN` |
| **Current Version** | `0.3.0a5` |
| **Status** | Active / Verified (`Podman SDK and OCI release` + `SoC regression`) |
| **Normative Authority** | `examples/UspNextGen/usp_nextgen_soc.yaml`, `upgrade/usp-v2-core-upgrades.md`, `src/socsim/multiplatform_composer.py`, `src/socsim/rtl_pin_adapter.py`, `tests/test_usp_v2_upgrades.py` |

---

## Document Revision History

| Version | Release Date | Milestone / Gate | Author / Scope | Detailed Summary of Changes |
|---|---|---|---|---|
| `0.3.0a5` | `2026-11-24` | USP Core Upgrade Batch (3 Upgrades) | Multi-Platform Composer, Protocol & RTL Adapters | Added official `UspNextGen` guide, white-box walkthrough of QEMU Direct CPU Master + `usp-bus-v2` Reverse DMA/Burst64/Quantum Sync + Pin-Level AXI4/AXI4-Lite/TL-UL RTL adapters, and verbatim measured reports. |

---

## 1. Overview & Artifacts

The official **UspNextGen** showcase lives in [`examples/UspNextGen/`](../../examples/UspNextGen/README.md) and validates all three USP v2 core upgrades running concurrently in one heterogeneous SoC (`multiplatform-soc-1`, detailed in [`upgrade/usp-v2-core-upgrades.md`](../../upgrade/usp-v2-core-upgrades.md)):

| Artifact | Purpose |
|---|---|
| [`examples/UspNextGen/usp_nextgen_soc.yaml`](../../examples/UspNextGen/usp_nextgen_soc.yaml) | 12-IP, 2-clock-domain (`core_clk 800 MHz` ↔ `periph_clk 250 MHz`), 3-crossbar SoC spec with `cpu.master_engine: qemu` and `quantum_ns: 100000` |
| [`examples/UspNextGen/rtl/chipyard_gcd_axi4_rtl.sv`](../../examples/UspNextGen/rtl/chipyard_gcd_axi4_rtl.sv) | Chipyard/`firtool`-flattened AXI4 Full 64-bit Euclidean GCD FSM + 32×64-bit INCR burst SRAM (auto-adapted via `VerilatorAxi4FullAdapter`) |
| [`examples/UspNextGen/rtl/axi4_lite_crc32_rtl.sv`](../../examples/UspNextGen/rtl/axi4_lite_crc32_rtl.sv) | Standard AMBA AXI4-Lite 32-bit IEEE 802.3 CRC-32 accelerator (auto-adapted via `VerilatorAxi4LiteAdapter`) |
| [`examples/UspNextGen/rtl/opentitan_gpio_tlul_rtl.sv`](../../examples/UspNextGen/rtl/opentitan_gpio_tlul_rtl.sv) | OpenTitan TL-UL 32-bit GPIO & edge-IRQ controller with D-channel backpressure verification (auto-adapted via `VerilatorTlulAdapter`) |
| [`examples/UspNextGen/packages/dma_burst_v2/package.yaml`](../../examples/UspNextGen/packages/dma_burst_v2/package.yaml) | Standalone `native-model-package-1` (`usp-bus-v2`) live socket plugin implementing reverse bus-master DMA, 64-bit bursts, quantum sync, and wire-level `usp-mmio32-v1` backward compatibility |
| [`examples/BSoC/packages/polyphase_12t/package.yaml`](../../examples/BSoC/packages/polyphase_12t/package.yaml) | Unmodified legacy `usp-mmio32-v1` plugin executing side-by-side to prove 100% backward compatibility |
| [`examples/UspNextGen/sw/usp_nextgen_sw.c`](../../examples/UspNextGen/sw/usp_nextgen_sw.c) | Bare-metal RV64 positive verification firmware (>2.5M-instruction compute loop + all pin-level RTL + v2 DMA + v1 compat) |
| [`examples/UspNextGen/sw/usp_nextgen_fail_sw.c`](../../examples/UspNextGen/sw/usp_nextgen_fail_sw.c) | Negative verification firmware proving pin-level AXI4-Lite `SLVERR` propagates through Verilator → SystemC → `usp-bus-v2` → QEMU guest trap |
| [`tests/test_usp_v2_upgrades.py`](../../tests/test_usp_v2_upgrades.py) | 11-test unit, contract, package qualification, and live QEMU/SystemC/Verilator integration suite |

---

## 2. White-Box End-to-End Execution Walkthrough

1. **QEMU Direct CPU Master & Periodic Quantum Sync (`NEXTGEN_QEMU_WORKLOAD_OK`):**
   - Stock `qemu-system-riscv64` boots `_usp_qemu_entry` in PMP-isolated M-mode `BRIDGE_RAM`, initializes VirtIO-MMIO v2 Console (`AF_UNIX` `usp-bus-v2`), configures 3-entry TOR PMP granting guest S-mode native `R|W|X` speed on zero-copy `mmap` RAM (`[0x80000000, 0x80800000)`), and jumps to `_start`.
   - Guest firmware executes a 300,000-iteration 64-bit compute workload natively in QEMU TCG RAM while periodic M-mode CLINT timer interrupts emit `USP_OP_TIME_SYNC` barriers carrying real `minstret` counts across QEMU ↔ SystemC ↔ `dma_burst_v2_0`, followed by an explicit guest `usp_bus_time_sync(50000)` hypercall.
2. **Chipyard / `firtool` Pin-Level AXI4 Full 64-bit RTL (`NEXTGEN_AXI4_FULL_GCD_BURST_OK`):**
   - `rtl_pin_adapter.py` auto-detects `io_axi_aw_*`/`w_*`/`b_*`/`ar_*`/`r_*` (64-bit, active-high `reset`) and binds `VerilatorAxi4FullAdapter<Vchipyard_gcd_axi4_rtl, Traits_chipyard_gcd_0>` directly without any MMIO32 wrapper.
   - Firmware computes `gcd(1071, 462) = 21` via the hardware FSM (completing via `sync_quantum` + PLIC IRQ #2) and issues an 8-beat × 64-bit (`64 B`) AXI4 INCR burst write/read verifying both beat parity and hardware `BURST_CHECKSUM`.
3. **AMBA Pin-Level AXI4-Lite 32-bit RTL across Async CDC (`NEXTGEN_AXI4_LITE_CRC32_OK`):**
   - Crossing `periph_cdc_bridge_0` (`core_clk 800 MHz` ↔ `periph_clk 250 MHz`), firmware feeds `"123456789"` into `axi4_lite_crc32_0` (`VerilatorAxi4LiteAdapter`), receives PLIC IRQ #4, and verifies exact IEEE 802.3 CRC-32 `0xCBF43926`.
4. **OpenTitan Pin-Level TL-UL 32-bit RTL (`NEXTGEN_OPENTITAN_TLUL_GPIO_OK`):**
   - `VerilatorTlulAdapter` exercises OpenTitan `Get`/`PutFullData` A/D valid-ready handshakes with a mandatory 1-cycle `tl_d_ready_i = 0` backpressure check on every transaction, confirming rising-edge GPIO PLIC IRQ #5 and loopback parity (`0x00A50055`).
5. **Live `usp-bus-v2` Reverse Bus-Master DMA + 64-bit Burst + Quantum Sync (`NEXTGEN_USP_BUS_V2_DMA_BURST_SYNC_OK`):**
   - Firmware performs a 16-beat × 64-bit (`128 B`) burst write/read on `dma_burst_v2_0` scratchpad, primes L2$ RTL lines, then triggers autonomous reverse DMA (`128 B` from `0x80100000` → transform `(w ^ 0xCAFEBABE12345678) + 1` → `0x80100400`).
   - Out-of-process `dma_burst_v2_0` sends `USP_OP_DMA_READ` & `USP_OP_DMA_WRITE` back through `MultiFabricInterconnect` (triggering real Verilator `l2_cache_rtl` snoops!) directly into QEMU's zero-copy shared `mmap` RAM, raises PLIC IRQ #3, and firmware validates every transformed 64-bit word directly in RAM.
6. **Concurrent Legacy `usp-mmio32-v1` Compatibility & Negative `SLVERR` Check (`NEXTGEN_USP_MMIO32_V1_COMPAT_OK`, `NEXTGEN_L2_SNOOP_OK`, `USP_NEXTGEN_PASS`):**
   - Unmodified `bsoc.polyphase_12t` (`usp-mmio32-v1`) runs concurrently in the same SoC (`tap_ck=1024`, `out=1`), Verilator `l2_cache_512k` confirms `hits > 0`, `misses > 0`, `snoops > 0`, and `usp_nextgen_fail_sw.c` verifies pin-level AXI4-Lite `SLVERR` cleanly faults the guest (`status == FAILED`, `rc != 0`).

---

## 3. Verified Live Execution Reports

Verbatim measured outputs and exported simulation artifacts are archived in [`examples/UspNextGen/reports/`](../../examples/UspNextGen/reports/), captured directly from hosted GitHub Actions CI on commit `2d47bbff8be5bdf696f011dcc7635cdcde2627cb` inside the pinned rootless Podman OCI image `socsim-sdk:0.3.0a5-r1` (`qemu-system-riscv64 10.0.13` TCG `-icount shift=3,sleep=off`, `SystemC 3.0.1`, `Verilator 5.032`, `riscv64-unknown-elf-gcc 14.2.0`):

- **Hosted `Podman SDK and OCI release` CI:** Run [`45547546572`](https://github.com/Duy-Huan/IP-XACT/actions/runs/45547546572) — **PASSED** (`27m51s`, both pre-export and post-`podman load` offline smoke runs green; `87 passed in 331.10s`, `0 failed`, `0 skipped`).
- **Hosted `SoC regression` CI:** Run [`45547546604`](https://github.com/Duy-Huan/IP-XACT/actions/runs/45547546604) — **PASSED** (`workflow-lint`, `branch-policy`, `regression`, `custom-zephyr` in `14m1s`, `hosted-receipt`).

### 3.1 Measured Verification Scorecard (Hosted Podman SDK Run `45547546572`)

| Verification Suite / Benchmark | Status | Functional Coverage | Guest Insns Retired | Wall Time (ms) | Wall-Time Throughput (MIPS)* | Sim Time (ps) | Quantum Syncs (`100 µs`) | Bus Tx / CDC | Key Upgrade Metrics |
|---|---|---|---:|---:|---:|---:|---:|---|---|
| **`UspNextGen`** (`--cpu-master qemu --qemu-accel tcg`) | **PASSED** | **100.0%** (`21/21`) | `3,493,318` | `442.3` | `7.898` | `27,933,801,000` | `272` (max drift `461.75 µs`) | `1,570` / `1,257` | `16×` 64-bit bursts (`960 B`), `256 B` plugin reverse DMA, `L2$`: `300H/4M/32S`, `SLVERR` neg-test `PASSED` |
| **`BSoC`** unmodified (`--cpu-master qemu --qemu-accel tcg`) | **PASSED** | **100.0%** (`19/19`) | `492,990` | `372.32` | `1.324` | `3,909,501,000` | `8` (max drift `1,362.12 µs`) | `17,127` / `4,003` | All 22 devices, 5× Verilator RTL, `2,432` AXI4-Stream pkts, `6` PLIC IRQs, live `polyphase_12t` v1 plugin |
| **`usp.dma_burst_v2`** (`usp package build` standalone qual) | **PASSED** | 13/13 vectors + v1 compat | — | — | — | — | `1` (`100 ms` barrier) | — | `v1_backward_compat_reset: true`, `4×64b` burst R/W, live reverse DMA `64 B` (`1R/1W`) |
| **Podman SDK full live pytest** (`USP_REQUIRE_SYSTEMC=1`) | **PASSED** | **87/87 tests** (incl. 11 USP v2) | — | `331,100` | — | — | — | — | QEMU, QA, Unified SoC, ASoC, BSoC, USP v2, Registry/DSE, MCP, Standardization all green |

> \* **Honest Measurement Disclosure:** Wall-time MIPS reflects host wall-clock co-simulation throughput (including SystemC/Verilator RTL evaluation and Unix-socket RPC) under deterministic QEMU TCG `-icount shift=3,sleep=off` (`8 ns`/guest instruction = `125 MHz` virtual clock), with M-mode VirtIO bridge spin overhead excluded from `minstret` and virtual-clock accounting. It is **not** cycle-accurate CPU pipeline IPC.

### 3.2 Pin-Level Verilator RTL Handshake & Cycle Breakdown (`UspNextGen`)

| Device Instance | Detected Pin Protocol | Runtime Adapter | Bus Hits | Pin Handshakes | Verilator RTL Cycles | Functional Check |
|---|---|---|---:|---:|---:|---|
| `chipyard_gcd_0` | `axi4` (64-bit, `io_axi_*`) | `VerilatorAxi4FullAdapter` | `8` | `35` | `17,402` | `gcd(1071, 462) = 21` + `8×64b` INCR burst parity & checksum |
| `axi4_lite_crc32_0` | `axi4-lite` (32-bit, `s_axi_*`) | `VerilatorAxi4LiteAdapter` | `8` | `22` | `17,435` | IEEE 802.3 CRC-32(`"123456789"`) = `0xCBF43926` + negative `SLVERR` fault |
| `opentitan_gpio_0` | `tl-ul` (32-bit, `tl_*`) | `VerilatorTlulAdapter` | `7` | `14` | `17,439` | Loopback `0x00A50055` + rising-edge PLIC IRQ + 1-cycle `d_ready=0` check |
| `l2_cache_512k` | `mmio32` (legacy) | `VerilatorMmio32Adapter` | `3` | `0` | `340` | `hits=300`, `misses=4`, `dma_snoops=32` (reverse DMA coherency) |

### 3.3 Exported Verbatim Report Artifacts

| Artifact File | Contents |
|---|---|
| [`usp-nextgen-simulation-report.md`](../../examples/UspNextGen/reports/usp-nextgen-simulation-report.md) | Complete verbatim Markdown report (`multiplatform-soc-1`) for `UspNextGen` from CI run `45547546572` |
| [`usp-nextgen-report.json`](../../examples/UspNextGen/reports/usp-nextgen-report.json) | Machine-readable JSON report with SHA-256 provenance, coverage bins, and bus/RTL counters |
| [`usp-nextgen-console.log`](../../examples/UspNextGen/reports/usp-nextgen-console.log) | Verbatim RV64 UART console log (`USP_NEXTGEN_PASS`) |
| [`usp-nextgen-backend-traces.log`](../../examples/UspNextGen/reports/usp-nextgen-backend-traces.log) | Verilator 5.032 build summaries, `usp-bus-v2` reverse-DMA/quantum frames, and `usp-mmio32-v1` compat frames |
| [`usp-bus-v2-qualification.json`](../../examples/UspNextGen/reports/usp-bus-v2-qualification.json) | Standalone `usp package build` qualification evidence for `usp.dma_burst_v2` |
| [`bsoc-qemu-master-report.md`](../../examples/UspNextGen/reports/bsoc-qemu-master-report.md) | Full verbatim Markdown report proving unmodified 22-device `BSoC` achieves `100.0%` coverage under `--cpu-master qemu` |
| [`bsoc-qemu-master-console.log`](../../examples/UspNextGen/reports/bsoc-qemu-master-console.log) | Verbatim UART console output for `BSoC` under QEMU CPU Master |

