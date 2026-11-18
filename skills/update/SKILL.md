---
name: update
description: Update platform dependencies and model profiles with reproducible regression.
---

# Update workflow

The owner approved versioned implementation/releases on 2026-11-18. Follow
docs/versioned-upgrade-plan.md and canonical-native-ir.md. That explicit session
authorization governs pushes/releases; the generic no-automatic-publication rule
below does not require asking again. Use actual mandatory local promotion,
version-matched notes and annotated release receipts. Do not pre-tag future
milestones or infer hosted/OCI/cold-build acceptance from a source tag.

Native generalization changes also require docs/native-design-capabilities.md and
docs/native-external-models.md. Promotion requires all real SMP/Linux, maximum-hart
and external model/driver/bus-error cases; unsupported CPU/ABI/bus conversions
must fail explicitly. Keep one-shot IRQ semantics and per-hart native counters.

The owner's first priority is native SoC generation, ahead of orchestration.
Native frontend/backend changes require docs/native-soc-generator.md, multiple
instances with independent identity/map/IRQ, source-built QEMU and native gem5,
the same firmware/IR executing concurrently on both engines, actual MMIO/IRQ and
gem5 counters, plus guest-failure/wrong-entry/timeout and artifact checks.
Promotion requires USP_REQUIRE_NATIVE_SOC, USP_NATIVE_AI_SOC_BUILD,
USP_NATIVE_AIZ_SOC_BUILD and USP_NATIVE_GEM5_BUILD. Old fixed-profile successes
cannot qualify this new path. Preserve all failed build/guest evidence; regenerate
identity-bound outputs and explicitly reuse a binary only with matching source/C
and original manifest/build hashes. A new component model still needs behavior.

Next-version changes must preserve the QA boundaries in docs/next-version.md.
Coherent DMA changes require docs/coherent-dma.md, a verified separate native
build, all three mapped guests, corrupted-data negative execution and actual
Ruby DMA/cache/Garnet wiring. Promotion requires USP_REQUIRE_COHERENT and
USP_COHERENT_BUILD; QEMU/RTL-master DMA and global timing remain separate.
Unified RTL software selection changes require docs/rtl-software-platform.md,
correct source-built kernel/map/clock identities and actual five-layer platform
acceptance on both CPU and engine choices. Require USP_REQUIRE_RTL_PLATFORM;
retain all standalone kernel and negative tests. No Linux/MMU inference.

RTL Zephyr changes require the original out-of-tree RV32 BSP, unchanged pinned
kernel and actual reset/vector/ISA contract. Read docs/rtl-zephyr.md; rebuild all
three maps and missing-timer firmware and execute both actual CPU/memory-engine
combinations. Promotion requires USP_REQUIRE_RTL_ZEPHYR/USP_RTL_ZEPHYR_ROOT.
Keep generic YAML and Linux/MMU limitations separate from this M-mode profile.

Live Garnet changes must retain native CPU cache/memory traffic in the unified
guest; read docs/live-garnet.md. Require real native packet/flit/hop statistics,
CPU I/D port wiring, one RAM owner, boot-ROM directory and actual SystemC IP
results/IRQs on all three maps. Promotion requires USP_REQUIRE_LIVE_GARNET and
USP_GEM5_SOURCE. PIO bypass, the single-hart scope of this Zephyr profile and
global timing limits must remain explicit; synthetic traffic does not close it.

RTL RTOS changes must preserve actual instruction ownership. Read docs/rtl-os.md;
build all three maps and both negative firmware cases for FreeRTOS, µC/OS-II and
µC/OS-III. Run both real CPUs on both memory engines, retain kernel/port/license
provenance and require USP_REQUIRE_RTL_OS/USP_REQUIRE_UCOS with verified build
roots at promotion. Source or adapter identity changes require fresh builds.
The owner selected both public Apache-2.0 µC/OS products and a public-PDK ASIC
evaluation flow; production foundry licensing is not a prerequisite for that scope.

