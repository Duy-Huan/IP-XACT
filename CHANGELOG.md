# Changelog

## Unreleased — continuous milestone implementation

- Build the actual Chipyard-pinned RocketTile with its L1/PTW/TLBroadcast and AXI
  adapters; execute real RTL instructions on QEMU/gem5-owned physical memory.
- Add explicit CPU ownership/plan lowering, strict RV64IMAC LP64 ELF checks,
  raw AXI/commit verification, deliberate backpressure/error diagnostics and
  source-built FreeRTOS plus PMP/Sv39 M/S/U conformance firmware.
- Broad M1–M9 exits remain partial. No new release tag or hosted result is implied.

## 0.3.0a4 — 2026-11-18 (v0.3.0-alpha.4)

- Import real 2022 bus/abstraction/interface/address-space/segment/subspace/
  interconnection XML into typed native IR v5 and checked static projection.
- Export qualified fabric designs to validated standard XML; include public
  AI_SOC/AIZ_SOC XML flows and mandatory real dual-engine/kernel acceptance.
- Add workflow-published Git notes with exact source/job outcomes and source
  release asset hashes; missing evidence remains UNOBSERVED. No REST access needed.
- Make native bare-metal ELF debug paths and assembler intermediates reproducible
  across output directories; require byte identity for equivalent XML/fabric inputs.
- Preserve v3/v4, original qualified engines and immutable SDK/source releases.
  General bus timing, RTL CPUs/MMU, Yocto and complete M1-M9 exits stay open.

## Unreleased

Remaining M1-M9 work is tracked in docs/versioned-upgrade-plan.md. Future
version tags are created only after their stated acceptance gates.

## 0.3.0a3 — 2026-11-18 (v0.3.0-alpha.3)

- Preserve typed local address spaces, bridge apertures and local placements in
  native IR v4; validate and lower static decoder trees to the CPU physical map.
- Reject cycles, aliases, missing nodes, clipped/overlapping intervals, unknown
  protocols, misalignment and address-width overflow before generating output.
- Add native fabric intake to the existing HW/SW lifecycle and translated
  Chipyard/OpenTitan qualification fixtures for both engines and native kernels.
- Keep flat IR v3 compatibility, exact build identities and all prior regression
  gates. General bus timing, DMA, heterogeneous CPUs and full milestones stay open.
- Tag v0.3.0-alpha.3 is reserved for a completed mandatory local gate; no future
  tag or observed hosted/OCI/cold acceptance is created by this candidate entry.

## 0.3.0a2 — 2026-11-18 (v0.3.0-alpha.2)

- Save the complete approved M1-M9 dependency/checklist ledger under upgrade/;
  implement sequentially and qualify a frozen batch with one aggregate CI/CD gate.
- Source-built model packages pin VLNV, license, sources, driver, register/reset
  and RPC contracts; actual SystemC reset/MMIO/IRQ/error probes qualify each build.
- Add usp flow plan/build/all/run/verify with explicit CPU/RAM/peripheral/time
  ownership, selected engines, ordered software and tamper-checked artifact links.
- Generate native RV64 single-hart FreeRTOS and Zephyr BSPs from canonical IR.
  Add explicit local LP64 µC/OS-II/III ports with preserved public source pins,
  64-bit context/stack/pointer adaptation and scheduler/register/UART IRQ tests.
- Add native QEMU per-hart execution/data counters with configurable analytical
  costs; retain native gem5 statistics and whole-run/uncalibrated fidelity labels.
- Qualify AI_SOC/AIZ_SOC flow fixtures and a gem5-only path. These are scoped
  increments within M1/M2/M3/M4/M7, not completion of all approved milestone exits.
- Require native lifecycle/software/package/error cases in the final promotion.
  Preserve existing releases; no new SDK or hosted/cold qualification is implied.

## 0.3.0a1 — 2026-11-18 (v0.3.0-alpha.1)

- Owner-approved versioned upgrade programme: v0.3 compiler/model/BSP, v0.4
  qualified RTL/bus/NoC/time/performance, v1.0 full-reference/release acceptance.
- Start M1 with immutable native IR v3 and direct structural IP-XACT intake,
  exact binding/window/register validation, hierarchy aliases and IR-driven backends.
- Require all Linux SMP harts online; add direct high-address AI_SOC/AIZ_SOC and
  hierarchical real-engine gates. Regenerate version-affected legacy bundles and
  record explicitly verified QEMU cache reuse while rebuilding their software.
