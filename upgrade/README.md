# Approved versioned native-SoC upgrade programme

Owner approval: 2026-11-18. Baseline main 4d65eae has 506 mandatory local tests
passed, with 75 native-generator tests. Hosted Actions remain unobserved. These
results qualify their existing contracts, not arbitrary industrial SoCs.

The objective is spec/IP-XACT + qualified model packages -> canonical design IR
-> execution plan -> real HW/SW simulator. IP metadata does not create missing
hardware behaviour. Implementation/qualification status is recorded separately
from this approved target; no future release tag exists until acceptance.

| Target | Milestone | Required work and exit |
|---|---|---|
| 0.3.0 | M1 compiler/IR | YAML/direct 2022 import, per-CPU contracts, hierarchy, interfaces, translations, clock/reset, memory/IRQ graphs; equivalent inputs yield equivalent canonical hardware and fail unsupported semantics |
| 0.3.0 | M2 packages | Exact VLNV/source/license/build/driver contracts and richer versioned SystemC transactions; new IP through a package with actual MMIO/IRQ/reset/error tests on both engines |
| 0.3.0 | M3 lifecycle | One generated plan/build/run flow; explicit CPU/RAM/state owners and adapter capability/resource limits; previously unseen designs execute without compiler edits or ISS substitution |
| 0.3.0 | M4 software | IR-derived startup/linker/DTS/BSP/drivers; bare-metal, Zephyr, FreeRTOS, both public µC/OS kernels, OpenSBI/Linux and actual Yocto image; relocation and real scheduler/IRQ/boot/driver acceptance |
| 0.4.0 | M5 CPU/bus | Existing real Ibex/custom masters, then qualified AXI/TileLink, Rocket then BOOM; explicit Earlgrey full-top/subsystem ownership; real protocol/IRQ/reset/MMU and eligible OS tests |
| 0.4.0 | M6 network/time | IR-generated Garnet topology and qualified QEMU/SystemC/RTL transaction adapters, DMA/coherence and defined synchronization; actual paths/data/IRQs/backpressure with any bypass disclosed |
| 0.4.0 | M7 performance | Per-hart/IP/network ROI, native gem5/RTL statistics and explicit QEMU event/cost estimates, reference calibration and error reporting |
| 1.0.0 | M8 full designs | Source/model/adapter/driver/test for every AI_SOC/AIZ_SOC 46/37 instance; matching synthesizable RTL where implementations exist, actual mapped execution and public-technology physical evaluation |
| 1.0.0 | M9 release | Independent unseen design, cold builds, rootless Podman SDK/OCI, mandatory regression and observed hosted CI; immutable versioned release with evidence and complete artifact dependency closure |

## First increment: 0.3.0a1 / v0.3.0-alpha.1

Scope: start M1 with a versioned canonical native IR, direct structural IP-XACT
plus explicit runtime bindings, and the existing qualified flat native adapters.
Both intake paths must drive the same compiled QEMU/native gem5 hardware and
software from the common IR. Preserve imported source hashes and exact VLNV,
width/register/parameter/hierarchy data; no ignored standard semantics.

This increment is not full M1-M4: generic bus/translation and heterogeneous CPU
execution, richer transaction models and generic RTOS/driver synthesis remain
open. Existing legacy profiles remain separately qualified. Introduce per-CPU,
clock/reset and fabric records only with strict executable semantics for the
current native contract; reject unsupported lowerings rather than imply support.

Required first-increment evidence: YAML/IP-XACT hardware equivalence, structural
hierarchy input, precise invalid binding/map/CPU/width diagnostics before output,
canonical-artifact tamper rejection and real dual-engine bare-metal/Linux tests.
The same typed IR owns placement/interrupt/boot/engine selection after intake.

## Release rules

- Package PEP 440 version 0.3.0a1 corresponds to immutable tag v0.3.0-alpha.1.
  Later alphas increment only when another accepted scope is delivered. Final
  v0.3.0 requires all M1-M4 exits, v0.4.0 all M5-M7, and v1.0.0 all M8-M9.
- Every release has version-matched notes, source identity, completed/remaining
  capability matrix and machine-readable local evidence. Tags are cut from
  regression-promoted main; no force push or rewriting published tags.
- Cold qualification and hosted status are independent of verified development
  cache reuse. A source tag is distinct from published GitHub assets or an OCI
  image. Network/provisioning failures remain blocked, never reported passed.
- Existing container-v0.2.0-r1 is immutable and independent of application tags.
  A new SDK publication needs its own actual build, rootless Podman smoke and
  OCI round trip; an application tag does not assert those assets exist.
- Retain failed/negative evidence. Archive cache dependency closures safely;
  never remove live symlink targets or rewrite hashes to accept stale binaries.

Garnet is a simulator model, not synthesizable RTL. A physical NoC needs a
qualified RTL counterpart; an ISS-only CPU cannot supply a matching RTL chip.
Public-library/PDK evaluation is the owner's ASIC scope, not foundry sign-off.

## Sequential implementation and one aggregate gate

Owner instruction, 2026-11-18: keep milestone requirements, dependencies and observed
status under upgrade/. Implement milestones in dependency order on dev; run focused
engineering checks as needed, then freeze the complete batch and execute workflow
lint, clean minimal Debian CI and the mandatory native promotion exactly once for
that candidate. A failed gate must be fixed and rerun. Publish only an accepted
version, with an immutable tag; individual milestones do not trigger separate CI
or speculative release tags. Never mark a milestone complete from planning alone.

[Machine-readable ledger](milestones.yaml) records required exits. Each M1–M9
file distinguishes the baseline, remaining work and evidence required. A partial
or blocked dependency stays visible; metadata does not replace a missing model.

## Current sequential batch

The latest promoted baseline is `v0.3.0-alpha.5` (`6a1492c`, 826 local passes:
775 initial plus 51 resumed after `tmpfs` ENOSPC repair; zero skips/failures),
delivering [rocket-native-batch.md](rocket-native-batch.md): actual Rocket
ownership, native IR v6 source binding and source-built software. The prior
[bus-xml-batch.md](bus-xml-batch.md) (`0.3.0a4`) adds standard XML bus graphs
and IR v5 static lowering; [address-decoder-batch.md](address-decoder-batch.md)
(`0.3.0a3`) qualifies v4; [native-flow-batch.md](native-flow-batch.md)
(`0.3.0a2`) adds source packages, persisted lifecycle and native RTOS/event
contracts.

Observed hosted GitHub Actions runs on `v0.3.0-alpha.5` failed because Debian
container jobs lacked explicit `git config --global --add safe.directory` after
checkout (`dubious ownership` on `/__w/IP-XACT/IP-XACT`). Fixing hosted CI
container safe-directory configuration and syncing the ledger on `dev` precedes
the next milestone-closing upgrade batch. Broad M1-M9 remain partial until every
exit has actual qualification evidence; run `usp upgrade --root .` to validate
ledger consistency.
