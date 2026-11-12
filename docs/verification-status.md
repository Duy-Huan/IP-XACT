# Master-spec phase status

The owner objective is incorporated into agent.md; the raw attached master document
is not published. Statuses are scoped to the selected reference and real acceptance,
not a claim that every future standard/backend feature is implemented.

| Phase | Status | Actual evidence / remaining boundary |
|---|---|---|
| 0 feasibility | Verified selected profile | Exact public QEMU/Zephyr source audit, real generated machine, guest timer/UART/PLIC |
| 1 compiler/IR | Verified supported subset; broader requirements partial | Official 2022 XSD; bounded structural hierarchy/configured views, component parameters, scalar register/field contracts and CPU/IRQ; general translations/buses/register side effects remain unsupported |
| 2 models/QEMU | Verified selected profiles | Versioned exact catalog/lock; compiled C machine; ROM/ELF reset and relocated UART; separate FIFO and nine-role source IP profiles |
| 3 Zephyr MVP and FreeRTOS extension | Verified selected profiles | Generated Zephyr board/DTS with real timer/PLIC ISR; FreeRTOS static BSP with tick preemption, saved registers and UART/PLIC notification |
| 4 Linux/Yocto | Direct Linux verified; Yocto image blocked | OpenSBI + Linux boot to original userspace; generated meta-usp parsed successfully (924 recipes, MACHINE=usp-rv64); upstream dependency fetch rejected by proxy |
| 5 gem5 | Verified SE workload subset | Same exact RV64 ELF executed under QEMU/Linux and gem5; guest exit zero and real raw stats; full-system board remains unsupported |
| 6 SystemC/RTL/hybrid | Verified selected IPs | FIFO TLM/RTL, actual Chipyard GCD Chisel generation, complete OpenTitan GPIO/TL-UL peripheral, guest PLIC IRQ and deterministic replay; no full Rocket/Earlgrey/DMA/coherence claim |
| 7 maturity | Partial; multi-SoC gate verified | Ten-target workspace execution and artifact-integrity verification, native CI and support docs; release/hosted checks/IDE still open |

The actual initial MVP sequence is IP-XACT → typed IR → exact binding → generated
QEMU C → compiled usp-rv64 → generated Zephyr board/DTS → west ELF → real UART,
timer ISR and UART/PLIC ISR → JSON/Markdown evidence. The UART base is 0x11000000,
so running generic virt cannot satisfy this board contract.

The Linux path additionally boots OpenSBI 1.6 and Linux 6.12, executes an original
static initramfs workload, and powers off. The original shared ELF ran on both
QEMU/Linux and gem5 SE with the same hash; the acceptance scopes remain distinct.
SystemC and RTL hybrid runs additionally require USP_TLM_RTL_IRQ_OK and independently
verify trace write/readback/IRQ state transitions. Repeated RTL traces matched.

Observed full local gate on 2026-11-12: **223 tests passed, zero failed/skipped**, including the
shared QEMU/Linux and gem5 ELF test, both TLM/RTL modes, and real CLI SIGTERM
cleanup of its owned guest process. Raw gem5 observations
include simInsts=3012 and simTicks=80400000 under explicitly uncalibrated assumptions;
these are simulator outputs, not estimates or silicon measurements. The required
native/source tests remain independent of hosted CI availability.

Component unsigned-longint parameters now elaborate through a bounded expression
interpreter, dependency graph and per-instance overrides with resolve/bounds checks.
The rv64-minimal UART's SPAN*UNIT window requires an actual override from 128 to
256 bytes. Its resolved values are retained in immutable IR. All three generated
machines and affected firmware were regenerated/rebuilt explicitly; native Zephyr,
FreeRTOS, Linux, gem5 and hybrid/workspace gates passed. The 32 new parameter tests
include invalid input, cycles and instance isolation. General parameter types,
full expression semantics and translations are still incomplete.

The checked-in workspace.yaml executes ten targets, including two scoped system bring-ups:
hierarchy Zephyr, base Zephyr, FreeRTOS, Linux, gem5 SE, FIFO TLM/RTL and source IP.
All ten passed in one workspace acceptance run. Aggregate evidence hashes every
retained target artifact; modified raw logs and false full-completion claims are
rejected. Missing builds are ERROR rather than skipped. Local promotion requires
the full matrix. See milestone-checklist.md for the item-level open/verified ledger;
selected-workspace success does not close the remaining master requirements.

