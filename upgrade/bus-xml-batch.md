# Standard bus intake and hosted observation batch: 0.3.0a4

Continue M1 with real IEEE 1685-2022 bus/abstraction/interface/address-space/
segment/subspace/interconnection XML. Preserve typed original graph facts in
canonical native IR v5, derive its static routing projection and consume the
same IR in HW/SW lowerers. Support the explicit native functional adapter only;
reject unknown protocols and unsupported timing/ownership semantics.

Required focused checks: XML/fabric equivalence, exporter/XSD round trip, graph
tamper and unsupported input rejection, actual both-engine five-workload flow
for AI_SOC/AIZ_SOC with byte-identical bare-metal ELF to equivalent fabric input.
Freeze the complete batch before clean minimal CI and the full mandatory gate.
Publish v0.3.0-alpha.4 only after checked main promotion; no speculative tag.

M9 infrastructure adds workflow-reported Git notes with exact source/run/job
outcomes and source release asset hashes. Test transport in isolated repositories;
query the real origin after push. No record is UNOBSERVED, never passed. Notes
are repository-reported evidence, not independent API attestation. No extra
development branch or remote forced update is required.

M1-M9 remain partial: general AXI/TileLink, Rocket/BOOM, full Earlgrey, timed
DMA/NoC ownership, Yocto, 46/37-instance implementations, cold/Podman and complete
release qualification need independent future acceptance.
