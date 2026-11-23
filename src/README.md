# Source Code Architecture Index (`src/socsim/`)

| Metadata Field | Value |
|---|---|
| **Document ID** | `SOCSIM-SRC-001` |
| **Current Version** | `0.3.0a5` |
| **Status** | Active / White-Box Subsystem Map |
| **Normative Authority** | `pyproject.toml`, `src/socsim/cli.py`, `mypy --check-untyped-defs src/socsim` |

---

## Document Revision History

| Version | Release Date | Milestone / Gate | Author / Scope | Summary of Changes |
|---|---|---|---|---|
| `0.3.0a5` | `2026-11-23` | Refactoring Gate | Codebase Hygiene | Created subsystem index mapping all Python modules in `src/socsim/` to their architectural layers and CLI entry points without breaking existing import paths. |

---

## Subsystem Organization

`src/socsim/` exposes a single top-level package (`socsim`) with two CLI aliases (`usp` and `socsim`, bound to [`socsim.cli:main`](socsim/cli.py)). Modules are grouped into seven cohesive layers:

### 1. CLI, Project Intake & Validation Frontend
| Module | Responsibility |
|---|---|
| [`cli.py`](socsim/cli.py) | Master command dispatcher routing 30+ subcommands (`usp <cmd>`) |
| [`lifecycle_cli.py`](socsim/lifecycle_cli.py) & [`lifecycle_preflight.py`](socsim/lifecycle_preflight.py) | Custom QEMU/Zephyr lifecycle CLI and preflight checks |
| [`ipxact.py`](socsim/ipxact.py) & [`native_ipxact.py`](socsim/native_ipxact.py) | Offline IEEE 1685-2022 XSD validator (thread-safe serialized init) and strict XML parser |
| [`domain.py`](socsim/domain.py), [`project.py`](socsim/project.py), [`validation.py`](socsim/validation.py) | Immutable domain dataclasses (`ContractError`, `Window`, `SoCIR`), manifest loading |
| [`parameters.py`](socsim/parameters.py), [`registers.py`](socsim/registers.py), [`hierarchy.py`](socsim/hierarchy.py) | Bounded unsigned integer elaboration, scalar register/field checks, structural hierarchy |
| [`version.py`](socsim/version.py) & [`release.py`](socsim/release.py) | Release version identity (`0.3.0a5`) and reproducible release bundle checks |

### 2. Canonical Native IR, Bus Graph & Flow Engine (`usp soc`, `usp flow`, `usp domains`)
| Module | Responsibility |
|---|---|
| [`native_ir.py`](socsim/native_ir.py) & [`soc_spec.py`](socsim/soc_spec.py) | Canonical immutable Native IR (v3–v6) schema and SoC YAML spec loader |
| [`native_fabric.py`](socsim/native_fabric.py), [`native_bus_graph.py`](socsim/native_bus_graph.py), [`native_bus_xml.py`](socsim/native_bus_xml.py), [`native_bus_export.py`](socsim/native_bus_export.py) | Local address spaces, bridge apertures, static decoder lowering, 2022 bus XML import/export |
| [`native_domains.py`](socsim/native_domains.py) & [`native_cpu_binding.py`](socsim/native_cpu_binding.py) | Clock/reset domain derivation, per-initiator address views, explicit CPU binding |
| [`native_flow.py`](socsim/native_flow.py) & [`native_bsp_gen.py`](socsim/native_bsp_gen.py) | End-to-end `native-flow-1` orchestrator and deterministic `soc.h`/`soc.ld` BSP generator |
| [`soc_native.py`](socsim/soc_native.py), [`soc_native_codegen.py`](socsim/soc_native_codegen.py), [`soc_native_gem5.py`](socsim/soc_native_gem5.py), [`soc_native_linux.py`](socsim/soc_native_linux.py) | Arbitrary-instance native SoC generator for concurrent QEMU + gem5 execution |
| [`native_package.py`](socsim/native_package.py) & [`native_external.py`](socsim/native_external.py) | `native-model-package-1` SHA-256 verification and `usp-mmio32-v1` Unix-socket RPC builder |

