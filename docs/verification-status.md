# Master-spec phase status

The owner objective is incorporated into agent.md; the raw attached master document
is not published. Statuses are scoped to the selected reference and real acceptance,
not a claim that every future standard/backend feature is implemented.

| Phase | Status | Actual evidence / remaining boundary |
|---|---|---|
| 0 feasibility | Verified selected profile | Exact public QEMU/Zephyr source audit, real generated machine, guest timer/UART/PLIC |
| 1 compiler/IR | Verified MVP subset; broader requirements partial | Official 2022 XSD; typed flat IR and strict CPU/boot/IRQ/address-space contracts; generic hierarchy, translations, buses, views, parameters and register semantics remain unsupported |
| 2 models/QEMU | Verified selected profiles | Versioned exact model catalog/lock; generated and compiled C machine; ROM/ELF reset and relocated UART; separate explicit hybrid profile |
| 3 Zephyr MVP | Verified current workspace | Generated board/DTS, west build, actual CLINT timer ISR and UART→PLIC ISR; cold rebuild/hosted CI tracked separately |
| 4 Linux/Yocto | Direct Linux verified; Yocto image blocked | OpenSBI + Linux boot to original userspace; generated meta-usp parsed successfully (924 recipes, MACHINE=usp-rv64); upstream dependency fetch rejected by proxy |
| 5 gem5 | Build in progress | Source-pinned RISCV SE adapter and shared arithmetic/write/exit workload; no successful gem5 run claimed until report passes |
| 6 SystemC/RTL/hybrid | Verified limited peripheral | Real guest MMIO/readback/PLIC IRQ in TLM and OpenTitan FIFO RTL modes; deterministic replay; no DMA/coherence/general time correlation |
| 7 maturity | Partial foundation | Version, rules, native CI, reports and support docs; no universal industrial release or IDE claim |

The actual initial MVP sequence is IP-XACT → typed IR → exact binding → generated
QEMU C → compiled usp-rv64 → generated Zephyr board/DTS → west ELF → real UART,
timer ISR and UART/PLIC ISR → JSON/Markdown evidence. The UART base is 0x11000000,
so running generic virt cannot satisfy this board contract.

The Linux path additionally boots OpenSBI 1.6 and Linux 6.12, executes an original
static initramfs workload, and powers off. The original shared ELF is prepared for
gem5 comparison; direct Linux and SE execution have separate acceptance scopes.
SystemC and RTL hybrid runs additionally require USP_TLM_RTL_IRQ_OK and independently
verify trace write/readback/IRQ state transitions. Repeated RTL traces matched.

Observed local core/native suite at this checkpoint: 78 passed, zero skipped; later
checks may add tests, so use generated JUnit for the current exact count. Historic
v0.1 evidence remains separate. Run bundles are ignored, with source/input/compiler/
model/template/ELF/binary hashes and raw logs. Current compiler changes deliberately
invalidate earlier generation manifests until regenerated/rebuilt.

## External blockers and unverified claims

Yocto's required downloads from downloads.yoctoproject.org and
 download.savannah.gnu.org received proxy CONNECT 403. A draft network update could
not be confirmed because the configuration service returned INVALID_ARGUMENT.
Those domains must be permitted through environment settings; subsequent source
requirements must be diagnosed rather than bypassing the policy. Parsing required
BitBake execution outside the command sandbox (user namespaces) and umask 022;
no host security/sanity or artifact checks were disabled. Debian 13 is not a
validated Poky 5.0 build host; this warning remains explicit.

Hosted GitHub Actions completion and main protection are distinct from local tests.
Chipyard generation, complete OpenTitan/security integration, FreeRTOS/µC/OS,
generic IP-XACT hierarchy/translation and full-system gem5 are not implemented.
No silicon/security guarantee or calibrated performance equivalence is implied.
