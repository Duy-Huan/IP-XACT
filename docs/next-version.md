# Next version: QA remediation and acceptance boundaries

This development increment is unreleased. It extends the accepted 0.2.0 package
without changing the generator's version identity or invalidating unrelated
accepted native artifacts. A new immutable version is cut after release acceptance;
neither successful selected tests nor this document means the master objective is
complete. The previous baseline was main 0e2c187 with 387 required tests passed.

Every material issue from the owner QA review is tracked below. “Delivered here”
means the stated bounded executable scope; broader acceptance remains open.

| QA area | Delivered here / existing evidence | Remaining exit criterion |
|---|---|---|
| Compiler/IR | Explicit two-hart/cache/NoC/DMA derived IR with per-initiator visibility; strict new profile validation | General 2022 bus/abstraction/view and initiator translation semantics; positive/negative executable models for each supported feature |
| CPU providers and buses | Existing real Chipyard Ibex/custom OBI-RVFI masters; no ISS substitution | Pinned Rocket and BOOM generation; source-backed TileLink/AXI response, reset, IRQ, caches/MMU adapters and real guest acceptance |
| RTL software selection | Delivered here: one platform YAML/plan/run/verify for bare-metal and all four existing kernels | RTL MMU/Linux/OpenSBI boot and driver acceptance on an appropriate CPU; cross-ISS remains unsupported |
| Unified IPs / Earlgrey | Existing GCD/GPIO in one SystemC guest; full-Earlgrey standalone candidate has ROM/flash/UART/timer/GPIO observations in ignored experiments | Checked-in reproducible full-top builder/SDK profile and same-SoC engine/RTL ownership adapter; independent candidate is not unified acceptance |
| Live NoC | Existing single-hart Zephyr four-router path; delivered here: two-hart six-router native Garnet path | QEMU and RTL-master transactions on a defined live network; topology sweeps and synthesizable counterpart |
| DMA and coherence | Delivered here: real native Ruby DMA, dirty-cache read/write, two-hart exchange, PLIC IRQ and descriptor-negative guest | Broader protocols, access-control/IOMMU policies, QEMU/RTL initiators, coherent OS workloads and concurrent-agent stress |
| Global timing/performance | Native gem5/RTL metrics preserved; coherent runs retain native event ticks | Defined scheduler synchronization/error bounds and ROI counters correlated against RTL/reference hardware; transaction time is not globally calibrated |
| Full AI_SOC/AIZ_SOC | Existing 46/37-instance architectural inventory and nine-component native/physical fixtures; new coherent fixture derives from their bring-up maps | Source, adapter, driver and end-to-end acceptance for every requested instance; no inventory-only or nine-component closure |
| Linux / Yocto | Existing native-ISS OpenSBI/Linux boot and generated Yocto layer | Successful pinned BitBake fetch/build/image, boot on both supported engines and console/custom-driver tests; network access must be applied, not merely drafted |
| Public-PDK ASIC evaluation | Existing both complete test-chip GDS, mapped firmware, positive reference setup/hold and zero router DRC | Resolve 14/11 max-capacitance findings, assess available public-library corners and record PVT/electrical limits; full inventories need actual RTL/layout |
| QA/release/CI | New coherence and unified RTL software gates are mandatory for local promotion and wired into manual workflows | Observe hosted jobs/protection, fresh-runner source rebuild and immutable release/wheel acceptance; local results do not establish hosted success |
| Research references | Existing nine-source ledger and bounded fetch tool | Retrieve/read actual documents after network access, record metadata/licenses, compare related work and measured contribution; no unsupported novelty claim |

Prioritize CPU bus/MMU contracts and full-top reproducibility after this increment.
Linux RTL depends on a privileged/MMU-capable CPU, not a renamed RV32 M-mode
fixture. Full inventories depend on actual public accelerator/controller/PHY
models, adapters and drivers; an automatically generated no-op cannot close them.
Public-PDK evaluation is the owner's selected ASIC scope; production foundry
manufacturing signoff is not a prerequisite or a completion claim.

Current reproducible commands and detailed limits are in coherent-dma.md and
rtl-software-platform.md. Run scripts/promote.sh with all required verified roots,
including USP_COHERENT_BUILD, only from a clean dev checkout. It executes the
entire supported regression before merging. Preserve all failed/negative guest
evidence and record the final actual test count in verification-status.md.