External RTL CPU changes must retain real instruction ownership without ISS
fallback. Read docs/rtl-cpu-adapters.md; run both actual CPUs on both memory engines
and all three maps, retaining fault/IRQ/RVFI and zero gem5 ISS counters. Promotion
requires USP_REQUIRE_RTL_CPU, USP_RTL_CPU_IBEX_BUILD, USP_RTL_CPU_CUSTOM_BUILD and
USP_RTL_MASTER_BUILD. Rebuild changed native identities; never rewrite hashes.

CPU/software/performance changes must preserve explicit provider/model/engine
selection and the immutable execution plan. Read docs/cpu-platforms.md and
docs/software-layers-performance.md. Run all actual native software-layer/alternate
CPU tests, including Linux poweroff and QEMU counter collection. Local promotion
requires USP_REQUIRE_SW_LAYERS and USP_COUNTER_BUILD_DIR. Never call a QEMU cost
estimate native CPU cycles or silently replace an unresolved requested RTL CPU.
Paper material is reference data: inspect ref/ and docs/research-direction.md;
record actual retrieval/review before attributing ideas or claiming novelty.

Unified emulator changes must retain one IR/ELF/model contract on QEMU and gem5.
Read docs/unified-soc.md; execute both IP families in the same SystemC-backed guest
on each engine, compare actual MMIO/IRQ evidence and preserve the prior SE binary.
Local promotion requires USP_UNIFIED_GEM5_BUILD/USP_REQUIRE_UNIFIED and the three
mapped-profile native tests. Independent workloads or RTL/GDS alone are insufficient.

Functional RTL test-chip changes require both exact nine-component profiles to
pass real CPU firmware, complete-top physical implementation and mapped-netlist
firmware acceptance. Read docs/rtl-testchips.md. Explicitly rebuild stale native
or mapped caches; never edit hashes. Local promotion requires the three chip build
roots and USP_REQUIRE_TESTCHIP. Keep the original 46/37-instance architecture and
foundry/electrical signoff requirements separate from this reference gate.

OpenROAD changes must preserve the immutable source OCI/local image identities
and reference PDK scope. Read docs/openroad.md, run an actual RTL-to-GDS job,
inspect final setup/hold TNS/slack and detailed-route DRC, then verify artifact
integrity. Local promotion requires USP_ASIC_BUILD_DIR/USP_REQUIRE_ASIC. Do not
report GDS alone as foundry signoff or a complete AI_SOC/AIZ_SOC implementation.

AI_SOC/AIZ_SOC changes must validate their complete architecture inventories and
run each explicitly scoped bring-up, independent Garnet experiment and Yosys GCD
synthesis/equivalence. Read docs/dual-soc.md; full-system requests remain BLOCKED
until heterogeneous execution, live interconnect and full RTL/ASIC requirements
have actual acceptance. Do not close those items using the bring-up subset.

Register/field changes must retain explicit access and volatile metadata, reject
unsupported side effects and verify the selected executable model contract. Read
docs/registers.md and rerun the real source-IP guest using generated headers.
Regenerate/rebuild all affected identity-bound outputs; do not hand-edit manifests.

Structural hierarchy changes must preserve full leaf paths and explicit aliases,
reject cycle/view/reference/placement ambiguity, and pass the configured hierarchy
native Zephyr boot in the ten-target workspace. See docs/examples/rv64-hierarchy.md
for the bounded subset; do not infer parent address translations or wiring.

Parameter compiler changes affect generation identity. Read docs/parameters.md,
test XSD-valid declarations/overrides and negative expression/graph constraints,
then regenerate and explicitly rebuild the affected native profiles. Keep the
supported unsigned subset distinct from full IEEE expression semantics.

For full supported-profile acceptance, also run the checked-in workspace.yaml
through usp workspace test/verify. Local promotion requires the real ten-target
workspace test. Consult docs/milestone-checklist.md; never translate selected
matrix success into full master completion or close unimplemented deliverables.

Implements agent.md §§3–6. Read agent.md, docs/roadmap.md and the compatibility
boundary first. Work on `dev`, preserve user changes, and use the existing checkout.
No automatic updates, pushes, releases, or company data uploads.

