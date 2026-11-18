# Roadmap aligned to the owner master specification

The objective in agent.md takes precedence over the original bare-metal-only
baseline. Phase completion means executable acceptance, not directories or claims.
Detailed observed status and evidence are maintained in verification-status.md.

The owner approved the versioned programme on 2026-11-18; see
versioned-upgrade-plan.md for M1-M9 and release gates. First delivery is a scoped
0.3.0a1 canonical-IR/direct-IP-XACT increment, not final v0.3.0 completion.

Owner priority: native generation precedes CHIA/agent orchestration. The frontend
emits real IP-XACT/IR, custom QEMU/native gem5 graphs, per-hart IRQ/memory wiring
and software from design data. In addition to the 13/11-instance base cases, the
native SMP inputs have 16/12 instances, four/two harts, high physical maps and
source-built OpenSBI/Linux. Project-local models register through the explicit
MMIO32 transport rather than new compiler branches. See native-soc-generator.md,
native-external-models.md and native-design-capabilities.md. Generic OS/driver
ports, CPU/bus/translation adapters and full inventories remain separate exits;
older profile success does not qualify those contracts.

The next-version QA matrix is in next-version.md. Explicit native gem5 two-hart
coherent DMA is implemented in a separate profile (coherent-dma.md); the original
single-hart Zephyr reference keeps its prior boundary. Generic platform YAML now
selects the four existing source-built RV32 kernels with strict build identities
(rtl-software-platform.md). These updates supersede the older no-DMA and
standalone-only RTL software boundaries below; broader master exits remain open.

| Phase | Acceptance target | Current boundary |
|---|---|---|
| 0 | Audited, pinned QEMU/Zephyr combination and reproducible setup | Source-pinned reference builds and real guest tests |
| 1 | Safe IP-XACT/XSD, typed deterministic IR and semantic diagnostics | Bounded structural hierarchy/configured views, component parameters, scalar registers/fields and CPU/IRQ overlays; general bus/register side effects/translation semantics still unsupported |
| 2 | Strict catalog/lock and generated compiled custom QEMU machine | Seven-role base, eight-role FIFO and nine-role Chipyard/OpenTitan profiles |
| 3 | Generated Zephyr BSP, real build, UART and IRQ | CLINT timer ISR and UART→PLIC ISR; original guest conformance application |
| 4 | Linux userspace and Yocto BSP/image | Direct OpenSBI/Linux path and generated layer; Yocto image fetch policy remains a prerequisite |
| 5 | Pinned gem5 workload/statistics and cross-engine behavior | SE plus full-system source-IP profiles; native generated SMP/Linux and per-hart counters; model timing remains uncalibrated |
| 6 | Real SystemC/RTL MMIO/IRQ bridge with time contract | FIFO/GCD/GPIO and deterministic replay; separate two-hart native gem5 coherent DMA reference; broader timing/initiators open |
| 7 | Versioned release, support matrix, regression and repeatable evidence | Multi-SoC workspace, evidence integrity, CLI/native CI; release and optional API/IDE still open |

Extend the parser deliberately: IP-XACT addressSpaces per initiator, bridge
translations, general register access/side-effect semantics, parameter constraints, bus and
abstraction definitions, views/fileSets, designConfiguration and recursive hierarchy.
Each extension needs positive/negative fixtures and a compatible executable model.
Do not infer behavior from XML names or add unsupported metadata as ignored fields.

The selected FreeRTOS GCC RV64 port now has a generated static-allocation BSP,
real timer preemption/register checks and UART/PLIC FromISR notification on the
non-hybrid profile; see freertos.md for its limits. Separate source-built RV32
FreeRTOS and both Apache-2.0 µC/OS-II/III kernels now run on actual RTL masters;
see rtl-os.md. Broader FreeRTOS drivers remain separate work. The selected Chipyard
GCD classes now compile with pinned Chisel/Scala/CIRCT and execute as real RTL;
the full Rocket/submodule configuration remains unimplemented. OpenTitan supplies
both FIFO and a complete GPIO/TL-UL peripheral, not the complete secure SoC.
Hybrid expansion requires reset/error/backpressure/width/ordering tests, then DMA
and memory permissions. Security and calibrated performance remain separate work.

