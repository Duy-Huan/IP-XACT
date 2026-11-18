# Sequential native package/software batch: 0.3.0a2

This batch implements scoped work in the approved M1-M9 programme. It does not
mark their broader acceptance exits complete. Requirements and prerequisites in
milestones.yaml remain authoritative; the final annotated release receipt/JUnit
records actual candidate qualification after the aggregate gate.

| Milestone | Concrete batch work | Remaining broad exit |
|---|---|---|
| M1 | Shared strict CPU normalization; source/provenance-preserving materialization; IR-driven software/ownership validation | General bus/interfaces/translations and heterogeneous lowering |
| M2 | Source/license/VLNV/driver/register/RPC package locks; real SystemC reset/MMIO/IRQ/error probes; independent instances | Richer transaction widths/DMA and general package/register/driver semantics |
| M3 | Persisted plan, build, software, ordered run and recomputed verification; qemu/gem5/both; gem5-only firmware without QEMU | General adapter/CPU lifecycle beyond qualified native contracts |
| M4 | Real native RV64 single-hart FreeRTOS, Zephyr and explicit LP64 µC/OS-II/III; native OpenSBI/Linux/boot; two maps | SMP RTOS, arbitrary IP/CPU drivers, Yocto image and wider ports |
| M5 | Existing separately qualified Ibex/custom RTL paths retained by regression | Rocket/BOOM, AXI/TileLink/MMU and full Earlgrey adapter/source qualification |
| M6 | Existing live Garnet/coherent profile retained by regression | General IR lowering, unified DMA/network timing and synchronization |
| M7 | Actual per-hart QEMU execution/data events, explicit analytical costs, native gem5 counters and raw-source identity | ROI control, reference/silicon calibration and model error budgets |
| M8 | Both original inventories and separate native/9-component RTL flow fixtures retained | Implement/qualify every 46/37-instance model/driver/RTL counterpart |
| M9 | One final local lint/minimal/native CI cycle, checked promotion and version-matched source tag | Observed hosted CI, cold all-engine and new Podman/OCI/full-design qualification |

## Execution and evidence

Work on dev. Focused compilation and guest/protocol/negative probes guide fixes.
After implementation and documentation freeze, commit the exact candidate, run
workflow lint and clean Debian reproduction, then one mandatory native promotion.
A failed gate requires correction and rerun. Do not merge/push/tag an unqualified
candidate, suppress assertions or rewrite old artifact hashes. A passed scoped
batch does not close the still-open broad exits above.

The mandatory new scenarios are source-built AI_SOC packages/software, AIZ_SOC
relocation/BSPs, gem5-only execution, actual multi-hart QEMU/gem5 counters, package
source/binary/reset negatives, and FreeRTOS/µC/OS assertion/tick-disabled guests.
All prior native/RTL/Garnet/physical regressions stay mandatory. Software-only
boot/IRQ acceptance is explicitly distinct from package peripheral conformance.

Read [the execution guide](../docs/native-flow.md). Intermediate failed and passed
logs remain under ignored results/. The final receipt binds the actual source
tree, full test inventory, zero skips/errors/failures and immutable version tag.
GitHub source refs, release assets, hosted status and SDK publication are reported
separately; no future version or fabricated success is created.
