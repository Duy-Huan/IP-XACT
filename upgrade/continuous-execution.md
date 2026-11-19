# Continuous execution towards M8-M9

Owner direction: 2026-11-19. Baseline a05dd99 / v0.3.0-alpha.4 has 664 mandatory
local passes and remains unchanged on main. All feature implementation is on dev.
No future version/tag is pre-created. Preserve immutable prior evidence.

Execution workstreams, in dependency order:
1. Complete canonical contract gaps (M1-M3): explicit bus/CPU/address/domain
   capability records and equivalent intake/lowering; never infer behaviour.
2. Extend qualified HW/SW combinations (M4-M5): investigate pinned Rocket AXI
   source generation and an actual CPU-owned adapter, then eligible RTOS/MMU/OS;
   qualify Yocto only with a real fetched/built/booted image.
3. Build actual transaction-path NoC/DMA/time and calibrated performance (M6-M7).
4. Close every 46/37-instance source/model/driver/RTL/physical exit (M8), then
   cold/Podman/OCI/hosted/release qualification (M9).

A technical dependency may be investigated early without closing its dependent
milestone. Existing native single-hart XML, SMP, RTOS and RTL profiles are retained.
Checkpoint commits are not release acceptance or permission to skip mandatory
local CI. Freeze the implementation batch before aggregate qualification/push.

Immediate resource preparation: persist/recover exact SDK image identities while
using temporary cache headroom; keep original OCI archive, logs, layouts and actual
runtime executables. No private attachment or company reference data is imported.

Development findings, not closed exits:
- Actual single RocketTile AXI32, all three low-PA profiles, bare-metal and
  PMP/Sv39 M/S/U passed on both memory engines. FreeRTOS has six positive and
  twelve expected-negative engine/map outcomes with durable exact archives.
- Original RV64IMAC Zephyr BSPs (smoke/timer-disabled) build for all three maps;
  actual expanded CPU/software/protocol suite is in progress.
- Cold concurrent first-in-process XML schema compilation failed in an earlier
  threaded engineering harness (raw failure retained). The current candidate
  serializes factory initialization and passes fresh-process four-thread intake.
  Fresh generated identities and exact explicit engine/software reuse are still
  required for the aggregate qualification; never rewrite prior hashes.
- Next CPU work: Linux boot ABI and long-run trace resources, generic native IR
  binding, reset/error coverage, then BOOM. Full M5 and M8 remain open.

`49381fe` focused Rocket checkpoint: 101/101 passed, zero skips, 644.58 seconds;
all raw outputs are in an exact verified archive with JUnit/provenance. This is
not the aggregate gate. Following work adds lossless long-trace storage and an
actual OpenSBI/Linux RTL boot investigation; no userspace success is claimed yet.

Rocket follow-up: the original gem5 Linux probe at `fd2735b` reached actual
fork/exec userspace and native poweroff, with 73,709,602 RTL edges and 34,170,663
retired instructions and independently passing verification. Its exact reversible
archive is retained; the corresponding QEMU probe timed out. This is a scoped
32MiB software-memory boot profile on 128MiB hardware RAM, not full M5/M8.

The next development checkpoint adds source-bound Linux builds/platform selection,
mandatory both-engine/all-map Linux promotion inputs, FENCE.I and optional QEMU
RAM burst snapshots. An actual simultaneous AXI read/finisher shutdown failure
was fixed by committing the real engine finisher only after accepted requests
drain. Focused checks: 60 contract tests and five real all-map scalar/burst/fault
tests passed with no skips. Linux/complete Rocket native gates remain in progress;
do not translate this checkpoint into aggregate CI, main promotion or a new tag.

Native source-CPU development now has typed IR v6 and actual arbitrary-instance
QEMU/gem5 memory graphs. A focused probe passed AI_SOC/AIZ_SOC 12/11-instance
fixtures concurrently on both engines, with exact shared IR/ELF, independent
source IP state/IRQs and zero native gem5 ISS counters. The AIZ_SOC execution
entered through standard bus XML. Full software/reset/error and aggregate gates
remain pending; the full 46/37 inventories and broad M8–M9 exits stay open.
