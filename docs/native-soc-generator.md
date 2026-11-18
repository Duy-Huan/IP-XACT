# Native SoC generator: spec to executable hardware and software

`--input-format fabric` accepts the static address-decoder source in specs/fabric/.
It produces canonical IR v4 and lowers its checked physical projection through
the same native engine/software path. See native-address-decoder.md for supported
translation semantics, real qualification requirements and explicit limits.

The platform's first priority is an executable SoC generator. The native frontend
accepts a strict YAML specification with CPU choice, boot ownership, memory and
device instances, addresses, sizes and IRQs. It generates real IEEE 1685-2022
components/design, validates them with the bundled XSD, re-imports a typed SoC IR,
binds qualified models, and lowers a custom QEMU machine and native gem5 system
graph plus a shared bare-metal BSP/conformance firmware and a native OpenSBI/Linux build path. It does not select or
rename a fixed upstream board. Each source-IP instance has one declared window
and IRQ in either engine; the new gem5 adapter is compiled from pinned source.

Instance identity is independent of model type. For example, accelerator_a and
accelerator_b bind separate instances of the same Chipyard GCD implementation at
different addresses/IRQs. Adding another instance does not add a compiler profile.
Names, counts, memory sizes and maps come from the specification. The existing
7/8/9-role compiler remains available for its established OS/RTL acceptance cases.

## Input and outputs

Version 0.3.0a1 adds a common native IR and direct structural IP-XACT intake.
See [canonical-native-ir.md](canonical-native-ir.md) for the overlay, original
examples, executable graph validation, source identity and release scope.

See [AI_SOC-native.yaml](../specs/AI_SOC-native.yaml) and
[AIZ_SOC-native.yaml](../specs/AIZ_SOC-native.yaml). These are original generator
acceptance specifications, not implementations of the 46/37-instance architecture
targets. AI_SOC-native has 13 instances: one CPU, ROM, two RAMs, CLINT, PLIC,
finisher, two UARTs, two GCDs and two GPIOs. AIZ_SOC-native has 11 instances, three
GCDs, one GPIO, one UART, a different CPU choice, reset vector, entry, RAM size,
timebase, device addresses and IRQ assignments. GCD is not an AI accelerator.

The top-level keys are schema_version ('1'), name, cpu, boot and devices. CPU
provides name, model (qemu.rv64 or qemu.max), timebase_hz and explicit gem5_model
(gem5.RiscvTimingSimpleCPU or gem5.RiscvAtomicSimpleCPU) when gem5 is selected.
These select separate engine CPU implementations of the required ISA; neither
claims to execute an unimplemented Rocket/custom RTL CPU. Boot explicitly names
ram, rom, console and entry. Each device has name, model, base and size; UART/GCD/
GPIO also require irq. Use integer or hex literals. `usp soc models` lists models.
Unknown fields, models, overlapping/overflowing windows, ambiguous names, bad
references, apertures and IRQs fail before output creation. No silent fallback.

Generated outputs include:

- Parameterized IP-XACT 2022 XML, native-ir.json, checked soc-ir.json and models.lock.json.
- machine.c: a compiled `usp-native` machine instantiating the declared devices.
- gem5-board.py and engine-bindings.json: native instance graph and per-engine bindings.
- soc.dts/soc.dtb: memory/I/O and interrupt descriptions from the same design.
- firmware/soc.h, linker.ld, start.S and main.c, then a compiled firmware.elf.
- simulate.py, build/run command logs, identity manifests and verification reports.

