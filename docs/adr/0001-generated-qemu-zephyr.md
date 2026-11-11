# ADR 0001: first custom machine and generated Zephyr board

The earlier virt smoke demonstrated tool availability but could not meet the new
master acceptance contract. We retain it as a legacy test and add an independent
QEMU 10.0.0 source-build profile with exact public source SHA and seven explicitly
bound roles. The generator produces actual C mapping/wiring and compiles it into
`usp-rv64`; UART is relocated to 0x11000000 to expose an accidental virt fallback.

Zephyr 4.2.0 supports an external board selecting its compatible qemu_virt_riscv64
SoC support. We reuse that upstream architecture/linker/driver code while generating
an independent board/DTS from validated metadata. The app tests both CLINT timer
ISR context and NS16550 TX-empty delivery through PLIC before finisher exit.

QEMU memory_region_init_rom requires a device owner or NULL in this revision;
a MachineState owner triggered a runtime assertion. The generator now uses NULL
as upstream board code does. This finding was validated through actual execution,
not hidden by suppressing the test. The failed run remains local evidence.

This decision does not expand supported hardware to arbitrary CPUs or IP. More
semantic constructs must be implemented and tested before new contracts are accepted.
The raw attached company document is kept outside Git; this ADR records only the
project-owned implementation decision and public upstream facts.

The custom UART model explicitly uses 3.6864 MHz input clock and baudbase 230400,
matching the generated DTS. We do not reuse upstream virt's differing baudbase for
this custom contract. Tests compare the generated software contract/header/DTS and
machine constants; actual guest UART and PLIC execution remain required.

Direct Linux uses a shared-capable Linux GNU linker; the bare-metal linker rejected
-shared while building the vDSO, so a preflight link probe now catches that mismatch
before a kernel build. The minimal PID 1's wait status is a 32-bit int per Linux ABI;
a long left stale high bits and correctly caused the new guest test to fail.

Debian SystemC 3.0.1 publishes stale pkg-config version 3.0.0. The adapter retains
that observed metadata and verifies the real 3.0.1 headers and link ABI with an
executable conformance model, rather than silently relabeling a different runtime.

The shared standalone workload initially used the generic ELF System V OSABI,
which caused gem5's upstream Linux-ABI assumption warning. The generated Linux
executables now declare the intended Linux OSABI using the Linux GNU elfedit tool;
the SE adapter checks it and refuses an unknown ABI. QEMU/Linux and gem5 then run
the same exact ELF without relying on that implicit operating-system fallback.
Extra CPU-model ISA extensions remain outside the workload's required RV64IMAC
subset and do not imply full architectural equivalence.
