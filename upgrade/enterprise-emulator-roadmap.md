# Enterprise Multi-Platform Emulator & CHIA-Comparison Upgrade Reference

Baseline recorded: 2026-11-22 on branch `dev` (`f98f7bf`, package `0.3.0a5`, Podman SDK `0.3.0a5-r1`).
This document captures the enterprise readiness assessment, direct comparison with CHIA
(`arXiv:2606.27350` / `ref/notes/chia-2606.27350.md`), and the executable upgrade milestones
**E1–E4** required to move from a controlled engineering pilot toward an extensible,
collaborative, multi-platform SoC emulator platform.

## 1. Baseline Snapshot (`f98f7bf`)

- Milestone ledger (`usp upgrade --root . --audit`):
  - `v0.3.0` (M1–M4): `11 implemented`, `1 open`, `0 verified` (`ready_for_final_v0_3_0: false`).
  - `v0.4.0` (M5–M7): `1 implemented`, `8 open`.
  - `v1.0.0` (M8–M9): `0 implemented`, `6 open`.
- Inventory coverage (`src/socsim/ip_catalog.py`):
  - `AI_SOC` (46 instances): `14` qualified, `22` pinned-source-needs-adapter, `10` missing-source-implementation.
  - `AIZ_SOC` (37 instances): `13` qualified, `18` pinned-source-needs-adapter, `6` missing-source-implementation.
- Verified capabilities at baseline:
  - Rootless Podman SDK (`44721541157`): 38/38 passed before and after OCI `podman load`.
  - Single unified SystemC/TLM-2.0 2×2 NoC emulator (`unified_hetero_soc.yaml`) booting real RV64 SW.
  - Zero-dependency MCP JSON-RPC 2.0 stdio server (`usp mcp`) exposing 15 real tools.
- Observed baseline CI gaps:
  - `SoC regression` (`regression` job on minimal Debian without `libsystemc-dev`) failed 3 new SystemC tests that checked `SOCSIM_REQUIRE_INTEGRATION=1` instead of `USP_REQUIRE_SYSTEMC=1`.
  - `gem5-se` job lacked `python3-dev` (`python3-config`) for SCons.

## 2. Six Enterprise Gaps Identified

1. **IP Library Depth (M8):** Pinned Chipyard/OpenTitan IPs (Gemmini, Saturn, Inclusive Cache, AES/HMAC/OTBN/SPI/I2C/USB) and DDR/DMA/PHY controllers still require explicit adapters.
2. **Hybrid Fidelity Co-Simulation (M5–M6):** Need real Verilator-compiled SystemVerilog RTL co-simulating cycle-by-cycle on the same SystemC/TLM-2.0 NoC alongside fast TLM-2.0 models and multi-master DMA.
3. **Timing Calibration Against RTL (M7):** Need reproducible cycle-error calibration comparing TLM-2.0 annotated latencies against real Verilator RTL cycle counts on shared workloads.
4. **Firmware Debug & Multi-Master SW Flows:** Need GDB Remote Serial Protocol (RSP) debug attach and real multi-master DMA + PMP Bus Guard fault software verification.
5. **Enterprise Team Collaboration (Colab):** Need a versioned, SHA-256-locked IP Plugin Registry (`usp registry`) so hardware/NPU/security/firmware teams publish and compose versioned IP packages independently.
6. **Parallel DSE & Cluster Orchestration (CHIA-Style):** Need multi-worker parallel sweep/DSE execution (`usp dse`) with tamper-proof caching, retry/profiling telemetry, and training/holdout calibration checks.

## 3. Direct Comparison with CHIA (`ucb-bar/chia`, `docs.chialoops.ai`)

