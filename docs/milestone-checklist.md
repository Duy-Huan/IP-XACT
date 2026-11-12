# Milestone acceptance checklist

This is an engineering acceptance ledger, not a statement of full IEEE coverage.
Only checked items have actual execution evidence in the current environment.
Raw attached company specifications remain outside Git. Phase exit criteria and
broader feature completion are tracked separately.

## Phase 0: foundation and upstream audit

- [x] Source-pinned QEMU/Zephyr combination with custom-machine feasibility audit.
- [x] Real boot/IRQ probes and a clean source export build of the selected MVP.
- [x] Frozen dependencies, source provenance, original fixtures and privacy rules.
- [ ] Independently repeat the complete matrix after a fresh cloud snapshot restore.

## Phase 1: compiler and IR

- [x] Offline official 2022 XSD, safe XML/YAML intake and typed immutable IR.
- [x] Deterministic serialization, overlap/overflow/path/model/IRQ diagnostics.
- [x] Working validate/elaborate CLI and positive/negative compiler tests.
- [x] Bounded recursive structural hierarchy and designConfiguration view selection.
- [ ] Wrapper parameter propagation, physical views and general hierarchical bus semantics.
- [x] Bounded unsigned component parameters, instance overrides and cycle diagnostics.
- [ ] General signed/string/vector parameters and full expression/assertion semantics.
- [ ] Initiator-specific translations and general bus/clock/reset graphs.
- [x] Scalar registers/fields, explicit access policies and exact executable GCD model contract.
- [ ] Register arrays/files, modes, reset/side-effect semantics and broader model contracts.

The selected flat-subset exit is verified. General IP-XACT semantics are incomplete.

## Phase 2: catalog and QEMU

- [x] Exact VLNV bindings, deterministic model lock and pinned source identities.
- [x] Generated and compiled custom machine with ROM/ELF/RAM boot checks.
- [x] Relocated UART, actual timer and peripheral interrupts.
- [x] Explicit seven-, eight- and nine-role model profiles; unsupported inputs fail.

## Phase 3: firmware

- [x] Generated Zephyr board/DTS/application, actual west build and boot/IRQ proof.
- [x] FreeRTOS source-built BSP with preemption/register/FromISR tests.
- [x] Guest failure, missing-tick timeout and modified-artifact negative checks.
- [ ] Other RTOS products: select uC/OS version/license, then implement and verify BSP.

## Phase 4: Linux and Yocto

- [x] Generated DTS, source-pinned OpenSBI/Linux and real initramfs userspace boot.
- [x] UART console, timer/PLIC and exact shared workload exit acceptance.
- [x] Generated Yocto BSP metadata and actual target-machine recipe parsing.
- [ ] Build the complete Yocto image after required download policy is applied.
- [ ] Boot that exact image and execute its own console/driver/init acceptance.

The image deliverable remains blocked. Direct Linux does not close the Yocto item.

## Phase 5: gem5

- [x] Pinned SE adapter, explicit CPU/memory assumptions and real raw statistics.
- [x] The same Linux-ABI ELF runs under QEMU/Linux and gem5 with matching hash.
- [ ] Broader registry, configurable cache/memory experiments and calibrated models.
- [ ] Full-system board/OS coverage is an additional unsupported capability.

The shared-workload exit is verified for SE; complete hardware equivalence is not.

## Phase 6: SystemC, Chipyard and OpenTitan

- [x] Explicit transaction/time/reset/ownership contract and real TLM MMIO/IRQ.
- [x] Actual Chipyard GCD Chisel/CIRCT generation and RTL result/IRQ acceptance.
- [x] Actual OpenTitan FIFO plus GPIO/TL-UL register peripheral and PLIC IRQ.
- [x] Reset, malformed protocol, integrity fault, backpressure and deterministic replay.
- [ ] DMA/coherent-memory expansion and independent asynchronous scheduling.
- [ ] Full Rocket/Chipyard and Earlgrey SoCs remain separate profiles to implement.

The selected-IP hybrid exit is verified. Full SoCs are not implied by that exit.

## Phase 7: maturity

- [x] Multiple explicit SoC profiles, source/dependency locks and capability guide.
- [x] Multi-SoC acceptance workspace with eight actual execution targets.
- [x] Evidence aggregation, artifact hash verification and machine-readable open scope.
- [x] CLI, development/release scripts and native regression workflow definitions.
- [ ] Observe hosted required CI checks and verify repository protection settings.
- [ ] Finish the richer IP library, optional capability-aware API/IDE and release policy.
- [ ] Cut and verify an immutable release after all required deliverables are ready.

## Run and inspect evidence

```bash
usp workspace validate workspace.yaml
usp workspace test workspace.yaml --out results/acceptance-001
usp workspace verify results/acceptance-001
```

This runs hierarchy Zephyr, base Zephyr, FreeRTOS, Linux, gem5 SE, FIFO TLM/RTL
and the dual Chipyard/OpenTitan profile through their real existing runners. Missing
builds are ERROR, never SKIPPED. One failure does not hide the remaining targets.
Every target retains its own original report/logs. The aggregate hashes every
target artifact, records the workspace/source/runtime identity and distinguishes
selected-matrix PASSED from full_master_complete=false. Hash checks detect changes;
they are not a digital signature or external certification.

Output paths must be new. Workspace inputs accept only supported target kinds,
unique safe names and relative paths confined to the workspace root, including
resolved symlinks. They cannot specify shell commands. Custom configurations may
select fewer targets; only the checked-in eight-target matrix is the full current
supported-profile gate. It is mandatory in local promotion. Hosted full-matrix
execution is an explicit expensive workflow-dispatch job, not a claimed past run.
