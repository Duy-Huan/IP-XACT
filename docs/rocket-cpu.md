# Chipyard-pinned Rocket CPU integration work

This is ongoing M5 implementation on dev. It does not close M5 or M8-M9.
The initial CPU wrapper instantiates the actual RocketTile from the Rocket Chip
commit selected by Chipyard 1.13.0, with its exact Diplomacy/CDE/HardFloat gitlinks.
It retains actual L1 caches, PTW and TLBroadcast, followed by upstream TLToAXI4,
AXI ID/buffer/user adapters and an explicit 64-bit-data, 32-bit-address AXI port.
It is a single tile profile, not the complete Chipyard board or a replacement ISS.

The qualified build direction uses SBT 1.8.2 with the published-checksum JDK
17.0.17+10, Chisel 6.5.0/Scala 2.13.12, a checked Maven inventory, firtool 1.62.0
and Verilator 5.032. JDK21/SBT1.8.2 failure evidence is preserved. Upstream trees
stay clean; no algorithm sources are rewritten. Berkeley/SiFive/Apache notices
are copied with build outputs.

`rocket_elaboration.py` preserves the full descriptive ParamsAnnotation and
SRAMAnnotation data in JSON and removes only those two descriptive classes from
the compiler input. All other annotations remain for strict firtool validation.
Split Verilog output retains blackbox implementation and active assertions.

`rocket_build.py` provides the source build/verify API. RAM and ROM/MMIO apertures
must be explicit and must fit the supported physical address width; they determine
actual PMA visibility. Other harts, bus widths/ISA profiles, external DMA coherence
and full SoC timing require their own adapters and tests. Errors do not fall back
to ISS execution. Source model behavior is never inferred from register metadata.

The AXI target adapter performs explicit byte-strobe handling and decomposes
64-bit beats into the existing aligned MMIO32 memory transport. QEMU qtest or
gem5 external atomic master owns backing RAM/peripherals; actual RTL owns CPU
instructions and L1 state. Native gem5 ISS counters must remain zero. Retirement
and cycles come from Rocket's actual commit trace and real simulated clock edges.

Exploratory runs have exercised real timer, Chipyard GCD and OpenTitan GPIO IRQs
on both engines. The initial final-response drain assertion was caught and fixed.
Deeper tests distinguish PMA access exceptions from downstream AXI errors; the
latter are not assumed to be precise CPU load exceptions. The current candidate
is not release qualified: production build, firmware/negative/IRQ/reset/burst/
backpressure acceptance, plan binding and aggregate mandatory CI remain required.

Development checkpoint, 2026-11-19: the actual mapped source builder has now
compiled all three low-PA maps (rv64-ips, AI_SOC/AIZ_SOC bring-up). Twelve actual
runs passed: bare-metal and original PMP/Sv39 M/S/U conformance on both engines
for each map, with deliberate request backpressure and real AXI 8-beat bursts.
The firmware checks S-mode virtual alias writes, an unmapped S-mode page fault,
U-mode page permissions/privileged-instruction rejection and a U-mode AMO. Raw
commit PCs include virtual user addresses and exactly five conformance traps.

Source-built FreeRTOS 11.2.0 RV64IMAC LP64 then passed its real preemption,
saved-register and UART/PLIC acceptance on both engines for rv64-ips. Each run
retired over 1.45 million actual RTL instructions, over 1.65 million clock edges
and 32 traps. This is source-built scheduler execution, not an ISS replacement.
The other mapped kernel runs and deliberate failure fixtures are still pending.

`usp rocket build/run/verify` exposes the actual path. Explicit CPU selection
`provider: chipyard`, `model: rocket64-imac-sv39-axi32` also lowers through the
common immutable ExecutionPlan; its platform profile requires a verified CPU
build and eligible source-built software. Platform lifecycle acceptance remains
pending. Other Rocket configurations, BOOM, full Earlgrey, Linux RTL boot and
native arbitrary-instance compiler binding remain separate work. No broad
milestone or release is closed by this checkpoint.

Preserved local engineering evidence: `results/continuous-upgrade-20261119/`
contains exact build logs and prior failures, 12 per-run bundles under
`build/rocket-acceptance-r2/`, and the two long FreeRTOS runs under
`/dev/shm/rocket-freertos-r2/` (durable, hash-verified archives are retained under
results before cache cleanup). The focused core probe passed 66 tests, with 15
native cases skipped because this invocation did not supply their opt-in roots;
that probe is not the mandatory aggregate native gate.

## Reproduce the development path

Prerequisites are the existing verified source-IP engines/models and clean public
source pins. `scripts/rocket_bringup.sh` acquires the exact Rocket gitlinks and
JDK/SBT, creates fresh CPU and software builds, then executes the focused native
suite. Existing output is deliberately preserved; choose fresh root paths for a
new candidate. It is an engineering gate, distinct from aggregate release CI.

```bash
bash scripts/setup_rocket_tools.sh
usp rocket build examples/rv64-ips --out build/my-rocket \
  --upstream .tools/upstream --tools .tools/chipyard \
  --java-home .tools/rocket/jdk-17.0.17+10 --scala-cache .cache/scala
usp platform run examples/rv64-ips \
  --config platforms/chipyard-rocket-axi32-gem5.yaml \
  --generated build/rv64-ips --models build/source-ip-models \
  --bridge build/rtl-master-gem5 --rtl-cpu-build build/my-rocket \
  --out results/my-rocket-run
usp platform verify results/my-rocket-run
usp rocket build-software examples/rv64-ips --kind zephyr \
  --cpu-build build/my-rocket --generated build/rv64-ips \
  --upstream .tools/upstream --out build/my-rocket-zephyr
```

Select QEMU with the matching `chipyard-rocket-axi32-qemu.yaml`. Add `freertos`
or `zephyr` to workloads and supply matching source-built `software_builds`
directories. Every kernel is bound to the exact CPU build and I/O IR; unrelated
ELFs are not promoted into kernel qualification. The older generic `Rocket`
configuration remains an unresolved negative fixture, including Linux/boot
workloads whose adapter/boot ABI is not qualified yet.

The original Rocket Zephyr BSP declares RV64IMAC and 64-bit context explicitly,
without an ISS board or FPU. Both smoke and deliberately disabled-timer kernels
are source-built for all maps; acceptance on both engines is required. The failed
ELF32 prototype is retained. Large retirement logs are validated as streams; the
verifier recomputes actual CPI/IPC and rejects modified IR, binary dependencies,
partial AXI responses or mixed software origins. None of these checks calibrates
the atomic memory timing model to silicon.
