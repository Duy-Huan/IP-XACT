# Roadmap aligned to the owner master specification

The objective in agent.md takes precedence over the original bare-metal-only
baseline. Phase completion means executable acceptance, not directories or claims.
Detailed observed status and evidence are maintained in verification-status.md.

| Phase | Acceptance target | Current boundary |
|---|---|---|
| 0 | Audited, pinned QEMU/Zephyr combination and reproducible setup | Source-pinned reference builds and real guest tests |
| 1 | Safe IP-XACT/XSD, typed deterministic IR and semantic diagnostics | Flat 2022 subset plus strict CPU/boot/IRQ/address-space overlays; generic hierarchy/bus/register features still unsupported |
| 2 | Strict catalog/lock and generated compiled custom QEMU machine | Seven supported roles; separate explicit eight-role hybrid profile |
| 3 | Generated Zephyr BSP, real build, UART and IRQ | CLINT timer ISR and UART→PLIC ISR; original guest conformance application |
| 4 | Linux userspace and Yocto BSP/image | Direct OpenSBI/Linux path and generated layer; Yocto image fetch policy remains a prerequisite |
| 5 | Pinned gem5 workload/statistics and cross-engine behavior | Explicit SE workload adapter; not a full-system SoC board |
| 6 | Real SystemC/RTL MMIO/IRQ bridge with time contract | FIFO TLM and pinned OpenTitan RTL; deterministic replay; no DMA/coherence |
| 7 | Versioned release, support matrix, regression and repeatable evidence | CLI, native CI and development workflow; no industrial/universal support claim |

Extend the parser deliberately: IP-XACT addressSpaces per initiator, bridge
translations, registers/fields/access policies, parameter constraints, bus and
abstraction definitions, views/fileSets, designConfiguration and recursive hierarchy.
Each extension needs positive/negative fixtures and a compatible executable model.
Do not infer behavior from XML names or add unsupported metadata as ignored fields.

The selected FreeRTOS GCC RV64 port now has a generated static-allocation BSP,
real timer preemption/register checks and UART/PLIC FromISR notification on the
non-hybrid profile; see freertos.md for its limits. Broader FreeRTOS drivers and
the requested µC/OS product/license remain separate work. Chipyard requires a
pinned Chisel/CIRCT/submodule configuration and actual generated RTL verification.
OpenTitan currently supplies a FIFO primitive only, not a complete secure SoC.
Hybrid expansion requires reset/error/backpressure/width/ordering tests, then DMA
and memory permissions. Security and calibrated performance remain separate work.
