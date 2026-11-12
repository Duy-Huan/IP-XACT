# Changelog

## Unreleased

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