### 3. Single Unified Multi-Platform SystemC/TLM-2.0 + Verilator RTL Emulator (`usp unified-soc`)
| Module | Responsibility |
|---|---|
| [`unified_systemc_soc.py`](socsim/unified_systemc_soc.py) | Compiles & executes the single unified `sc_main` binary: Dual-Hart SMP `RV64IMA_Zicsr`, IR-generated `unified_soc_routes.h` 2×2 NoC, 6× Verilator 5.032 clean-room SV RTL IPs (`MAC NPU`, `SHA-256`, `AES-128`, `SPI`, `I2C`, `L2 Cache`), DMA master, IOPMP guard, GDB RSP, IEEE 1364 VCD dump, 24-bin coverage, and RTL-vs-TLM cycle calibration |
| [`multiplatform_composer.py`](socsim/multiplatform_composer.py) | Generic data-driven `multiplatform-soc-1` composer (`usp compose`): multi-clock CDC, multi-crossbar/multi-master fabrics, AXI4-Stream DAG, live `usp-mmio32-v1` Unix-socket plugins, Verilator RTL, and synchronized IP-XACT / QEMU / gem5 / Chipyard exports |

### 4. Enterprise Collaboration, DSE Cluster, CHIA Loop & Visualization
| Module | Responsibility |
|---|---|
| [`ip_registry.py`](socsim/ip_registry.py) | Versioned, SHA-256-locked IP model registry with SemVer range resolution & tamper checks |
| [`ip_scaffold.py`](socsim/ip_scaffold.py) | One-command IP plugin scaffolder (`usp ip scaffold`) generating IP-XACT 2022 XML, SV RTL, SystemC TLM-2.0 MMIO32, `selftest.c`, and `package.yaml` |
| [`dse_cluster.py`](socsim/dse_cluster.py) | Parallel multi-core (`thread`/`process`) and optional Ray DSE sweep engine with content-addressed `ArtifactCache` |
| [`chia_loop.py`](socsim/chia_loop.py) | CHIA-style cyclic HW/SW optimization loop (`usp loop run`) optimizing IR NoC router placements/latencies under an isolated verification oracle with SVG convergence plots |
| [`dashboard.py`](socsim/dashboard.py) | Zero-CDN air-gapped HTML5 Verification, 24-Bin Coverage, RTL/TLM Calibration, DSE & CHIA Convergence Dashboard |

### 5. AI Control Plane, SDK, QA & Governance (`usp mcp`, `usp qa`, `usp upgrade`)
| Module | Responsibility |
|---|---|
| [`mcp_server.py`](socsim/mcp_server.py) | Zero-dependency Model Context Protocol (MCP) JSON-RPC 2.0 `stdio` server exposing 23 tools, 5 resources, and 3 prompts with workspace sandboxing |
| [`sdk.py`](socsim/sdk.py) | Python `EmulatorNode` SDK supporting local and rootless Podman execution (`run_in_podman()`) |
| [`qa_runner.py`](socsim/qa_runner.py) & [`multiplatform_qa.py`](socsim/multiplatform_qa.py) | Real-execution QA/QC suites (`quick`, `unseen`, `multiplatform`, `wheel`, `full`) and SHA-256 scorecards |
| [`upgrade.py`](socsim/upgrade.py), [`readiness_audit.py`](socsim/readiness_audit.py), [`hosted_ci.py`](socsim/hosted_ci.py) | M1–M9 milestone ledger validator, readiness auditor, and Git-note CI receipt reader |
| [`contracts.py`](socsim/contracts.py), [`v04_contracts.py`](socsim/v04_contracts.py), [`ip_catalog.py`](socsim/ip_catalog.py) | Qualification matrices, CPU/bus/NoC contracts, and 33-entry pinned IP readiness catalog |

