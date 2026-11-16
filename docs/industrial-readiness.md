# Industrial applicability and the requested CHIA comparison

Review date: 2026-11-16, Asia/Ho_Chi_Minh. Baseline source: main/dev 0375df2.
The accompanying change makes AI_SOC/AIZ_SOC executable projects visible under
examples/ without expanding supported hardware semantics.

## Decision

The current tool is a candidate for a controlled engineering pilot: early
firmware bring-up, selected source-IP driver/interrupt tests, explicit model
composition and repeatable reference experiments. It is not yet qualified as a
general industrial SoC design/verification platform. It cannot execute an
arbitrary RISC-V SoC described in IP-XACT without additional implementation work.
There is no evidence that the complete 46/37-instance AI_SOC/AIZ_SOC targets meet
the owner's end-to-end acceptance objective.

Passing the supported regression establishes those tested profiles, not a
requirements coverage percentage, full industrial qualification or silicon
signoff. Application in a real project requires a defined use case, a validated
model library and correlation against that project's reference implementation.

## CHIA reference status

The owner identified CHIA as https://arxiv.org/abs/2606.27350. The direct requested
PDF URL is https://arxiv.org/pdf/2606.27350. Retrieval in this review failed with
HTTP/proxy 403 (curl return 22, reported final HTTP code 000). The paper has not
been read, its title/version/content have not been independently verified, and
no claim here describes CHIA's architecture, results, advantages or limitations.
The earlier reference ledger also records this source as unread.

A defensible side-by-side comparison requires the actual paper or accessible
authoritative source. Extract its stated CPU/IP/protocol support, abstraction and
timing levels, synchronization/memory ownership, workload/OS scope, configuration
generality, hardware correlation, benchmark scale, artifacts and limitations.
Record page/section evidence for each claim. Then run comparable workloads on the
same hardware contract before making relative accuracy or performance claims.
The industrial assessment below is based on this repository, independently of
any unverified attribution to CHIA.

## Code-level capability boundary

| Area | Implemented boundary | Consequence for industrial use |
|---|---|---|
| Hardware intake | Safe 2022 XSD and typed IR; bounded hierarchy, unsigned parameter and scalar-register subsets | XSD-valid XML is not proof of executable hardware; general bus/translation semantics remain open |
| Custom machines | contracts.py defines exact 7/8/9 role sets, a specific RV64 MSU/Sv39 contract, device sizes and IRQ/address constraints | Even another instance of an existing supported peripheral requires extending the profile/compiler; this is not generic SoC composition |
| Model binding | pipeline.resolve uses exact catalog VLNV/contracts and descriptors keyed by the fixed instance roles | A catalog entry or XML register map does not implement arbitrary behavior or generate a missing adapter |
| CPU selection | Four explicit native ISS adapters and two supported RV32 OBI/RVFI RTL CPU profiles | Rocket/BOOM and arbitrary AXI/TileLink/MMU CPUs are not automatically executable; unavailable requests must fail without ISS substitution |
| Unified source IPs | Actual Chipyard GCD/OpenTitan GPIO in a shared SystemC process with native QEMU/gem5 guest MMIO/IRQ | Useful driver/functional reference; not the complete Chipyard or Earlgrey SoC and not a working AI accelerator |
| Software | Native-ISS bare-metal/FreeRTOS/Zephyr/Linux/boot profiles; supported RTL bare-metal plus FreeRTOS/ucos2/ucos3/Zephyr BSPs | RTL MMU/Linux, general CPU/OS portability and full Yocto image acceptance remain open |
| Network/DMA | Separate native gem5 two-hart MI_example/Pt2Pt reference, six routers, actual DMA/cache data/IRQ tests | Does not close arbitrary protocol/topology, QEMU/RTL coherent initiators, IOMMU or asynchronous whole-system timing |
| Performance | Native gem5 counters, native RTL clocks/RVFI, separately labelled QEMU analytical estimates | Counts describe the selected model and interval; no demonstrated calibrated prediction of an arbitrary target CPU/SoC |
| RTL/physical | Separate real nine-component test chips, reference public-library layouts and mapped-firmware acceptance | Does not synthesize/place/route the full architecture inventory from general IP-XACT; electrical/corner and full physical verification remain open |
| Maturity | Source/artifact identities, negative tests and workflow definitions | Fresh-runner rebuild, hosted required checks/protection, coverage closure and industrial qualification are separate evidence requirements |