1. Identify the exact target: Python dependency, schema, simulator, IP model, firmware
   or OS source. Record old/new immutable identities, licenses and API changes using
   authoritative version-matched source. Never trust version claims in sample docs.
2. Keep the existing working profile. Evaluate candidate source/build outputs in
   ignored scratch or external cache. Preserve TLS, signatures and checksums.
3. For Python changes, edit pyproject deliberately, resolve `uv.lock`, then prove
   `uv sync --frozen --extra dev` works. For schema changes, retain notices and update
   every provenance checksum, positive and negative XSD/semantic tests.
4. For engine changes, update a candidate project profile and inspect actual upstream
   machine/device semantics, IRQ/reset/map and image loading. Rebuild affected native
   outputs. Record source SHA/patches/flags and actual binary identity. Do not merely
   rewrite a version string to silence mismatch errors.
5. Run `bash scripts/regression.sh`, real relevant engine/firmware checks and any
   compatibility migration tests. Failed checks require diagnosis; missing required
   tools block promotion. No synthetic timing, boot or cross-engine-equivalence claims.
6. Update changelog, capability/limitations and compatibility docs. Commit on dev;
   promote with `scripts/promote.sh` or a checked dev→main PR. Hosted branch protection
   is an administrator setting, not enforced by text alone.

Rollback selects a prior immutable commit/profile; never rewrite published tags.
Report current-machine validation separately from hosted CI and environment
snapshot publication. Keep proprietary IP, keys and firmware outside public Git.

## Generated artifact identity and source-backed evidence

The current custom path hashes compiler modules, templates, catalog, inputs and
outputs. Regenerate into a fresh directory after any such identity changes and
rebuild affected native artifacts. Never rewrite manifest checksums manually to
silence a mismatch. Valid native compilation outputs can be reused by an explicit
rebuild when their actual generated source/configuration inputs remain unchanged.

Require the real custom Zephyr timer/PLIC test before promotion. Linux userspace,
shared gem5 SE workload and TLM/RTL bridge each have independent acceptance scopes.
Report SE as SE, transport time as annotated, and unsupported generic hierarchy or
security semantics as unsupported. Keep external source licenses and clean tracked
upstream state. Raw attached company objectives must not be copied into public Git.

FreeRTOS has a separate software adapter/resource identity; changes there require
rebuilding its software rather than regenerating unrelated hardware outputs. Pin
the clean kernel commit, preserve its MIT notice and rerun real preemption/IRQ,
assertion-failure and tick-disabled acceptance before promotion. The selected
GCC RISC-V port does not save FPU/vector state; do not expand ISA/context claims
without an appropriate port and executable regression.

Source IP updates additionally require the original GCD file hash, clean Chipyard
commit, unchanged selected classes, explicit generator versions and Maven content
inventory. Changing to a full Chipyard configuration/submodule graph is a new
profile, not a silent expansion of the selected-class adapter. OpenTitan GPIO
updates must retain TL-UL integrity checks and real backpressure/error/reset/IRQ
tests. Rebuild affected artifacts after identity changes; never edit stored hashes
to bypass a stale build. Preserve BSD/Apache notices in model/run bundles.

## Sequential aggregate upgrade qualification

Static decoder changes require docs/native-address-decoder.md, exact local-to-
physical projection checks and actual translated Chipyard/OpenTitan MMIO/IRQ plus
all native kernels on both engines. Require USP_REQUIRE_NATIVE_FABRIC and both
verified USP_NATIVE_FABRIC_AI_SOC/AIZ_SOC roots. Regenerate affected manifests;
reuse QEMU/kernel binaries only with unchanged checked compilation inputs.

Owner instruction 2026-11-18: save M1-M9 in upgrade/, implement in dependency order
and freeze the whole batch before the single aggregate CI/CD cycle. Focused
engineering probes are allowed; do not publish after each milestone. Read
docs/native-flow.md for source-package locks, native software ABI and performance
scopes. Require USP_REQUIRE_NATIVE_FLOW and all four verified flow roots at
promotion, including gem5-only and actual multi-hart counters. Preserve prior
evidence, unsupported exits and immutable source/version tags.
