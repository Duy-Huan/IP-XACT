# Research direction and evidence boundaries

Owner objective: a SoC Compiler and Canonical SoC IR connect IP-XACT 2022 to QEMU,
gem5, SystemC, Chipyard and OpenTitan for executable hardware/software co-design
and design/spec evaluation. The research question is whether explicit contracts
and lowering can make heterogeneous model composition reproducible, diagnosable
and useful across functional, architectural and RTL fidelity levels.

The owner supplied nine paper URLs, indexed in ref/papers.json. All initial
requests received proxy CONNECT 403; **none has been downloaded or read**. Titles,
contents, availability, licenses and claimed contributions remain unverified.
No design claim in this document is attributed to those unread sources. The
proposed novelty/research gap is a hypothesis, not an established priority claim.

## Existing executable baseline

The current compiler already validates the supported IP-XACT 2022 subset and
produces immutable hardware IR. A separate immutable execution plan now records
CPU provider/model, selected engine, exact model/source bindings, exclusive memory
ownership, routed addresses/IRQs, time domains and unsupported capabilities.
SystemC GCD/GPIO run in the same guest system as the selected QEMU/gem5 CPU.
Actual traces check transaction order, width/address routing, IRQ levels and
monotonic annotated time. Native and analytical performance have distinct labels.

This is an executable bounded baseline. It is not a universal IP-XACT compiler,
calibrated multi-fidelity platform, distributed conservative scheduler, generic RTL
CPU integration or complete SoC signoff. Reference test cases are regression data;
CPU/engine/workload choices are configuration data subject to available adapters.

## Questions for the literature review

For each actual paper, extract evidence with page/section references rather than
inferring capabilities from its title:

| Dimension | Comparison question |
|---|---|
| Frontend / IR | What is imported, canonicalized, checked and generated? |
| Model composition | Which CPU/peripheral/bus/master roles actually coexist? |
| Ownership | Who owns RAM, DMA, caches, interrupts and reset state? |
| Time | Functional, annotated, quantum-synchronized or cycle-accurate? |
| Software | Bare-metal, RTOS, bootloader, Linux; which drivers were exercised? |
| Fidelity / metrics | What counters, calibration/error bounds and scope are proven? |
| Automation | Supported models, missing-model diagnostics, provenance and rebuild policy? |
| Evaluation | Artifacts, reproductions, baselines, scale and failure cases? |

Only after retrieval/review should ref notes classify direct overlap, reusable
techniques, remaining gaps and candidate contributions. Do not claim that combining
tool names or generating directories itself establishes novelty. A paper with a
similar IR/compiler or co-simulation flow must be compared directly.

## Engineering and evaluation roadmap

1. Extend explicit CPU-master contracts for Chipyard and custom RTL, with actual
   bus/clock/reset/IRQ/counter interfaces. Reject incompatible ownership/ISA/boot
   combinations before running; never substitute a CPU silently.
2. Extend the current blocking transaction boundary toward scheduler-independent
   event/time contracts: causality, lookahead/quantum limits, ordering, backpressure,
   interrupt latency, reset and checkpoint behavior. Implement/test one supported
   synchronization policy at a time.
3. Add DMA/shared-memory/coherence and live NoC ownership only with native
   executable tests. Independent Garnet traffic is not a coupled interconnect.
4. Add design-stage exploration over configurable CPU/cache/memory/IP alternatives,
   preserving the same software-visible contracts and measuring model error/cost.
   Scope ROI and calibration; separate estimates from native model counters.
5. Extend compiler semantics (bus/abstraction/port graphs, initiator translations,
   parameters/register side effects) with positive/negative fixtures and compatible
   model adapters. Retain deterministic canonical IR and exact source provenance.
6. Evaluate against directly reviewed related systems using reproducible cases:
   integration effort, diagnostic correctness, model reuse, boot/driver coverage,
   trace/functional conformance, speed/fidelity/error and design-decision usefulness.

A defensible contribution would include the contracts/compiler/IR, executable
adapters, reproducible cross-engine experiments and a measured evaluation showing
what existing approaches do and do not already cover. Publication/research novelty
remains an external scholarly assessment, independent of local regression success.