- Match package/CLI/lock/tag identities; attach complete checksummed local JUnit
  and source qualification to annotated tags, with separate hosted source assets.
- This is an alpha scope; M1-M9 completion and future release tags are gated on
  actual execution. See docs/versioned-upgrade-plan.md for remaining exits.

## 0.2.0 qualified baseline — main 4d65eae

- Parameterized homogeneous hart topology, per-hart CLINT/PLIC/startup and native counters.
- 48-bit physical maps, configurable gem5 clocks/latency and native OpenSBI/Linux boot.
- External MMIO32 executable/VLNV registry, checked identities, original SystemC/IRQ-driver example.
- Mandatory SMP/Linux/model/error gates; modern Linux ISA bindings and one-shot IRQ fixes.
- Qualified functional contracts; generic bus/RTL/OS/physical coverage remains explicit.

### CI, SDK and earlier platform increments

Added clean-container CI reproduction and workflow/ShellCheck validation. Declared
procps for cleanup tests and bzip2 for source builds, isolated the wrong-kernel Git fixture, pinned hosted
runner/base images and moved actions to verified Node.js 24 revisions. Publishing
remains gated on the exact candidate's local checks; hosted status is separate.

Prioritized native SoC generation from a structured spec, memory map and I/O/IRQ
assignments. Added a parameterized IP-XACT/XSD/IR frontend independent of fixed
instance roles, custom QEMU machine generation, bare-metal headers/linker/firmware,
multiple independent Chipyard GCD/OpenTitan GPIO/UART/RAM instances, actual guest
conformance and evidence verification. Concurrent native gem5/QEMU execution shares IR/ELF and checks MMIO/IRQ data.
Native generator OS lowering remains
open; the existing full architecture inventories are separate targets.

Added directly visible AI_SOC/AIZ_SOC IP-XACT examples, canonical file/IR parity
checks and actual shared-ELF QEMU/gem5/SystemC acceptance. Added an industrial
readiness assessment separating supported pilot use from arbitrary-SoC and full
architecture qualification. The initial CHIA download was blocked; the owner then supplied v3. A page-cited
review and pinned source inspection distinguish reported CHIA results from this
project's actual runs and retain its remaining industrial limitations.

Added a Podman rootless SDK Containerfile, committed-source build context,
UID/GID-preserving wrapper, isolated host caches, SELinux support and offline
native QEMU smoke. A separate container-v0.2.0-r1 prerelease workflow builds,
tests, exports/loads an OCI archive and publishes checksummed assets from main.
Native Podman hosted acceptance remains distinct from local Docker-compatible
validation; full model builds and the separate OpenROAD runtime are not bundled.

Added an explicit two-hart native gem5/Ruby/Garnet profile with original timing
DMA, per-initiator derived IR, dirty-cache data checks, actual PLIC interrupts and
the existing SystemC Chipyard/OpenTitan IP guest. Native transaction checksums,
port wiring, statistics, artifact inventory and deliberately corrupted firmware
are regression gates on all three maps. QEMU/RTL-master DMA, PIO timing and the
full architecture remain separate work.

Unified platform YAML now selects the existing source-built RV32 FreeRTOS,
µC/OS-II/III and Zephyr profiles alongside bare-metal on both actual RTL CPUs and
memory engines. Strict kernel/map/clock identity, ownership, stage verification
and aggregate performance prevent ISS substitution and mixed evidence. Both new
native gates are required for promotion. Added the complete QA remediation matrix
with explicit remaining CPU/MMU, compiler, full-SoC, Yocto, PVT and hosted exits.

RTL CPU startup now waits for a live Unix socket within an explicit budget of up
to 60 seconds, separate from guest execution. Dead servers report their actual
exit code; startup deadlines are recorded as timeouts and stop owned processes.
This fixes a cold gem5 startup failure under concurrent full-RTL load. Real delayed,
expired and exited server tests cover the lifecycle without changing guest checks.

Added an original source-built RV32 Zephyr SoC/board for both real RTL CPU masters
and both memory engines. An explicit Ibex reset shim and vectored IRQ contract
retain unmodified upstream kernel scheduling and real timer/UART/GCD/GPIO drivers.
Actual firmware/config/DTS and source identities are verified; missing timer and
modified reset artifacts fail acceptance. The new CLI, native CI and promotion
gate remain distinct from unsupported MMU/Linux and generic platform YAML ports.

