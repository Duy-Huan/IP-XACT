# BSoC Telecom Stress-Test: Platform Bottleneck Audit & Multi-Platform Upgrade

| Metadata Field | Value |
|---|---|
| **Document ID** | `SOCSIM-UPG-BSOC-001` |
| **Current Version** | `0.3.0a5` |
| **Status** | Implemented & CI-Verified (`multiplatform-soc-1` + `examples/BSoC`) |
| **Reference Target** | Telecom Baseband / SDR SoC (`BSoC`: Rocket + Hwacha 4-Lane Vector + TileLink XBar + Dual-Clock CDC + AXI4 Ctrl/Data XBars + 5-Stage AXI4-Stream DSP Chain + TISAR ADC/18KB LUT + 5× SAMs + 512KB Pattern Gen/LA + JTAG-to-AXI4) |
| **Normative Authority** | `src/socsim/multiplatform_composer.py`, `src/socsim/resources/composer/composer_runtime.hpp`, `examples/BSoC/bsoc_composed.yaml`, `tests/test_bsoc_and_composer.py` |

---

## Document Revision History

| Version | Release Date | Milestone / Gate | Author / Scope | Detailed Summary of Changes |
|---|---|---|---|---|
| `0.3.0a5` | `2026-11-23` | `BSoC` Stress-Test & Composer Upgrade | Architecture & Verification | Conducted code-level bottleneck audit using the telecom BSoC diagram, then designed and verified the generic `multiplatform-soc-1` composition engine (`usp compose`) with multi-clock CDC, multi-crossbar/multi-master fabrics, AXI4-Stream DAG lowering, generic Verilator RTL template wrappers, and live out-of-process `usp-mmio32-v1` Unix-socket plugin binding inside SystemC/TLM-2.0. |

---

## 1. Real Bottlenecks & Limitations Exposed by BSoC (Before Upgrade)

Attempting to model and simulate a realistic telecom baseband SoC (**BSoC**: 64-bit Rocket + 4-lane Hwacha Vector + 2 clock domains + 3 crossbars + 3 bus masters + 5-stage AXI4-Stream DSP pipeline + mixed-signal ADC/LUT + 5× SAM buffers + Pattern Gen/LA) exposed **8 concrete architectural bottlenecks** in the pre-upgrade toolchain:

| # | Bottleneck Found in Existing Codebase | Concrete Evidence in Pre-Upgrade Source | Why It Blocked Real Multi-Platform SoCs |
|---:|---|---|---|
| **B1** | **Closed Hardcoded C++ Target Enum** | `specs/multiplatform/unified_systemc/unified_systemc_soc.cpp` + `src/socsim/unified_systemc_soc.py` (`UsocTargetId`, `USOC_HAS_*` `#ifdef` slots) | Adding any new domain IP (Hwacha Vector, 32-phase Tuner, 136-tap FIR, Polyphase Filter, 128-pt FFT, TISAR ADC) required modifying the monolithic C++ top-level instead of composing from data. |
| **B2** | **Disconnected `usp-mmio32-v1` Plugin ABI** | `src/socsim/native_package.py` + `src/socsim/ip_registry.py` vs. `unified_systemc_soc.cpp` | Compiled `native-model-package-1` IP binaries spoke `usp-mmio32-v1` for QEMU/gem5, but the SystemC emulator had NO `AF_UNIX` client proxy to bind those same binaries live without recompiling C++. |
| **B3** | **Zero `AXI4-Stream` Dataflow / SAM Tap / MUX Support** | `src/socsim/native_fabric.py`, `src/socsim/unified_systemc_soc.py` | Telecom/ISP/radar SoCs combine MMIO control buses with streaming sample pipelines (`AXI4-Stream`), per-stage Sample Acquisition Memories (SAMs), and Pattern Generator / Logic Analyzer MUXes. None could be declared, validated for acyclicity, or simulated. |
| **B4** | **Single Clock Domain & Single-Parent Bus Tree** | `src/socsim/native_fabric.py` (`resolve()` line 112 rejects multi-parent bridges); `unified_systemc_soc.py` (single `clock_hz` + hardcoded 2×2 mesh only) | Could not model `core_clk` (`800 MHz`) ↔ `dsp_clk` (`250 MHz`) async CDC crossings (`Memory-Mapped IO Manager`) or multiple independent masters (`JTAG-to-AXI4` + `MMIO Manager`) sharing `AXI4 Control Crossbar` and `AXI4 Data Crossbar`. |
| **B5** | **Rigid Hart Count (`{2, 4}`) & Hardcoded Single Firmware** | `src/socsim/unified_systemc_soc.py` (`harts in {2, 4}`, hardcoded `unified_sw.c`) | Rejected 1-hart Rocket+Hwacha configurations (`harts: 1`) and prevented each SoC example from bringing its own domain-specific verification firmware (e.g., RF tone downconversion & FFT verification). |
| **B6** | **Rigid `0x1000` Aperture & 16-Model Cap in Native Frontend** | `src/socsim/soc_spec.py` (line 146: `size != 0x1000`), `src/socsim/native_external.py` (line 15: `len <= 16`) | Real DSP SRAMs (`18KB` ADC LUT, `160KB`/`256KB`/`64KB` SAMs, `512KB` Pattern Gen/LA) and >16 custom IPs cannot fit fixed 4 KiB external windows. |
| **B7** | **Fragmented Cross-Ecosystem Lowering (Chipyard / gem5 / QEMU / RTL)** | `src/socsim/unified.py` (hardcoded `usp-rv64-ips-v1`, `cygcd`/`otgpio` only) | No single canonical IR and `binding-lock.json` that binds IPs from **Chipyard + gem5 + QEMU + Verilator RTL + `usp-plugin` + Mixed-Signal TLM** together AND emits synchronized IEEE 1685-2022 IP-XACT, QEMU DTS/crosscheck, gem5 `NativeSocDevice` script, and Chipyard Scala config. |
| **B8** | **No Generic Composer CLI / MCP Automation** | `src/socsim/cli.py`, `src/socsim/mcp_server.py` | Users and AI agents lacked `usp compose plan|run|calibrate|audit` and MCP tools to compose, audit, simulate, and calibrate arbitrary new multi-platform SoC topologies. |

