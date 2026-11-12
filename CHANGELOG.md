# Changelog

## Unreleased

Added bounded recursive structural IP-XACT hierarchy and designConfiguration view
selection. Source paths remain in IR while explicit aliases preserve logical
backend identities. Added a hierarchical SoC with real generated-machine Zephyr
boot/IRQ acceptance, bringing the supported workspace to eight targets.

Added bounded unsigned component parameter elaboration, dependency resolution,
per-instance overrides and minimum/maximum checks. Expressions are interpreted
through a strict bounded operator whitelist. Resolved values are retained in IR;
the reference UART now requires a real instance override to match its model window.
General signed/string/vector parameters and hierarchy remain unsupported.

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
