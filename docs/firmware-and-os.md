# Firmware and software bring-up

All supported software paths use real guest execution and record raw serial,
return code and artifact identity. Keep proprietary firmware and run bundles
outside Git. Each backend/OS acceptance has its own scope.

## RV64 bare-metal compatibility path

`scripts/build_firmware.sh` builds original `boot.S` with an external RISC-V ELF
toolchain, no C runtime and an entry at 0x80000000. The legacy virt runner polls
NS16550 transmit-ready, requires `SOCSIM_BOOT_OK` and a success finisher. Regression
also executes a nonterminating guest and a failure-finisher guest, checking timeout
cleanup and simulator exit classification. It does not establish OS/IRQ behavior.

## Generated Zephyr BSP

The custom `usp-rv64-v1` profile generates an external Zephyr 4.2.0 board, DTS,
Kconfig and original conformance application from the validated hardware IR. The
real west build boots on the generated machine, with a UART deliberately relocated
from upstream virt. Acceptance requires guest boot, a CLINT timer ISR, an NS16550
UART interrupt delivered through PLIC and a success finisher. See
[rv64-minimal](examples/rv64-minimal.md) for commands and exact source pins.

The separate hybrid profile additionally verifies MMIO/readback/PLIC IRQ against
SystemC TLM and OpenTitan FIFO RTL. It is not a general peripheral bridge. A DTS
file or a preexisting virt board boot alone does not satisfy either profile.

## FreeRTOS and other RTOS

The selected FreeRTOS Kernel 11.2.0 GCC RISC-V port has a generated RV64 M-mode,
single-hart BSP with static task storage and no hosted libc/heap. It verifies
actual tick-driven preemption between two nonyielding tasks, saved-register state,
and UART/PLIC notification from an ISR to a waiting task. Negative guests exercise
assertion failure and disabled-timer timeout. See [FreeRTOS](freertos.md) for source
and compiler pins, commands, evidence and unverified driver/context features.

For `uOS`, the intended product (for example µC/OS-II or µC/OS-III), its version
and license still need selection before an integration can be specified. It is not
assumed to be compatible with Zephyr or freely redistributable. No such source is
downloaded or runtime support claimed.

## Direct Linux/OpenSBI

Linux 6.12 and OpenSBI 1.6 use the generated RAM/IRQ/timer/UART DTS. A RISC-V Linux
GNU compiler/linker is required; the bare-metal linker cannot build the vDSO and
is rejected by a preflight probe. ROM enters OpenSBI in M mode, which starts the
S-mode kernel and the original initramfs PID 1. PID 1 executes the same static
arithmetic/write/exit ELF used by gem5 SE, verifies status, emits
`USP_LINUX_USERSPACE_OK` and requests poweroff. Cross-engine acceptance compares
the exact ELF hash; it does not imply full hardware/topology equivalence.

```bash
export USP_FETCH_LINUX=1
bash scripts/fetch_upstreams.sh
export CROSS_COMPILE_LINUX=riscv64-linux-gnu-
bash scripts/linux_bringup.sh
```

## Yocto image: blocked

The generated `meta-usp` layer targets scarthgap/Poky 5.0.9, supplies its own
`usp-rv64` MACHINE and generated DTS, a pinned kernel recipe, minimal image and
original console smoke service. It does not inherit qemu virt device topology.
Parsing has succeeded for the target machine (924 recipes), but the image has not
been built or booted. Download policy denies at least downloads.yoctoproject.org
and download.savannah.gnu.org; both were rechecked on 2026-11-12 and returned proxy
CONNECT 403. Further source requirements must be diagnosed as BitBake progresses.
Never disable checksum, TLS, namespace or sanity checks to make a build pass.

BitBake parsing required execution outside the command sandbox (user namespaces)
and process umask 022. Source oe-init-build-env without shell nounset. Debian 13 is
not a validated Poky 5.0 host; retain that warning or select a supported production
build host. Reuse downloads/sstate without embedding credentials. Secure boot,
signing, lifecycle and anti-rollback remain independent work requiring dedicated
positive and negative tests with synthetic development keys.