The native substrate validates 1..8 homogeneous RV64 ISS harts, one ROM/CLINT/PLIC/finisher,
one or more RAMs/UARTs, and any supported GCD/GPIO count within the 31 unique PLIC
sources and 64-total-instance bound. Names are arbitrary ASCII identifiers except
the internal __usp_ prefix. Device base addresses are 4 KiB aligned, below 2^48;
RAM is 4 KiB..512 MiB per region, at most 1 GiB total. ROM is 4..64 KiB. Exact
peripheral apertures remain model contracts: UART 256 bytes, CLINT 64 KiB, PLIC
64 MiB, GCD/GPIO/finisher 4 KiB. This is a qualified library, not behavioral
inference from names/registers. The current frontend is YAML-to-IP-XACT; direct
general IP-XACT plus arbitrary interface/translation overlays is still future work.

## Build and run

Use the frozen Python/build environment described in build-reproducibility.md,
the pinned public QEMU checkout and the verified source-IP model build. Native
generation/build/run do not implicitly acquire dependencies. GCC, dtc, Meson,
Ninja and the documented QEMU host dependencies must be installed.

```sh
usp soc validate specs/AI_SOC-native.yaml
usp soc generate specs/AI_SOC-native.yaml --engine both --out build/native-AI_SOC
usp soc gem5-build --source-gem5 .tools/upstream/gem5 --out build/native-gem5 --jobs 2
usp soc build --generated build/native-AI_SOC --source-qemu .tools/upstream/qemu \
  --cross-compile riscv64-unknown-elf- --jobs 2
usp soc accept --generated build/native-AI_SOC --models build/source-ip-models \
  --gem5-build build/native-gem5 \
  --out results/native-AI_SOC-001
usp soc verify results/native-AI_SOC-001
```

Repeat with AIZ_SOC-native.yaml and fresh directories; reuse the verified gem5
engine build. `accept` launches independent QEMU/gem5 runs concurrently using the
same generated IR and ELF, then compares per-instance writes and GCD result data.
Use `run --engine qemu` or `run --engine gem5 --gem5-build ...` to select one engine;
the generated simulate.py is a convenience launcher. The generated machine owns
the actual declared address map; it does not launch upstream virt. Every selected
GCD/GPIO instance gets its own SystemC/Verilator process/state and socket and its
own PLIC source. The verified existing RTL wrapper contains both source IPs; only
the selected peripheral's window/IRQ is exposed by that instance's binding. Its
unused sibling is explicitly recorded, not counted as a SoC instance. Multiple
model processes share one running native CPU/memory/interrupt system.

The generated firmware checks memory, actual timer interrupts, each UART's PLIC
interrupt, concurrently submitted distinct GCD calculations (6, 8, 10, ...), and
GPIO loopback/W1C/interrupts. Host acceptance also reads each real transaction
trace, verifies per-instance GCD results/IRQs, model/guest exits and artifact
hashes. Raw UART output and per-instance model/transaction logs remain in the run
directory. Build logs preserve the exact generated C and firmware compilation.
gem5 additionally requires a native guest-finisher exit and retired instructions,
cycles, CPI and IPC from stats.txt. Default CPU clock is 1 GHz and SimpleMemory latency 10 ns; cpu.clock_hz and
cpu.memory_latency_ns configure gem5. Proxy PIO latency is 100 ns: model settings, not calibrated target
hardware. The flat SystemXBar graph does not claim the full Garnet/NoC target.

`usp soc run ... --firmware path/to/application.elf` runs a supplied ELF against
the generated hardware and strict entry/RAM contract. It records execution status
without claiming the supplied application tested every peripheral. Guest failure,
timeout and wrong-entry binaries have separate actual negative tests. `build
--reuse-qemu previous-generated-directory` explicitly reuses a prior binary only
when its original manifest/build identities, source pin, generated C and binary
hash all match; firmware and DTS are rebuilt. Evidence labels that reuse.

## Acceptance and remaining work

tests/test_native_soc.py validates a renamed unseen configuration with an extra
GCD instance, the real XSD/IR round trip, negative contracts and evidence mutation.
Set USP_REQUIRE_NATIVE_SOC=1, USP_NATIVE_AI_SOC_BUILD, USP_NATIVE_AIZ_SOC_BUILD
and USP_NATIVE_GEM5_BUILD, USP_IP_MODELS_DIR to require both actual dual-engine
simulations. Local promotion
requires these builds and executes the new cases together with the prior suite.
scripts/native_soc_bringup.sh provides the source-build/acceptance recipe.