Structural hierarchy resolves model views through designInstantiation and
designConfigurationInstantiation, with explicit configured-view selection,
context-sensitive cycle checks and full leaf paths retained in immutable IR.
The rv64-hierarchy example passes XSD, deterministic/negative import tests and
actual custom QEMU/Zephyr UART/timer/PLIC boot. A two-wrapper/configuration fixture
also elaborates correctly. Wrapper parameter propagation, physical ports, wiring
and general hierarchical address translations remain unsupported.

Scalar register/field metadata now passes bounds/alignment/overlap/access checks
and exact GCD model conformance before lowering. The generated soc_registers.h
feeds the real Zephyr GCD driver; all four machines, affected firmware and native
source IP models were explicitly regenerated/rebuilt. The full native matrix
passes with that header-driven guest. Eleven new tests cover canonical IR/schema,
header generation, invalid metadata/model contracts and legacy-backend rejection.
A newly built, separately installed wheel produced an identical generation
manifest and passed the real dual-IP guest using the verified native binaries.
General register side effects, arrays/files and mode-dependent access remain unsupported.

The nine-role source IP profile additionally generates the actual selected
Chipyard 1.13.0 GCD classes using Chisel 6.5.0/Scala 2.13.12/CIRCT 1.62.0, then
Verilates that output alongside the actual OpenTitan GPIO/TL-UL register peripheral
at silver-v5. An original explicit GCD MMIO/completion-IRQ shim and GPIO pin
loopback connect both to QEMU's PLIC. Guest vector results 6/6/255/1 and real GPIO
edge/W1C/readback passed; repeated transaction traces matched byte for byte.
Host conformance passed busy/illegal accesses, reset, malformed protocol, TL-UL
response backpressure and command-integrity failure with the actual alert output.
Source-model artifact tampering and real timeout/owned-process cleanup are tested.

The isolated generator uses SBT 1.10.7 because upstream SBT 1.8.2 failed on this
host's Java 21. The original Chipyard checkout/classes remain unchanged. A fresh
empty Scala/Maven cache resolved all 63 locked compilation artifacts, generated
RTL and built the native model successfully. Public Chipyard/OpenTitan checkouts
were reused; this was not a full Chipyard submodule/Rocket or Earlgrey build.

A separately installed wheel generated this source IP hardware profile, rebuilt
its actual Chisel/OpenTitan native models and passed the guest GCD/GPIO IRQ test.
That check reused the verified QEMU binary and Zephyr ELF, rather than claiming a
second complete SoC source build. All packaged generator/native resources worked
outside the editable Python installation.

FreeRTOS Kernel 11.2.0 now uses the unmodified source-pinned GCC RISC-V port and
an original generated software BSP. Actual guest acceptance observed 30 ticks,
30 machine-timer interrupts, 32 switches, progress from both nonyielding register
workers and one UART/PLIC interrupt. Real assertion-failure and tick-disabled
guests were correctly reported FAILED; a successful normal guest exited zero.
Those worker counts are functional evidence, not performance calibration. See
freertos.md for the static allocation, ISA/context and unverified API boundaries.

A clean source-only export from committed Git files installed its frozen dependency
lock, generated and compiled QEMU, built the external Zephyr BSP and passed its
boot/IRQ tests. That cold check intentionally omitted Linux/gem5 from its selected
suite; full native validation used the prepared workspace. A separately installed
wheel also generated the custom project artifacts successfully. No fresh cloud
snapshot restore or hosted Actions success is claimed.

A separately installed wheel containing the FreeRTOS resources generated its own
hardware artifacts, rebuilt the actual kernel/BSP ELF and passed the real guest
preemption/PLIC test using the previously verified QEMU binary. This validates
package resources and installed CLI execution; it is not another QEMU source build.

Run bundles are ignored and contain source/input/compiler/model/template/ELF/binary
hashes and raw logs. Historic failures are retained. Current compiler changes
invalidate old generation manifests until explicit regeneration/rebuild.

## External blockers and unverified claims

