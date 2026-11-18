# Source packages and one native HW/SW lifecycle

The v0.3.0-alpha.2 batch extends the existing canonical native IR path with
source model packages, a persisted lifecycle plan and generated software BSPs.
The approved complete programme and observed gaps remain in
[upgrade/](../upgrade/README.md). This is a scoped increment, not final v0.3.0.

## Run the supplied designs

After the pinned public sources/toolchains and native gem5 model engine have
been provisioned, run from the repository root:

```sh
usp flow plan specs/flows/AI_SOC-package.yaml --out build/AI_SOC-plan.json
usp flow all specs/flows/AI_SOC-package.yaml --out build/AI_SOC-flow \
  --source-qemu .tools/upstream/qemu --gem5-build build/native-gem5-v3 \
  --source-freertos .tools/upstream/freertos --source-zephyr .tools/upstream/zephyr \
  --source-linux .tools/upstream/linux --source-opensbi .tools/upstream/opensbi \
  --ucos-upstream .tools/upstream --jobs 2
usp flow verify --built build/AI_SOC-flow --out build/AI_SOC-flow/runs
```

`build` and `run` may be invoked separately. `all` executes them in order and
verifies the result. Existing outputs are preserved; choose a fresh directory.
`--cross-compile` and `--cross-compile-linux` select installed tool prefixes.
No fetch, board substitution or inference of missing behavior is implicit.

AI_SOC-package defines a single RV64 hart, 96 MiB high-address boot RAM and two
independent SystemC counter instances. One common hardware IR drives the package
driver, FreeRTOS, Zephyr, µC/OS-II, µC/OS-III and OpenSBI/Linux. Every workload
executes independently on QEMU and gem5 with the same ELF and canonical IR.
The package driver exercises both counters, reset values, independent values and
actual PLIC IRQ delivery/acknowledgement. RTOS/Zephyr acceptance requires real
timer/scheduler/UART IRQ evidence, not just a boot string.

```sh
usp flow all specs/flows/AIZ_SOC.yaml --out build/AIZ_SOC-flow \
  --source-qemu .tools/upstream/qemu --gem5-build build/native-gem5-v3 \
  --models build/source-ip-models --source-freertos .tools/upstream/freertos \
  --source-zephyr .tools/upstream/zephyr --ucos-upstream .tools/upstream
usp flow all specs/flows/AIZ_SOC-gem5.yaml --out build/AIZ_SOC-gem5 \
  --gem5-build build/native-gem5-v3 --models build/source-ip-models
```

AIZ_SOC uses different names, RAM/reset/I/O/IRQ assignments and a boot entry
offset. It exercises generated bare-metal and the four native RTOS/Zephyr BSPs.
The gem5-only fixture builds firmware/DTB and executes gem5 without compiling or
launching QEMU. Its canonical contract still declares both compatible ISS choices;
this is not execution of a QEMU microarchitecture in gem5.

## Plan, ownership and integrity

`plan.json` records source hashes, selected engines, explicit owners, ordered
workloads and unresolved build steps. A planned package has no invented binary
digest. Actual compilation resolves its executable; native generation then
validates the complete hardware graph and emits native-ir.json. Backends and
BSPs consume its checked projection. An unknown CPU/model/protocol fails.

`flow-build.json` binds the plan, compiler adapters, canonical IR, generated
manifest, model packages, software binaries and selected engine identities.
Verification checks their content and recomputes acceptance from actual native
reports/UART/counters, including workload-to-IR/ELF ownership. Editing a claimed
PASS or deleting a required check cannot change the observed result.

Source YAML bytes are retained when no package materialization is necessary.
Verified QEMU/kernel reuse is explicit (`--reuse-qemu`, `--reuse-linux`) and
requires the original source/C/manifest/binary or compiled kernel identities.
Old manifests are never modified to suppress stale-build errors.

## Package contract

See [the public counter package](../specs/flows/packages/counter/package.yaml).
It pins the exact VLNV, license text/SPDX, C++ source and freestanding driver.
It declares aligned 32-bit little-endian MMIO in 4 KiB, one level IRQ in response
bit zero, isolated peripheral state and annotated SystemC time. The builder
compiles C++17 against installed SystemC, then executes actual socket transactions
for reset, read/write, IRQ assert/ack and address error. Responses and the model's
raw trace must match the declared conformance vectors. Modified sources, drivers,
licenses, register ambiguity, missing error tests or unsupported buses fail.

```sh
usp package build specs/flows/packages/counter/package.yaml --out build/counter-package
usp package verify build/counter-package
```

Register and driver package metadata are preserved in package/build locks; this
does not extend native IP-XACT register-side-effect lowering. A package-driver
workload currently covers all external instances of one package family; multiple
different external families need an explicitly combined driver/adapter. Native
RTOS tests cover the kernel/timer/UART contract; they do not qualify every unused
peripheral driver. Their software-only scope is recorded explicitly.

## Software ABI

The native RTOS/Zephyr BSPs support one RV64 M-mode hart. SMP RTOS, FPU/vector/TLS
context and arbitrary MMU/CPU/bus ports are unsupported. Startup/linker/DTS,
console, PLIC, CLINT and timebase derive from IR, including a nonzero RAM entry
offset and 48-bit addresses. Linux has its existing separate SMP/MMU/OpenSBI path.

µC/OS-II/III use the pinned public Apache-2.0 sources already qualified for RV32.
The native path explicitly copies/adapts their port to LP64: 64-bit pointers,
stack/status types, frame slots, MEPC/interrupt causes and stack alignment; CLINT
MSIP writes remain 32 bit. The µC/OS-II memory-alignment pointer cast is adapted
in a local kernel copy. Upstream trees remain clean; licenses, source inventories,
original file hashes and local adaptations are retained. Actual tests require
preemption, resumed high-bit register state and one UART IRQ on both engines.
Assertion failure and disabled ticks must fail after actual firmware boot.

## Performance and remaining exits

With `performance.qemu_cost_model`, the flow compiles plugin-enabled QEMU and a
source-pinned QEMU 10 event plugin. It records actual instruction execution
attempts and data-access callbacks for each declared hart. Analytical cycles/CPI/
IPC use the configured costs and are explicitly uncalibrated; native QEMU CPU
cycles remain null. Gem5 retains its native statistics. The interval is the whole
guest including boot/idle; this does not implement ROI control, global timing or
silicon/reference calibration.

`specs/flows/AI_SOC-smp-profile.yaml` separately qualifies four active RV64 harts
with generated bare-metal firmware, shared-memory/timer/IPI checks, actual QEMU
per-hart callbacks and native gem5 per-hart counters. Run it with the same
`--source-qemu`, `--gem5-build` and `--models` arguments as AIZ_SOC. This fixture
does not extend the single-hart native RTOS ABI.

Generic bus translations, heterogeneous/RTL CPU lowering, AXI/TileLink and
Rocket/BOOM/full Earlgrey, general DMA/NoC/time, arbitrary OS/IP drivers and a real
Yocto image remain open. These fixtures do not implement the 46/37-instance full
architectures or emit matching full RTL/ASIC designs. Existing RTL/Garnet/physical
profiles retain their separately tested scopes. Hosted/cold/Podman qualification
is separate from the local aggregate regression.