### 6. Multi-Engine Simulators, RTL CPUs, NoC & Physical Reference Flow
| Module | Responsibility |
|---|---|
| [`backends.py`](socsim/backends.py), [`pipeline.py`](socsim/pipeline.py), [`runner.py`](socsim/runner.py), [`execution.py`](socsim/execution.py), [`execution_plan.py`](socsim/execution_plan.py), [`log_capture.py`](socsim/log_capture.py) | Backend resolution, QEMU machine generation, process-group-safe execution, and evidence capture |
| [`gem5.py`](socsim/gem5.py), [`unified.py`](socsim/unified.py), [`fabric.py`](socsim/fabric.py), [`noc.py`](socsim/noc.py), [`coherent.py`](socsim/coherent.py), [`coherent_build.py`](socsim/coherent_build.py) | gem5 SE/FS adapters, live Ruby/Garnet mesh, and two-hart coherent DMA |
| [`hybrid.py`](socsim/hybrid.py), [`ipblocks.py`](socsim/ipblocks.py), [`ipblocks_codegen.py`](socsim/ipblocks_codegen.py) | SystemC/Verilator OpenTitan FIFO/GPIO and Chipyard Chisel GCD co-simulation |
| [`cpu_platform.py`](socsim/cpu_platform.py), [`rtl_cpu.py`](socsim/rtl_cpu.py), [`rtl_cpu_build.py`](socsim/rtl_cpu_build.py), [`rtl_platform.py`](socsim/rtl_platform.py), [`native_rtl.py`](socsim/native_rtl.py), [`native_rtl_engine.py`](socsim/native_rtl_engine.py) | RTL CPU master adapters (Ibex / custom RTL) over QEMU and gem5 memory engines |
| [`rocket_cpu.py`](socsim/rocket_cpu.py), [`rocket_build.py`](socsim/rocket_build.py), [`rocket_elaboration.py`](socsim/rocket_elaboration.py), [`rocket_linux.py`](socsim/rocket_linux.py), [`rocket_software.py`](socsim/rocket_software.py), [`rocket_zephyr.py`](socsim/rocket_zephyr.py) | Source-built Chipyard `RocketTile` AXI CPU elaboration, build, and OS bring-up |
| [`testchip.py`](socsim/testchip.py), [`synthesis.py`](socsim/synthesis.py), [`asic.py`](socsim/asic.py) | CPU-owned RTL test chips, Yosys/Slang synthesis, and OpenROAD Nangate45 GDS flow |

### 7. OS/Firmware BSPs, Performance & Multi-SoC Workspaces
| Module | Responsibility |
|---|---|
| [`freertos.py`](socsim/freertos.py), [`ucos.py`](socsim/ucos.py), [`native_ucos.py`](socsim/native_ucos.py), [`rtl_os.py`](socsim/rtl_os.py), [`rtl_zephyr.py`](socsim/rtl_zephyr.py), [`linux.py`](socsim/linux.py), [`yocto.py`](socsim/yocto.py), [`native_software.py`](socsim/native_software.py) | Source-pinned FreeRTOS, Zephyr, µC/OS-II/III, OpenSBI/Linux, and Yocto layer adapters |
| [`layers.py`](socsim/layers.py), [`performance.py`](socsim/performance.py), [`native_performance.py`](socsim/native_performance.py) | Software integration matrix and native/estimated CPI/IPC counters |
| [`systems.py`](socsim/systems.py), [`workspace.py`](socsim/workspace.py), [`legacy_cache.py`](socsim/legacy_cache.py) | Multi-SoC workspace orchestration (`AI_SOC`, `AIZ_SOC`) and verified cache reuse |

### 8. Bundled Static Assets
- [`src/socsim/schemas/`](socsim/schemas/) — Offline Accellera IEEE 1685-2022 XSD files (with Apache-2.0 provenance & SHA-256 checksums) and JSON schemas (`ir-v2.json`, `hardware-v1.json`, `model-catalog-v1.json`).
- [`src/socsim/templates/`](socsim/templates/) — Parameterized QEMU C machine, linker, DTS, and firmware templates.
- [`src/socsim/resources/`](socsim/resources/) — Board support resources for FreeRTOS, Zephyr, µC/OS, Rocket, unified SoC, coherent DMA, and ASIC reference flows.