---

## 2. Architectural Upgrades Implemented

All 8 bottlenecks above were systematically addressed via the new **data-driven Multi-Platform Composer (`multiplatform-soc-1`)** while maintaining **100% backward compatibility** with every existing `native-ir-v3`, `unified-systemc-soc-1`, `ASoC`, `AI_SOC`, and `AIZ_SOC` regression test:

1. **Data-Driven SystemC/TLM-2.0 Runtime (`src/socsim/resources/composer/composer_runtime.hpp`):**
   - Uses `tlm_utils::multi_passthrough_target_socket` & `multi_passthrough_initiator_socket` (`MultiFabricInterconnect`) to bind **arbitrary `M` bus masters** (`rocket_cpu_0`, `dma_accel_0`, `jtag_to_axi4_0`, `tisar_adc_0_stream`) and **arbitrary `N` target devices** across arbitrary crossbars and clock domains with automatic async CDC crossing latency and telemetry counters.
   - Adds **`UspMmio32ProxyTlm`**: live `AF_UNIX` `usp-mmio32-v1` socket client inside SystemC/TLM-2.0, enabling **zero-recompilation out-of-process IP plugin binding** — the **exact same compiled `native-model-package-1` binary** (`bsoc.polyphase_12t`) binds identically into SystemC, QEMU (`native_attach`), and gem5 (`NativeSocDevice`)!
   - Adds **`VerilatorMmioWrapper<VDut>`**: single generic C++ template binding **any** Verilator 5.032 synthesizable SystemVerilog RTL module (both combinational single-cycle and multi-cycle FSMs such as the 136-tap FIR and 704-cycle 128-point Radix-2 FFT!).
   - Adds **`TisarAdcStreamTlm`**: mixed-signal behavioral TISAR ADC + real 18 KiB calibration LUT SRAM + 5-stage **AXI4-Stream** pipeline driver populating all 5 SAM buffers and the 512 KiB Logic Analyzer.
2. **Zero-Branch Multi-Platform Compiler & Cross-Engine Lowering (`src/socsim/multiplatform_composer.py`):**
   - Validates `multiplatform-soc-1` (arbitrary 4 KiB-aligned apertures, `1..4` harts, multi-clock CDC, multi-crossbar/multi-master fabrics, **topological cycle detection** on `streams`, RTL/plugin provenance hashes).
   - Auto-generates `generated_soc_top.cpp`, `soc_map.h`, `link.ld`, `binding-lock.json`, `ipxact/design.xml` (IEEE 1685-2022), `qemu/<soc>.dts` (+ compiled `.dtb`), `gem5/<soc>_gem5_system.py` (AST-verified `NativeSocDevice` socket bindings), and `chipyard/<SoC>ChipyardConfig.scala`.
   - Executes live Hybrid (`5× Verilator RTL + 1× Live usp-mmio32-v1 Socket Plugin + Mixed-Signal TLM`) + live `qemu-system-riscv64` RV64 cross-engine check + Hybrid-vs-TLM bit-exact calibration (`usp compose calibrate`).
3. **CLI & MCP Exposure (`src/socsim/cli.py`, `src/socsim/mcp_server.py`):**
   - Added `usp compose plan|run|calibrate|audit` and MCP tools `emulator_compose_audit`, `emulator_compose_plan`, `emulator_compose_run`.

---

## 3. Remaining Honest Industry Boundaries (What Still Requires Upstream Tooling)

In strict compliance with `agent.md` §§3–4, we explicitly distinguish verified capabilities from remaining upstream boundaries:

| Capability Area | Current Verified Status | Remaining Upstream Requirement for Full Silicon-Grade Signoff |
|---|---|---|
| **Chipyard Rocket + Hwacha RTL** | Clean-room Verilator `hwacha_vec4_rtl.sv` + SystemC RV64IMAFD CSR initiator + generated `BSoCChipyardConfig.scala` | Elaborating upstream Chisel `RocketTile` + `Hwacha` Scala generators requires cached sbt/Mill + FIRRTL/CIRCT (`scripts/setup_chipyard.sh`). |
| **gem5 Timing Co-Simulation** | AST-verified `bsoc_gem5_system.py` binding `RiscvTimingSimpleCPU` + `512kB L2Cache` + `8MB SimpleMemory` + `NativeSocDevice` (`usp-mmio32-v1`) | Live gem5 timing execution requires compiling `gem5.opt` with `EXTRAS` (`scripts/setup_gem5.sh`, multi-hour C++ build outside fast smoke CI). |
| **QEMU Cross-Engine Execution** | Live `qemu-system-riscv64` boots & verifies shared RV64 DSP math guest + compiled `bsoc.dtb` | Running all 22 custom BSoC addresses directly inside a single QEMU binary requires building the custom QEMU machine binary (`scripts/setup_qemu.sh`). |
| **Analog Mixed-Signal (`TISAR ADC`)** | Behavioral mixed-signal TLM + real 18 KiB calibration LUT SRAM driving live AXI4-Stream samples | Transistor-level ADC noise, INL/DNL, and comparator metastability require SPICE/Verilog-AMS co-simulation (outside digital virtual-platform scope). |