Added source-built RV32 FreeRTOS, µC/OS-II and µC/OS-III on both actual RTL CPU
masters with QEMU/gem5 memory engines. The new dedicated CLI/workflow binds
generated maps, exact public kernel pins, reviewed port adaptations and preserved
licenses. Native tests require preemption, saved registers and real timer/UART
interrupts; µC/OS additionally exercises Chipyard GCD/OpenTitan GPIO IRQs. Both
failure and disabled-tick firmware are rejected. Promotion now requires these
three-kernel matrices. The owner clarified public-PDK ASIC evaluation scope.

Added an actual live Ruby/MI_example/Garnet path for native gem5 CPU cache/memory
transactions in the unified Zephyr/SystemC SoC. Four routers connect CPU cache,
RAM directory, ROM directory and I/O controller; real packet/flit/hop counters,
CPU port wiring and the single RAM owner are required at promotion. Native PIO
still uses the Ruby sequencer's PIO path. QEMU/RTL-master Garnet, device DMA and
multihart coherence remain separate work.

Native gem5/Garnet/RTL-master builds now allow a bounded graceful shutdown before
hard termination, preserving SCons' completed-object database after a timeout.
Actual timeout/return-code evidence remains a failure. Real subprocess tests cover
checkpoint preservation and termination of an unresponsive build.

Added source-backed Chipyard Ibex and custom RTL CPU masters through SystemC/TLM,
QEMU qtest and an out-of-tree gem5 atomic master. Explicit target IR and CPU/boot/
IRQ ownership prevent ISS substitution. Native RTL clocks/RVFI provide CPI/IPC;
gem5's passive IRQ context must show zero ISS execution. Added normalized HDL ABI,
source/build/artifact validation, deterministic RV32 firmware and a native matrix
across three I/O maps, with mandatory promotion checks and public-source setup.
Rocket/BOOM, other protocols/MMU, Zephyr/Linux RTL ports and calibrated timing remain open.

Pinned ELF inspection is now a core dependency so minimal installations can
validate/run external firmware artifacts without the optional source-build SDK.

Added configurable CPU provider/model/engine/workload profiles, immutable execution
plans with explicit ownership/routing/time domains and real bridge-trace checks.
Native adapters select QEMU rv64/max and gem5 TimingSimple/AtomicSimple; unresolved
Chipyard/custom RTL or cross-ISS CPU combinations fail without substitution.
Added actual bare-metal, FreeRTOS, Zephyr, Linux/OpenSBI and boot-sequence runs on
the unified source-IP board, QEMU plugin event counters, explicitly estimated
CPI/IPC, native gem5 statistics and portable software-matrix verification.
Added a nine-paper reference ledger/fetch tool and an evaluation roadmap; network
policy currently blocks retrieval, so no paper review or research-priority claim
is made.

Added a unified virtual-SoC adapter: the same IP-XACT IR, Zephyr ELF and actual
SystemC/Verilator Chipyard GCD/OpenTitan GPIO models now execute on QEMU or gem5
full-system. Added out-of-tree gem5 MMIO/PLIC bridge, real reset ROM, explicit
engine binding plans, IP command/data comparison, evidence verification and
three mapped-profile native promotion gates. Timing remains transaction-driven
and uncalibrated; full Rocket/Earlgrey, DMA/coherence and live Garnet remain open.

Added separate nine-component AI_SOC/AIZ_SOC functional RTL test-chip profiles.
Actual Ibex CPUs execute compiled RV32 firmware against OpenTitan UART/GPIO/timer,
Chipyard GCD, real ROM/SRAM and original OBI/TL-UL fabric. Added complete-top Slang
synthesis and OpenROAD GDS flow, mapped-netlist compilation, repeated boot/IRQ/
memory/exception tests, serial comparison, cycle-limit negatives and portable
hashed evidence. Added explicit rebuild history, mandatory local native gates and
a manual provisioned-runner workflow. Larger architecture completion and foundry
signoff remain open, including the reported electrical violations.

Integrated pinned OpenROAD-flow-scripts for a self-contained RTL top/SDC or the
verified Chipyard GCD leaf. Actual Nangate45 implementation produces final GDS,
netlist, extraction and timing/routing reports. Added artifact/negative timing/DRC
verification, process cleanup, immutable local image setup and optional physical
evidence in system acceptance. Reference-layout completion remains distinct from
full-SoC/foundry signoff.

