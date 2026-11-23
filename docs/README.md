# Documentation Hub (`docs/`)

| Metadata Field | Value |
|---|---|
| **Document ID** | `SOCSIM-DOC-000` |
| **Current Version** | `0.3.0a5` |
| **Status** | Active / Normative Index |
| **Normative Authority** | `scripts/check_docs.py`, `agent.md`, `CHANGELOG.md` |

---

## Document Revision History

| Version | Release Date | Milestone / Gate | Author / Scope | Summary of Changes |
|---|---|---|---|---|
| `0.3.0a5` | `2026-11-23` | Refactoring Gate | Documentation Hub | Created Diataxis-structured master index covering Tutorials, How-To Guides, Architecture/Explanation, and Technical Reference across all 50+ repository documents. |

---

## 1. Tutorials (Getting Started & End-to-End Walkthroughs)

| Document | Audience | Topic |
|---|---|---|
| [`setup-and-environment.md`](setup-and-environment.md) | All engineers | Complete Python `.venv`, `requirements*.txt`, QEMU, gem5, Chipyard, OpenTitan & Podman SDK setup |
| [`podman.md`](podman.md) | Workstation / KDE Plasma / CI | Building, verifying offline, exporting, and running the rootless Podman OCI SDK |
| [`creating-soc.md`](creating-soc.md) | SoC Architects | Authoring a new IP-XACT 2022 project and running validation/elaboration |
| [`examples/rv64-minimal.md`](examples/rv64-minimal.md) | Hardware/SW Bring-up | Generated custom QEMU machine + Zephyr RTOS CLINT/PLIC interrupt bring-up |
| [`examples/rv64-hybrid.md`](examples/rv64-hybrid.md) | SystemC / RTL Engineers | Connecting QEMU MMIO/IRQ to SystemC / Verilated OpenTitan FIFO RTL |
| [`examples/rv64-ips.md`](examples/rv64-ips.md) | Multi-Platform Engineers | Integrating source-built Chipyard Chisel GCD + OpenTitan GPIO/TL-UL |
| [`examples/rv64-hierarchy.md`](examples/rv64-hierarchy.md) | IP-XACT Authors | Bounded recursive structural IP-XACT hierarchy and `designConfiguration` views |

---

## 2. How-To Guides (Task-Oriented Engineering Playbooks)

| Document | Scope |
|---|---|
| [`unified-systemc-soc.md`](unified-systemc-soc.md) | Running the Single Unified Dual-Hart SMP + 6× Verilator RTL SystemC SoC, DSE, CHIA loop, IP scaffolder & HTML5 Dashboard |
| [`mcp-server.md`](mcp-server.md) | Connecting AI coding assistants (Claude, Cursor, VS Code, Podman) to the 20-tool MCP stdio server |
| [`native-flow.md`](native-flow.md) | Planning, preflighting, building, running, and verifying `native-flow-1` hardware/software pipelines |
| [`native-soc-generator.md`](native-soc-generator.md) | Generating multi-instance SoCs (`AI_SOC`, `AIZ_SOC`) and running concurrent QEMU + gem5 acceptance |
| [`ci-local.md`](ci-local.md) | Reproducing hosted CI locally (`ci_lint.sh`, `regression.sh`, `promote.sh`, clean-container tests) |
| [`development.md`](development.md) | Branch discipline (`dev` → `main`), coding standards, linting, and regression promotion |
| [`cloud-environment.md`](cloud-environment.md) | Running in non-root cloud sandboxes without `sudo`/`apt` |
| [`qa-emulator-sdk.md`](qa-emulator-sdk.md) | Using `socsim.sdk` (`EmulatorNode`) and `usp qa` tamper-proof scorecards |
| [`multiplatform-soc-qa.md`](multiplatform-soc-qa.md) | Multi-platform SoC QA acceptance profiles and matrix verification |

---

## 3. Architecture & Explanation (Design Rationale & System Internals)