The current native path supports generated bare-metal and an explicitly built
OpenSBI/Linux/DTB/initramfs bundle. Use the SMP specifications, whose boot RAM is
64 MiB and whose entries are at 2 MiB aligned RAM bases:

    usp soc generate specs/AI_SOC-smp.yaml --engine both --out build/native-smp-001
    usp soc build --generated build/native-smp-001 --source-qemu .tools/upstream/qemu
    usp soc linux-build --generated build/native-smp-001 --source-linux .tools/upstream/linux --source-opensbi .tools/upstream/opensbi
    usp soc accept --generated build/native-smp-001 --models build/source-ip-models --gem5-build build/native-gem5 --software linux --out results/native-linux-001 --timeout 180

cpu.harts (default 1), cpu.clock_hz (default 1000000000) and
cpu.memory_latency_ns (default 10) are optional strict integer fields. Hart IDs
are contiguous from zero; names are the CPU instance name followed by _hartN for
secondary harts. CPU implementations are homogeneous and explicitly selected for
each engine. Firmware allocates separate 4 KiB stacks, initializes BSS once,
checks atomic shared memory and one-shot timer/IPI delivery on every hart, and
requires its generated RAM to fit the image and stacks. IRQ source capacity is
shared across harts; each hart gets its own M/S PLIC contexts and CLINT lines.
The IR has one CPU instance/address space and real routes per hart. Hardware
clock/memory/fabric settings also appear in hardware-graph.json.

AI_SOC-smp.yaml has four harts/16 instances and AIZ_SOC-smp.yaml two harts/12
instances. These retain real Chipyard/OpenTitan peripherals, distinct maps,
clocks and CPU choices. The maximum-hart fixture is a separate boundary test.
Native gem5 exposes per-hart counters; aggregate cycles are summed core cycles
and aggregate CPI/IPC is not system throughput. Boot/idle remains included.

Linux boot has its own native-linux-boot suite and checks OpenSBI, kernel, PID 1,
workload success and clean power-off. It does not qualify source-IP Linux drivers.
The generated DTB marks the selected boot RAM as OS memory; additional RAM banks
remain mmio-sram rather than silently becoming Linux-managed banks.
Kernel reuse via linux-build --reuse-linux verifies source pins, identical spec,
compiled kernel inputs and every original output. It records the original build
hash and regenerates the DTB; stale compiler identity is never edited manually.

External model descriptors use the optional external_models mapping and require
explicit executable/hash/license/protocol/VLNV data and user firmware. See
[native-external-models.md](native-external-models.md) for the wire contract and
actual counter/IRQ example. The compiler does not infer behavior from metadata.

Promotion additionally requires USP_REQUIRE_NATIVE_SMP and
USP_REQUIRE_NATIVE_EXTERNAL, the two SMP/Linux roots, maximum-hart root and
external example. Generic Zephyr/RTOS BSP generation, arbitrary RTL CPU/bus/MMU
adapters, coherent DMA/global timing and full inventories remain separate work.
See [native-design-capabilities.md](native-design-capabilities.md) for the assessed
contract and remaining industrial boundaries. CHIA orchestration remains deferred
behind this native generator work.

## One native HW/SW lifecycle

See [native-flow.md](native-flow.md) for usp flow plan/build/all/run/verify,
source packages, both mapped designs, the gem5-only path and native RV64
FreeRTOS/Zephyr/µC/OS BSPs. These adapters derive placement/boot/IRQ/timebase from
the checked canonical IR; they are distinct from older fixed-profile BSPs.
Software-only tests retain their scope; package peripheral conformance is separate.