Full-board source inspection and the distinct CPU/memory/image ownership contracts
are recorded in full-soc-integration.md. Its prerequisites are not completed
builds or acceptance evidence; the full-SoC checklist remains open.

AI_SOC/AIZ_SOC now have explicit complete metadata inventories and separate real
bring-up configurations. Their supported flow includes independent Garnet mesh
experiments, GCD generic synthesis/equivalence and a reference Nangate45
OpenROAD RTL-to-GDS result. See dual-soc.md and openroad.md; this does not close
the complete heterogeneous CPU/IP/NoC/RTL/ASIC architecture targets.

Separate nine-component functional RTL test-chip profiles now integrate an actual
Ibex CPU, ROM/SRAM, OpenTitan UART/GPIO/timer, Chipyard GCD and original fabric.
Both complete test-chip tops reach Nangate45 GDS with zero router DRC and passing
reference setup/hold timing. Same-firmware RTL/mapped-netlist acceptance is a
separate required gate. See rtl-testchips.md; this does not change the larger
architecture inventory or close its remaining heterogeneous/signoff requirements.

The current emulator priority is one virtual SoC, as clarified by the owner:
actual Chipyard/OpenTitan peripherals share a SystemC process and bind into the
selected QEMU or gem5 CPU engine. The same Zephyr ELF now passes real MMIO/PLIC
IRQ acceptance on both engines for rv64-ips and both system bring-up maps. See
unified-soc.md. This functional integration is distinct from independent engine
experiments and from the separate CPU-owned RTL/GDS profiles.

CPU provider/model, engine and software workloads are now explicit configuration
inputs with an immutable execution-plan layer. QEMU rv64/max and gem5 TimingSimple/
AtomicSimple run selected bare-metal, FreeRTOS, Zephyr and Linux/boot workloads.
The source-IP board now boots Linux under gem5 with actual MMU/FDT/firmware/OS
handoff and clean poweroff. Native gem5 CPI/IPC and QEMU event-count cost estimates
remain distinct. See cpu-platforms.md and software-layers-performance.md.
Pinned Chipyard Ibex and normalized custom RTL masters support RV32 bare-metal
and FreeRTOS/µC/OS-II/III on both memory engines; see rtl-cpu-adapters.md and rtl-os.md.
Source-built M-mode Zephyr now also runs on both supported RTL masters and memory
engines, with the real generated kernel/driver application; see rtl-zephyr.md.
Rocket/BOOM, other protocols/ISA/MMU, Linux RTL ports and cross-ISS remain open.
Native gem5 cache/memory traffic now crosses live Ruby/Garnet while the same
Zephyr guest exercises SystemC IPs. This four-router reference is distinct from
the independent 16/8-router mesh experiments and unimplemented device DMA.
The owner selected public-PDK ASIC evaluation; production foundry signoff is not
required for that clarified scope. Full-inventory implementation remains open.

The owner research direction and nine-paper ledger are in research-direction.md
and ref/. CHIA v3 was provided and reviewed (ref/notes/chia-2606.27350.md); the other eight
sources remain unread. Novelty claims await broader related-work review and
measured evaluation.

## Sequential batch programme

The owner requested the complete milestone ledger under [upgrade/](../upgrade/README.md),
with sequential implementation and one aggregate CI/CD cycle per frozen batch.
The 0.3.0a2 scope adds source packages, a persisted native lifecycle, real single-hart
RV64 RTOS/Zephyr BSPs (both public µC/OS kernels included) and QEMU per-hart event
estimates. See native-flow.md. Broad M1-M9 states remain partial until every exit
has matching evidence; no final v0.3/v0.4/v1.0 tag is pre-created.

The 0.3.0a4 scoped batch adds standard native bus XML/IR v5 and workflow Git-note
observations. See docs/native-bus-xml.md, docs/hosted-ci-receipts.md and
upgrade/bus-xml-batch.md. Full milestone completion remains separately gated;
implementation references alone do not constitute release qualification.