Yocto's downloads.yoctoproject.org and download.savannah.gnu.org, and GitHub's
api.github.com, all received proxy CONNECT 403 on 2026-11-12. Hosted CI/protection
API checks are blocked through that route. A draft adding api.github.com was saved
with both existing Yocto domains preserved. Review/Save/Publish remains necessary;
the draft does not apply runtime policy.
The earlier configuration-service error is resolved: a restricted-network draft
adding both domains was saved and read back, preserving package-manager presets.
The running instance still reports revision 1 with no custom domains; draft save
does not apply or publish the policy. Review/Save/Publish in environment settings
remains outstanding. Subsequent source requirements must be diagnosed rather than
bypassing the policy. Parsing required
BitBake execution outside the command sandbox (user namespaces) and umask 022;
no host security/sanity or artifact checks were disabled. Debian 13 is not a
validated Poky 5.0 build host; this warning remains explicit.

Cloud install/start instructions were also saved and read back exactly. Their
current-instance installation refresh and ten real legacy runtime tests passed;
startup uses the already validated native tools without a persistent service.
This does not claim publication or validation after a fresh environment restore.

Hosted GitHub Actions completion and main protection are distinct from local tests.
Full Chipyard/Rocket SoC generation, complete OpenTitan/security integration, µC/OS,
general IP-XACT wiring/translation and full-system gem5 are not implemented.
No silicon/security guarantee or calibrated performance equivalence is implied.

## AI_SOC/AIZ_SOC and OpenROAD extension

Both sanitized architecture targets pass offline XSD and metadata elaboration
(46/37 instances). Separate nine-role bring-up subsets pass actual generated
QEMU/Zephyr boot, timer/PLIC and real Chipyard GCD/OpenTitan GPIO IRQ tests.
The independent pinned Garnet meshes pass with 16/8 routers: observed injected
and received packets are 7552/7552 and 3776/3776, average hops 2.474576 and 1.727754.
The standalone initialization adapter fixes explicit 64B cache metadata and a
dummy DataBlock constructor; the reviewed patch/native binary are hashed and the
original upstream tracked tree is restored. No live CPU/RTL coupling is claimed.

Native Yosys 0.52 produces 610 generic GCD cells and passes equivalence. The same
actual source leaf completes the pinned OpenROAD Nangate45 flow to GDS, with
zero detailed-router DRC, setup/hold TNS=0 and positive worst slacks. Both system
acceptances pass their four implemented stages, including snapshots of that one
verified reference layout. The ten-target workspace and artifact verification
pass. Full heterogeneous SoC execution, full RTL/ASIC and foundry signoff remain
open; see dual-soc.md and openroad.md for exact scope and original evidence.

## Complete functional RTL test-chip flow

Separate nine-component profiles now execute actual Ibex-owned RV32 firmware with
ROM/SRAM, OpenTitan UART/GPIO/timer, Chipyard GCD and original OBI/TL-UL fabric.
Both whole tops completed synthesis through Nangate45 GDS and passed the reference
setup/hold and router-DRC gate. Both post-route binaries then executed the same
firmware with exact UART equality to RTL across two boots/reset sequences. SRAM
word/byte access, an unmapped load exception and actual timer/GPIO/GCD interrupts
passed. Short cycle limits failed as required. Both portable acceptance bundles
passed hash and cross-stage identity verification.

Observed evidence is results/AI_SOC-rtl-complete-first and
results/AIZ_SOC-rtl-complete-first; actual layouts are
build/NAME-full-testchip-asic. See rtl-testchips.md for configuration, final metrics,
GDS identities and reproducible commands. These are complete small test-chip flow
fixtures, not completion of the original 46/37-instance designs. Final STA still
reports 14/11 max-capacitance violations; no production/electrical/PVT closure,
live Garnet interconnect, RTOS on these RTL CPUs or manufacturing signoff is claimed.

The complete local native gate passed all 223 tests in 84.82 seconds, including
both test-chip acceptances and the existing ten-target workspace. Static typing,
lint/format and all 26 documentation checks passed. A separately installed wheel
contained all six test-chip resources, executed both actual RTL and mapped
binaries with two boots each and verified both portable bundles. That packaging
check reused the actual compiled artifacts; it does not claim a second source
or physical build. Hosted EDA execution remains unobserved.