| Document | Scope |
|---|---|
| [`architecture.md`](architecture.md) | Master system architecture, IP-XACT compatibility boundary, white-box compiler pipeline, and platform traceability |
| [`../src/README.md`](../src/README.md) | White-box Python codebase index mapping every `src/socsim/*.py` module to its subsystem |
| [`canonical-native-ir.md`](canonical-native-ir.md) | Canonical immutable Native IR (v2–v6) data model and direct IP-XACT 2022 intake |
| [`native-address-decoder.md`](native-address-decoder.md) | Local address spaces, bridge apertures, and static decoder tree lowering |
| [`native-bus-xml.md`](native-bus-xml.md) | IEEE 1685-2022 bus/abstraction/interface/subspace XML import and export |
| [`native-external-models.md`](native-external-models.md) | `native-model-package-1` SHA-256 contract, `usp-mmio32-v1` socket RPC, and SystemC conformance |
| [`cpu-platforms.md`](cpu-platforms.md) | Configurable CPU provider/model/engine profiles and strict no-ISS-substitution rules |
| [`rtl-cpu-adapters.md`](rtl-cpu-adapters.md) | Actual RTL CPU masters (Chipyard Ibex, custom RTL) over SystemC/QEMU/gem5 memory |
| [`rocket-cpu.md`](rocket-cpu.md) & [`native-rtl-cpu.md`](native-rtl-cpu.md) | Source-built Chipyard `RocketTile` AXI CPU + L1/PTW/TLBroadcast integration |
| [`unified-soc.md`](unified-soc.md) | Shared IP-XACT IR + Zephyr ELF across QEMU and gem5 with SystemC IPs |
| [`live-garnet.md`](live-garnet.md) & [`coherent-dma.md`](coherent-dma.md) | Live gem5 Ruby/MI_example/Garnet NoC routing and two-hart coherent timing DMA |
| [`dual-soc.md`](dual-soc.md) & [`full-soc-integration.md`](full-soc-integration.md) | Sanitized `AI_SOC` / `AIZ_SOC` inventories and Rocket/EarlGrey integration boundaries |
| [`firmware-and-os.md`](firmware-and-os.md), [`freertos.md`](freertos.md), [`rtl-os.md`](rtl-os.md), [`rtl-zephyr.md`](rtl-zephyr.md), [`rtl-software-platform.md`](rtl-software-platform.md) | Bare-metal, FreeRTOS, Zephyr, µC/OS-II/III, OpenSBI/Linux, and Yocto scope |
| [`software-layers-performance.md`](software-layers-performance.md) & [`gem5-se.md`](gem5-se.md) | Software layers, QEMU event counters, estimated vs. native gem5 CPI/IPC, and gem5 SE |
| [`rtl-testchips.md`](rtl-testchips.md) & [`openroad.md`](openroad.md) | CPU-owned RTL test chips and OpenROAD Nangate45 RTL-to-GDS reference flow |
| [`research-direction.md`](research-direction.md) & [`industrial-readiness.md`](industrial-readiness.md) | CHIA comparison, industrial readiness gap analysis, and reference paper ledger |

---

## 4. Reference (Specifications, Contracts, Status & Releases)

| Document | Scope |
|---|---|
| [`build-reproducibility.md`](build-reproducibility.md) | Exact upstream Git tags/SHAs, tool versions, and lockfile contracts |
| [`parameters.md`](parameters.md) & [`registers.md`](registers.md) | Bounded integer parameter expressions and scalar register/field elaboration contracts |
| [`validation.md`](validation.md) & [`native-design-capabilities.md`](native-design-capabilities.md) | Strict negative/fail-closed validation rules and qualified design capability matrix |
| [`milestone-contracts.md`](milestone-contracts.md) & [`milestone-checklist.md`](milestone-checklist.md) | Formal milestone contracts and multi-SoC acceptance checklists |
| [`verification-status.md`](verification-status.md) & [`hosted-ci-receipts.md`](hosted-ci-receipts.md) | Live phase exit verification ledger and workflow-published Git-note receipts |
| [`roadmap.md`](roadmap.md), [`versioned-upgrade-plan.md`](versioned-upgrade-plan.md), [`next-version.md`](next-version.md) | Approved M1–M9 versioned upgrade programme (`v0.3` → `v0.4` → `v1.0`) |
| [`../upgrade/enterprise-emulator-roadmap.md`](../upgrade/enterprise-emulator-roadmap.md) | Enterprise CHIA-parity E1–E12 verification audit ledger |
| [`releases/`](releases/) | Per-alpha release notes (`v0.3.0-alpha.1` through `v0.3.0-alpha.4`) |
| [`../scripts/README.md`](../scripts/README.md) & [`../containers/README.md`](../containers/README.md) | Automation scripts catalog and container runtime documentation |
