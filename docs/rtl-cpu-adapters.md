# Actual RTL CPUs with QEMU or gem5 memory engines

The selected real CPU RTL executes instructions in a SystemC/Verilator process.
QEMU or gem5 owns physical RAM, ROM, UART, CLINT and PLIC. Both actual Chipyard
GCD and OpenTitan GPIO remain shared SystemC RTL peripherals in the same SoC.
The native engine CPU object is only a passive IRQ context; it never substitutes
an ISS for the requested RTL CPU.

| CPU | Memory engine | Software | Performance |
|---|---|---|---|
| Chipyard Ibex blackbox and pinned upstream core | QEMU qtest or gem5 atomic master | RV32IMC bare-metal | Actual RTL clocks/RVFI |
| Custom RTL with normalized obi-rvfi32-v1 top | Either memory engine | Same RV32 contract | Actual RTL clocks/RVFI |
| Rocket/BOOM, arbitrary AXI/TileLink, other ISA/MMU | Needs separate adapter | Unsupported here | No substitute results |

The custom reference is a real Ibex with writeback/branch-target ALU enabled,
illustrating the custom source interface. It is not an independently designed CPU
or Rocket. A user core needs actual HDL and an explicit bus/RVFI wrapper; XML or
configuration cannot manufacture behavior. The current build also supplies the
pinned Ibex/primitive libraries. Custom source/include files must be enumerated
with hashes inside the manifest directory; unsupported top-port widths/directions
and unlisted custom includes fail before native compilation.

## Build and run

Chipyard commit 69eba860a352343e4ac6b6df0f3638a79a86ec78 pins ucb-bar/ibex-wrapper
to 1a030b1d20e0bfb3f8ca250549f4826f1d2b2c32, which pins lowRISC/ibex to
48f11c673348908c37d3eaa9bd1cd0a5b5c69253. The builder verifies the three clean
repositories and both gitlinks, compiles the unchanged upstream blackbox/core
with Verilator 5.032, SystemC and RVFI, and retains licenses and source hashes.
Cached CPU/master builds also bind the builder source hash. CPU verification
checks the exact three source pins, model and interface contract before launch;
modified provenance labels cannot make an obsolete build executable.
This CPU source revision is separate from the existing RTL test-chip source pin.

```bash
usp rtlcpu build --manifest models/cpus/chipyard-ibex.yaml \
  --chipyard /path/to/chipyard --wrapper /path/to/chipyard-ibex \
  --core /path/to/chipyard-ibex-core --out build/rtl-cpu-ibex --jobs 2
usp rtlcpu build --manifest models/cpus/custom-ibex-wb.yaml \
  --chipyard /path/to/chipyard --wrapper /path/to/chipyard-ibex \
  --core /path/to/chipyard-ibex-core --out build/rtl-cpu-custom --jobs 2
usp rtlcpu build-engine --source /path/to/gem5 \
  --out build/rtl-master-gem5 --jobs 2

usp platform plan examples/rv64-ips --config platforms/chipyard-ibex-qemu.yaml
usp platform run examples/rv64-ips --config platforms/chipyard-ibex-qemu.yaml \
  --generated build/rv64-ips --models build/source-ip-models \
  --rtl-cpu-build build/rtl-cpu-ibex --out results/rtl-qemu-001
usp platform run examples/rv64-ips --config platforms/custom-ibex-wb-gem5.yaml \
  --generated build/rv64-ips --models build/source-ip-models \
  --rtl-cpu-build build/rtl-cpu-custom --bridge build/rtl-master-gem5 \
  --out results/rtl-gem5-001
usp platform verify results/rtl-gem5-001
usp rtlcpu verify results/rtl-gem5-001/baremetal
```

Choose fresh output directories. The out-of-tree gem5 extension preserves the
existing SE executable under a build lock, restoring it even on failure.
Generated SimObjects bind their C++ headers to their actual extension directory.
The extension removes only its own globally added include paths, preserving base
compilation signatures. SCons still verifies source/header dependencies; extension
objects and generated parameters compile normally without rebuilding unrelated IP.
Native SCons builds receive SIGTERM on timeout/cancellation and have ten seconds
to write their incremental database before remaining processes are killed.
Interrupted builds remain failures; command records retain the actual return code
and timeout/interruption status. Resume through the builder, using a fresh output
directory for failed extensions. Never edit saved build identities.
scripts/rtl_cpu_bringup.sh acquires the two pinned public CPU repositories, builds
the masters and runs the native acceptance matrix. It expects the existing
generated rv64-ips and AI_SOC/AIZ_SOC bring-up machines and source-IP models.