Added sanitized AI_SOC/AIZ_SOC architecture inventories and separate executable
bring-up configurations. Added strict system plans, independent gem5 Garnet mesh
experiments, actual GCD leaf synthesis/equivalence and aggregate evidence checks.
Full heterogeneous execution/live NoC/full RTL/ASIC requirements remain explicit.
The supported acceptance workspace now includes ten targets.

Added scalar IP-XACT register/field metadata, explicit access policies, alignment,
bounds and overlap diagnostics. Declared GCD metadata must match the executable
shim/RTL contract. Generated register offsets/masks now feed the real Zephyr
source-IP driver; unsupported model contracts fail before execution.

Added bounded recursive structural IP-XACT hierarchy and designConfiguration view
selection. Source paths remain in IR while explicit aliases preserve logical
backend identities. Added a hierarchical SoC with real generated-machine Zephyr
boot/IRQ acceptance, bringing the supported workspace to eight targets.

Added bounded unsigned component parameter elaboration, dependency resolution,
per-instance overrides and minimum/maximum checks. Expressions are interpreted
through a strict bounded operator whitelist. Resolved values are retained in IR;
the reference UART now requires a real instance override to match its model window.
General signed/string/vector parameters and wrapper parameter propagation remain unsupported.

Added strict multi-SoC workspace validation/execution and aggregate evidence
verification. Seven real OS/engine/hybrid targets now have one repeatable acceptance
command, with missing artifacts classified ERROR, retained raw logs and explicit
open milestone criteria. The full supported matrix is mandatory in local promotion.

Added a nine-role IP-XACT profile with actual Chipyard GCD Chisel/CIRCT generation
and OpenTitan GPIO/TL-UL RTL. Added source/tool/dependency locks, SystemC transaction
adapter, two PLIC IRQs, guest vector/readback/W1C checks, host protocol/integrity/
backpressure/reset checks and deterministic replay. Corrected repeated-level IRQ
delivery in the new bridge and resolved Zephyr's compiler prefix explicitly in
both environment and CMake cache. No full Rocket/Earlgrey platform claim is made.

Added a source-pinned FreeRTOS 11.2.0 RV64 M-mode acceptance BSP using the verified
hardware IR and unmodified upstream port. Actual guest tests cover timer-driven
preemption, saved-register checks, UART/PLIC FromISR task notification, assertion
failure and missing-tick timeout. Software identity, packaging, documentation,
source acquisition and required local/hosted native regression were extended.

## 0.1.0

Initial original implementation: offline IEEE 1685-2022 XSD, strict flat metadata
subset, deterministic immutable IR, explicit model bindings, fixed QEMU RV64 virt
profile, real bare-metal firmware and evidence capture. Includes negative compiler
and simulator tests, dev/main promotion workflow, CI regression, agent rules and
setup/update skills. Other engines, RTOS and Yocto remain explicit future milestones.

## 0.2.0

Incorporated the owner's master objective into agent rules. Added strict hardware
overlays, typed CPU/IRQ/address-space IR, JSON schema export and versioned model
catalog. Added generated and compiled QEMU RV64 machine, external Zephyr BSP and
real timer/UART IRQ tests; direct Linux/OpenSBI initramfs path and Yocto layer
metadata; optional gem5 SE workload adapter and local SystemC/OpenTitan RTL FIFO
bridge with deterministic trace evidence. Added source-build helpers, static typing
and native CI coverage. Capability and unresolved-scope details are recorded in
docs/verification-status.md; no universal or industrial-completeness claim is made.

Native process owners handle SIGTERM with scoped cleanup and restore the previous
handler. A real CLI cancellation test verifies exit 143 and that its guest is reaped.

Gem5 source builds now explicitly configure the external SystemC process profile
through upstream Kconfig, retaining the native configuration and executable
build-info evidence. Configuration tampering is rejected. This avoids building
the unused embedded SystemC kernel; actual SystemC/TLM co-simulation remains in
its existing source-built processes, with the original native engine binaries
preserved by extension builds.

OpenROAD jobs now support explicit continuation of matching incomplete outputs
and bounded configurable timeouts. Resume validates RTL/SDC/configuration/image
identity, confirms prior container shutdown, locks the output and retains prior
attempt logs. Native Make continues from its checkpoints; final DRC/STA/GDS and
mapped-firmware acceptance remain mandatory.

The FreeRTOS preemption fixture now waits for observable progress from both
workers within a bounded guest-tick interval. Delayed functional-emulator ticks
can arrive in a burst before worker execution; the minimum timer/preemption,
register preservation and IRQ acceptance requirements are unchanged.