The exact enforcement is visible in src/socsim/contracts.py (PROFILE_EXTRAS,
attach_contract), pipeline.py (resolve), cpu_platform.py (ADAPTERS/cpu_selection),
rtl_cpu_build.py (load_manifest), coherent.py (contract) and their actual runners.
The complete architecture manifests are architecture-only; their plan explicitly
sets full_system_complete=false. Candidate block names are not running models.

## What IP-XACT can and cannot supply

IP-XACT can describe identities, maps, interfaces and references to implementations.
Execution still needs compatible functional models or RTL, plus explicit reset,
clock, bus response, IRQ and memory/boot contracts. The compiler cannot derive an
AI algorithm, cache-coherence protocol, PHY or arbitrary device side effects from
an IP name and register addresses. Generic composition can be engineered over a
qualified model library; it does not mean automatic support for every SoC.

The current compiler should first separate instance identity from model role,
support multiple instances without adding a hard-coded profile, implement typed
bus/initiator/address-translation graphs, and select adapters by explicit
capabilities. This must be demonstrated with an unseen configuration, not a new
name for an existing board or a successful no-op model.

## Required industrial acceptance evidence

1. Freeze one product use case and hardware specification. Enumerate every IP,
   address/IRQ/reset/clock/power domain, source model, fidelity and owner. Unknown
   components fail explicitly; architectural inventory alone is not coverage.
2. Qualify adapters with reset, error, backpressure, ordering, DMA, permission and
   IRQ tests. Validate AXI/TileLink or other protocols with the chosen reference
   and appropriate assertions/VIP; test coherent multi-agent contention where used.
3. Boot the actual target software and exercise its real drivers. Include secure
   boot/update/recovery only where implemented and tested, and the selected Linux/
   Yocto image and RTL CPU path where required.
4. Define synchronization and error bounds. Measure representative ROI workloads
   against RTL/FPGA/reference hardware; report errors, model assumptions and
   performance/accuracy tradeoffs. Native simulator counters alone are insufficient.
5. For physical implementation, produce the actual selected RTL top and technology
   constraints. Retain multi-corner STA/electrical, appropriate physical DRC/LVS,
   CDC/RDC, power intent and power/IR/EM/DFT evidence as required by that product.
   Reference router DRC and a GDS file do not close this. The owner selected public
   PDK evaluation; no proprietary foundry prerequisite is invented for that scope.
6. Run reproducibly on a clean machine with dependency/source pins and auditable
   raw evidence. Track functional/code/assertion coverage and requirement exits;
   record unsupported/blocked cases and all observed failures separately.

## Fresh evidence in this review

The initial four example tests passed: file/IR parity for both projects and actual
same-ELF QEMU/gem5/SystemC runs for each chip. Each actual run requires real CPU
execution, GCD/GPIO results and interrupts, guest/model process success and
artifact verification; full_architecture_complete remains false. No new layout
place/route or fresh upstream source rebuild is implied by these runs.

Raw evidence is retained under results/chia-assessment-20261116, including a
case-by-case JUnit/report index and the exact tested source. The full required
regression result must be taken from its actual generated log/JUnit after it
finishes; the prior 427-test result is historical evidence, not this review's run.
Expected negative cases (unavailable CPU, extra unsupported IP, deliberately
failing firmware) are successful checks only when their specified rejection is
actually observed; their guest/platform status must not be relabelled PASSED.