The four YAML profiles select provider/model/engine, clock and source manifest.
The immutable execution plan derives typed RV32 M-mode target IR from the verified
base I/O IR plus the explicit CPU binding. Both hashes remain in evidence. It
renames the CPU role and removes the unsupported S-mode IRQ route; the native
PLIC's spare S context is unused. The original RV64 CPU/ISA contract is not
claimed to execute. Repeat with systems/AI_SOC/bringup or AIZ_SOC/bringup and their
matching generated build. These are nine-role emulator subsets, distinct from
the larger architecture inventories and full-chip physical implementation.

Default original firmware tests BSS/stack/RAM, byte/halfword writes, actual load
fault handling, native timer ISR, four GCD result/IRQ vectors, GPIO loopback/IRQ/
W1C, UART and native finisher. I/O addresses/IRQs come from IR. External baremetal
artifacts may supply ELF, sha256 and expected_markers; their images must obey the
same CPU/boot contract and reach the real finisher. Selected external markers
alone do not imply the reference custom-IP drivers were exercised.

## Execution and timing contracts

Top usp_rtl_cpu has split instruction/data requests, grant, one-cycle responses,
byte enables, initial active-low reset, boot address, MSIP/MTIP/MEIP pins, RVFI
valid/trap/PC/instruction and fatal alert. See ibex.sv.in for exact ports. The
supported contract is little-endian RV32IMC plus Zicsr/Zifencei, M-mode, no FPU or
MMU. ELF entry is RAM base for image loading; Ibex first fetch is RAM+0x80. The
firmware includes an explicit entry/reset trampoline and eight reset cycles.
RV64/float ABI images fail before launch. Runtime reset/checkpoint, other boot
layouts, RTOS/Zephyr ports and Linux-capable RTL CPU models require separate work.

QEMU uses its native qtest accelerator, which creates dummy CPU threads instead
of executing TCG instructions. Actual physical read/write/clock_step requests and
native IRQ interception use an owned Unix socket. Its rv32 object is an IRQ sink.

gem5 UspRtlMaster issues actual atomic bus packets and samples the native RISC-V
interrupt controller. A halted RV32 AtomicSimpleCPU under StubWorkload supplies
only the native IRQ context. Actual fetch, execute, commit and numCycles must
all exist and equal zero. Missing counters fail acceptance. CPU CPI/IPC come from
RTL, not the dormant ISS statistics; native gem5 statistics remain appropriate
for the existing gem5 ISS profiles.

The real SystemC TLM initiator/target forward OBI transfers. Unmapped IR windows
produce CPU bus-error responses. Bus responses arrive one RTL cycle after grant,
independent of memory-engine latency. Engine time advances in bounded quanta
(default 16 CPU cycles; allowed 1–64); IRQs are sampled at transactions/quantum
boundaries. Peripheral RTL time remains transaction-driven, without global cycle
correlation or calibrated memory/cache timing. Finisher acknowledgement exits the
engine; eight local CPU edges drain the acknowledged store without new engine
transactions. Native clock/RVFI counters include reset, stalls/idle and drain;
reported RVFI traps are excluded from the CPI instruction denominator.

Every run stores the original ELF, target IR, raw bus/RVFI/IP traces, UART,
commands, return codes, build identities, timing limits and JSON/Markdown evidence.
Verification recomputes counter and acceptance checks. Native tests cover two
CPUs × two engines × three I/O maps, same-ELF identity, cycle limits and artifact
tampering. Promotion requires USP_REQUIRE_RTL_CPU, USP_RTL_CPU_IBEX_BUILD,
USP_RTL_CPU_CUSTOM_BUILD and USP_RTL_MASTER_BUILD. No DMA/coherence, multihart,
cross-ISS or full-architecture/signoff completion is claimed. Hosted workflow
configuration is separate from a successful hosted execution.

The source builders explicitly select upstream Kconfig USE_SYSTEMC=n for gem5:
our SystemC kernel runs in the separate, source-built model/CPU processes. This
bounds the engine build to its native CPU/memory/event functionality. It does not
change the IPC/TLM bridge or replace its RTL. New build records retain the native
Kconfig snapshot and the executable's actual --build-info output; both are
verified, including the compiled setting. Preexisting pinned engine binaries
remain supported under their recorded identities. Embedded gem5 SystemC modules
are outside this profile.

RTL reset enters RAM directly. The unused ROM payload is engine-specific: gem5
uses zero-filled read-only SimpleMemory, and QEMU retains its native reset stub.
The execution plan labels these actual implementations. Identical ROM bytes are
not part of this profile's software contract.
