# Firmware and software bring-up

## Implemented: RV64 bare-metal

`scripts/build_firmware.sh` builds `boot.S` with an external RISC-V ELF toolchain,
no C runtime and the example linker at 0x80000000. It polls NS16550 transmit-ready,
writes `SOCSIM_BOOT_OK`, then writes the SiFive success finisher. The runner uses
`-bios none`, no OpenSBI/bootloader and one hart. Integration regression also builds
a nonterminating guest and a guest writing the failure finisher. Their expected
failure outcomes test timeout cleanup and real simulator exit handling.

These tests cover CPU execution, RAM/ELF loading, UART access and finisher exit.
They do not cover interrupts, multitasking, Linux, DMA, secure boot or timing.
`socsim run` currently expects the smoke marker and finisher contract; a generic
workload acceptance mechanism is future work.

## Zephyr and RTOS integration contract

Zephyr is the first planned RTOS. Pin a supported Zephyr release, west manifest,
SDK and modules. Generate a board/DTS/Kconfig using the actual validated topology,
following that release's board format. Build hello_world, then timer/interrupt,
threading and driver tests. Boot in the selected backend and capture actual guest
assertions. A DTS file alone is not a BSP validation.

FreeRTOS needs a specific RISC-V port, timer source, interrupt/trap handler, heap
and linker contract. For `uOS`, first identify the intended product (for example
µC/OS-II or µC/OS-III) and its license; do not assume it is Zephyr-compatible or
redistributable. No external RTOS source is downloaded by the current setup.

## Yocto/Linux integration contract

Use a separate pinned Yocto workspace with exact poky, meta-openembedded and BSP
layer commits and a selected release series. Generate a real MACHINE/layer contract
from validated hardware: ISA/ABI/tune, UART console, RAM, IRQ/timer, DTB, kernel and
image format. Decide OpenSBI/U-Boot handoff and rootfs storage per board. Reuse
downloads/sstate caches without embedding credentials. Keep large builds outside
the platform package and size disk/RAM before BitBake builds.

Acceptance requires a real Linux console/login or self-test, guest device discovery
and chosen userspace/driver workload. QEMU functional success does not establish
gem5 FS, Chipyard RTL or OpenTitan platform equivalence. No current Zephyr, RTOS or
Yocto build/boot is claimed by this release. Secure boot signing, lifecycle and
anti-rollback require independent negative tests with synthetic development keys.

## v0.2 verified software path and remaining OS boundary

The earlier planned-only sections describe v0.1 history. The current custom profile
has a real generated external Zephyr 4.2.0 board, a west build and real timer/UART
interrupt checks. See the rv64-minimal guide for actual CLI commands and markers.

The direct Linux path pins Linux 6.12 and OpenSBI 1.6, uses a RISC-V Linux GNU
compiler/linker, and packages an original static initramfs. It is intentionally
small: PID 1 runs the same arithmetic/write/exit ELF used by gem5 SE, verifies its
status, prints USP_LINUX_USERSPACE_OK, then requests poweroff. The earlier direct
PID-1-only smoke was already observed passing; current shared-workload acceptance
is independently recorded in each new report.

```bash
export USP_FETCH_LINUX=1
bash scripts/fetch_upstreams.sh
export CROSS_COMPILE_LINUX=riscv64-linux-gnu-
bash scripts/linux_bringup.sh
```

The generated Yocto meta-usp layer targets scarthgap/Poky 5.0.9, supplies its own
usp-rv64 machine and generated DTS, a pinned kernel recipe, minimal image and
original console smoke service. It does not inherit qemu virt device topology.
Layer parsing has been exercised with the target machine, but image build/boot is
blocked by upstream download policy. It must not be described as a verified Yocto
image. At least downloads.yoctoproject.org and download.savannah.gnu.org need access;
additional sources must be diagnosed as BitBake progresses. No checksum, TLS or
namespace/sanity checks are disabled to make a build pass.

BitBake parsing required execution outside the command sandbox (user namespaces)
and process umask 022. The normal source command is not compatible with shell
nounset, so source oe-init-build-env without set -u. Debian 13 is not a validated
Poky 5.0 host; retain that warning or use a supported build host for production.
No FreeRTOS, µC/OS or secure-boot acceptance is claimed yet.
