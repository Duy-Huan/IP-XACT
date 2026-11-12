# gem5 architectural workload scope

The adapter pins gem5 v24.1.0.3 at b9da2bfe1e21f6efa2730edd4e80e58c5d965458.
It uses the source-verified RiscvTimingSimpleCPU, SystemXBar, SimpleMemory and
SEWorkload APIs. Configuration comes from the supported IR's CPU/memory contract,
with explicit assumptions: one hart, 1 GHz, no caches and 10 ns SimpleMemory.
These statistics are model-dependent and uncalibrated. Wall time is separate.

This is **syscall emulation**, not a full-system implementation of the entire SoC.
The report names ROM, CLINT, PLIC, UART and finisher as unmodeled. Requesting gem5
through the normal full-SoC QEMU plan still fails rather than silently substituting
SE. Only the explicitly selected gem5 SE command exercises this restricted profile.

The original arithmetic/write/exit RV64 ELF is placed in the Linux initramfs and
executed as a child of the original PID 1. The same exact ELF is passed to gem5 SE;
its bytes are checked against the Linux build manifest. Observable success requires
USP_SHARED_WORKLOAD_OK, workload exit zero, the expected m5 termination event and
nonzero real simInsts. Raw stats.txt/config files are preserved. No host-side marker
or estimated instruction count substitutes for an actual run.

```bash
uv sync --frozen --extra dev --extra bringup --extra models
USP_FETCH_LINUX=1 USP_FETCH_MODELS=1 bash scripts/fetch_upstreams.sh
# First complete the generated QEMU/Zephyr and direct Linux guides.
usp gem5 build --source-gem5 .tools/upstream/gem5 --out build/gem5 --jobs 4
usp gem5 run examples/rv64-minimal --generated build/rv64-minimal \
  --build-dir build/gem5 --out results/gem5-001
```

Gem5 requires Python development headers/library, GCC/G++, SCons and zlib headers.
The initial RISCV source build is substantially more expensive than the small
workload; reuse valid outputs. Relocated Python library paths are passed only to
the build/run subprocesses. Building normally generates ignored files under the
pinned upstream build directory but must leave tracked upstream source clean.

The separate unified-soc.md adapter now supplies a verified Zephyr full-system
board with actual Chipyard/OpenTitan RTL through SystemC, shared IR/ELF and real
MMIO/PLIC interrupts. Its acceptance is independent of this SE result. Full-system
Linux and CPU/RTL timing correlation remain unverified.
