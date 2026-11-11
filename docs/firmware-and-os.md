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