| Dimension | CHIA (`arXiv:2606.27350`) | USP / IP-XACT Emulator | Strategic Synergy |
|---|---|---|---|
| Core abstraction | Agentic/programmatic cyclic workflow graph + Ray cluster | IEEE 1685-2022 IP-XACT / YAML → Canonical IR → QEMU/gem5/SystemC/Verilator + BSP | Complementary layers |
| Multi-platform IP standardization | Node wrappers mainly for Chipyard/FireSim/gem5/Hammer | Unified IP-XACT/YAML map, strict VLNV/SHA-256 contracts, SystemC TLM-2.0 NoC | USP stronger on standardized multi-vendor SoC composition |
| Scale & calibration | Ray cluster, 202-iter BOOM↔gem5 alignment, FireSim SPEC06 | Single-host + Podman SDK; adding parallel DSE + Verilator↔TLM calibration (E2/E4) | Adopt CHIA's parallel workers & calibration discipline |
| AI integration | Built-in agentic loop edges + isolated verification oracle | Open MCP JSON-RPC 2.0 stdio server (`usp mcp`) for any AI client | Combine MCP openness with isolated tamper-proof verification |

## 4. Executable Upgrade Milestones (E1–E12)

| Milestone | Deliverable | Acceptance Gate | Status |
|---|---|---|---|
| **E1** | Separate `USP_REQUIRE_SYSTEMC=1` from QEMU-only `SOCSIM_REQUIRE_INTEGRATION=1`; add `python3-dev` to `gem5-se` | Both `SoC regression` (minimal Debian) and `Podman SDK and OCI release` pass 100% | **Verified on `main` (`df563f6`, PR #1)** |
| **E2** | Hybrid SystemC/TLM-2.0 + Verilator 5.032 SystemVerilog RTL (`mac_npu_rtl.sv`) + 2nd-master DMA Engine (`DmaEngineTlm`, IRQ 14) + GDB RSP stub (`--gdb-port`) + RTL↔TLM cycle calibration inside `Unified_Hetero_MultiPlatform_SoC` | Real RV64 ELF exercises UART/CLINT/PLIC, PMP CSR, Chipyard GCD, OpenTitan GPIO, Verilated RTL MAC NPU, NoC DMA transfer + IRQ, PMP Bus Guard blocking unauthorized CPU & DMA writes, GDB RSP query, and calibration report in Rootless Podman | **Verified on `main` (`df563f6`, PR #1)** |
| **E3** | Versioned, SHA-256-locked **IP Plugin Registry** (`src/socsim/ip_registry.py`, `usp registry`, MCP tools) for multi-team IP publishing & composition | Publish `user.mac_npu@1.0.0` & `user.counter@1.0.0`, resolve lockfile, reject tampered archive, verify offline in Podman | **Verified on `main` (`df563f6`, PR #1)** |
| **E4** | **CHIA-style Parallel DSE & Calibration Orchestrator** (`src/socsim/dse_cluster.py`, `usp dse`, MCP tool) running multi-worker sweeps with shared `ArtifactCache`, profiling, and RTL↔TLM calibration metrics | Parallel multi-config NoC sweep on `Unified_Hetero_MultiPlatform_SoC` with cache replay check and verified `dse-report.json` / `dse-report.md`, followed by green CI promotion `dev` → `main` | **Verified on `main` (`df563f6`, PR #1)** |
| **E5** | **4× Co-Simulated Verilator 5.032 Synthesizable RTL Blocks** (`mac_npu_rtl.sv`, `sha256_rtl.sv`, `spi_ctrl_rtl.sv`, `l2_cache_rtl.sv`) across 14 mapped IPs and 7 PLIC IRQs | All 4 RTL modules compile with Verilator 5.032 and link cleanly into `unified-soc-emu` | **Verified on `main` (`687c179`, PR #2)** |
| **E6** | **Industrial Verification Suite, IEEE 1364 VCD & 20-Bin Functional Coverage** (`unified_sw.c`, `--vcd-out`, `coverage.json`, 4-RTL↔TLM parity calibration) | 14/14 UART markers, `coverage_percent == 100.0`, valid `unified-soc.vcd`, 4-RTL↔TLM calibration PASS | **Verified on `main` (`687c179`, PR #2)** |
| **E7** | **Multi-Core/Ray Pluggable DSE Cluster, Semver Ranges & Zero-CDN HTML5 Dashboard** (`--cluster thread\|process\|ray`, `^`/`~`/`>=,<`, `usp dashboard generate`, 18 MCP tools) | Process DSE sweep + Ray gate + semver resolution + zero-CDN HTML5 dashboard verified in Podman | **Verified on `main` (`687c179`, PR #2)** |
| **E8** | **Full End-to-End CI Promotion (`dev` → `main`)** | Post-merge `Podman SDK and OCI release` (`45111151634`) and `SoC regression` (`45111152266`) green on `main` | **Verified on `main` (`687c179`, PR #2)** |
| **E9** | **Dual-Hart SMP RV64IMA_Zicsr + 6× Verilator 5.032 RTL + IR-Generated NoC Routing Table + Backpressure** (Hart 0 + Hart 1 with CLINT `msip[1]` IPI + RV64A `amoadd.w`/`lr.w`/`sc.w` atomics; added FIPS 197 `aes128_rtl.sv` IRQ 17 & `i2c_ctrl_rtl.sv` IRQ 18; `unified_soc_routes.h` on actual routing path; 16 IPs, 9 PLIC IRQs, 3 masters, 24-bin coverage) | All 6 Verilator RTL blocks + Dual-Hart SMP firmware pass 18/18 UART markers, `24/24` coverage (`100%`), GDB RSP, and 6-RTL↔TLM calibration in Rootless Podman | **Implemented (verifying on CI)** |
| **E10** | **CHIA-Style Cyclic HW/SW Optimization Loop + Isolated Verification Oracle + Inline SVG Dashboard** (`src/socsim/chia_loop.py`, `usp loop run`, `pareto.json`, SVG convergence chart in `dashboard.py`, MCP tool `emulator_run_chia_loop`) | Cyclic NoC router-placement & latency optimization verifies immutable SW oracle + 100% coverage on every iteration, achieves measurable speedup, replays cache, and renders air-gapped SVG chart | **Implemented (verifying on CI)** |
| **E11** | **Enterprise IP Plugin Scaffolder** (`src/socsim/ip_scaffold.py`, `usp ip scaffold`, MCP tool `emulator_scaffold_ip` — total 20 MCP tools) | Scaffolds valid IEEE 1685-2022 IP-XACT XML + SV RTL + SystemC TLM-2.0 + `selftest.c` + hash-pinned `package.yaml` that compiles via `usp package build` and publishes to `usp registry` in Rootless Podman | **Implemented (verifying on CI)** |
| **E12** | **Aggregate Local + Hosted Podman/Regression CI Verification & Clean PR Promotion (`dev` → `main`)** | Both `Podman SDK and OCI release` and `SoC regression` pass 100% on `dev` and post-merge `main` | **In progress** |

## 5. Honest Remaining Scope Toward Full `v1.0.0` Production Signoff

Even after **E1–E12**, the platform transparently distinguishes what is verified today from what remains open on the long-horizon `v1.0.0` roadmap (`docs/roadmap.md`):

- **Clean-room vs. Upstream RTL:** The 6 Verilated SystemVerilog RTL blocks (`mac_npu_rtl.sv`, `sha256_rtl.sv`, `aes128_rtl.sv`, `spi_ctrl_rtl.sv`, `i2c_ctrl_rtl.sv`, `l2_cache_rtl.sv`) are clean-room synthesizable RTL models co-simulated with SystemC 3.0.1 — distinct from full upstream Chipyard Rocket/BOOM/Gemmini RTL, full OpenTitan EarlGrey RTL, or live gem5 Garnet.
- **Full `AI_SOC` (46) / `AIZ_SOC` (37) Catalog (M8):** Remaining pinned-source and missing-source inventory items in `src/socsim/ip_catalog.py` still require dedicated upstream adapters and full-chip Linux/Yocto bringup.
- **Real Multi-Node Ray Cluster & Release Tags (M9):** `--cluster ray` executes real Ray tasks when `ray` is installed on a multi-node cluster (and fails explicitly with `E_CLUSTER_UNAVAILABLE` when absent), while GitHub-hosted single-VM CI exercises `--cluster process` and `--cluster thread`; final `v0.3.0`/`v0.4.0`/`v1.0.0` release tags are held until full M4/M7/M9 signoff gates close.


