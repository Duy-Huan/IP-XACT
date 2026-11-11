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
| 5 gem5 | Verified SE workload subset | Same exact RV64 ELF executed under QEMU/Linux and gem5; guest exit zero and real raw stats; full-system board remains unsupported |
| 6 SystemC/RTL/hybrid | Verified limited peripheral | Real guest MMIO/readback/PLIC IRQ in TLM and OpenTitan FIFO RTL modes; deterministic replay; no DMA/coherence/general time correlation |
| 7 maturity | Partial foundation | Version, rules, native CI, reports and support docs; no universal industrial release or IDE claim |

The actual initial MVP sequence is IP-XACT → typed IR → exact binding → generated
QEMU C → compiled usp-rv64 → generated Zephyr board/DTS → west ELF → real UART,
timer ISR and UART/PLIC ISR → JSON/Markdown evidence. The UART base is 0x11000000,
so running generic virt cannot satisfy this board contract.

The Linux path additionally boots OpenSBI 1.6 and Linux 6.12, executes an original
static initramfs workload, and powers off. The original shared ELF ran on both
QEMU/Linux and gem5 SE with the same hash; the acceptance scopes remain distinct.
SystemC and RTL hybrid runs additionally require USP_TLM_RTL_IRQ_OK and independently
verify trace write/readback/IRQ state transitions. Repeated RTL traces matched.

Observed full local gate: **85 tests passed, zero failed/skipped**, including the
shared QEMU/Linux and gem5 ELF test, both TLM/RTL modes, and real CLI SIGTERM
cleanup of its owned guest process. Raw gem5 observations
include simInsts=3012 and simTicks=80400000 under explicitly uncalibrated assumptions;
these are simulator outputs, not estimates or silicon measurements. The required
native/source tests remain independent of hosted CI availability.

A clean source-only export from committed Git files installed its frozen dependency
lock, generated and compiled QEMU, built the external Zephyr BSP and passed its
boot/IRQ tests. That cold check intentionally omitted Linux/gem5 from its selected
suite; full native validation used the prepared workspace. A separately installed
wheel also generated the custom project artifacts successfully. No fresh cloud
snapshot restore or hosted Actions success is claimed.

Run bundles are ignored and contain source/input/compiler/model/template/ELF/binary
hashes and raw logs. Historic failures are retained. Current compiler changes
invalidate old generation manifests until explicit regeneration/rebuild.

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
