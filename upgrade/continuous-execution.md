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
- Cold concurrent first-in-process XML schema compilation failed in one threaded
  engineering harness (raw failure retained). Preloading validated inputs avoids
  that harness race, but does not qualify concurrent cold intake. Address schema
  construction synchronization in the next compiler batch with fresh generated
  identities and exact explicit engine/software reuse; never rewrite hashes.
- Next CPU work: Linux boot ABI and long-run trace resources, generic native IR
  binding, reset/error coverage, then BOOM. Full M5 and M8 remain open.
