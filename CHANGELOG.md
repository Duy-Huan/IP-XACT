# Changelog

## Unreleased

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
Rocket/BOOM, other protocols/MMU, RTL OS ports and calibrated timing remain open.

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
