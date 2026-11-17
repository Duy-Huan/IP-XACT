# Native SoC generator: spec to executable hardware and software

The platform's first priority is an executable SoC generator. The native frontend
accepts a strict YAML specification with CPU choice, boot ownership, memory and
device instances, addresses, sizes and IRQs. It generates real IEEE 1685-2022
components/design, validates them with the bundled XSD, re-imports a typed SoC IR,
binds qualified models, and lowers a custom QEMU machine and native gem5 system
graph plus a shared bare-metal BSP/conformance firmware. It does not select or
rename a fixed upstream board. Each source-IP instance has one declared window
and IRQ in either engine; the new gem5 adapter is compiled from pinned source.

Instance identity is independent of model type. For example, accelerator_a and
accelerator_b bind separate instances of the same Chipyard GCD implementation at
different addresses/IRQs. Adding another instance does not add a compiler profile.
Names, counts, memory sizes and maps come from the specification. The existing
7/8/9-role compiler remains available for its established OS/RTL acceptance cases.

## Input and outputs

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

- Parameterized IP-XACT 2022 XML, imported soc-ir.json and models.lock.json.
- machine.c: a compiled `usp-native` machine instantiating the declared devices.
- gem5-board.py and engine-bindings.json: native instance graph and per-engine bindings.
- soc.dts/soc.dtb: memory/I/O and interrupt descriptions from the same design.
- firmware/soc.h, linker.ld, start.S and main.c, then a compiled firmware.elf.
- simulate.py, build/run command logs, identity manifests and verification reports.

The native v1 substrate supports one RV64 ISS hart, one ROM/CLINT/PLIC/finisher,
one or more RAMs/UARTs, and any supported GCD/GPIO count within the 31 unique PLIC
sources and 64-total-instance bound. Names are arbitrary ASCII identifiers except
the internal __usp_ prefix. Device base addresses are 4 KiB aligned, below 4 GiB;
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
cycles, CPI and IPC from stats.txt. The current CPU clock is 1 GHz, SimpleMemory
latency 10 ns and proxy PIO latency 100 ns: model settings, not calibrated target
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

This new path targets QEMU/gem5 bare-metal, with independently verified native
builds/runs required for acceptance. It emits a bare-metal BSP and hardware DTS,
not a verified Zephyr/Linux/Yocto BSP. Existing gem5/Zephyr/RTL-profile success is
not evidence for this new generic path. Next gates are OS BSP generation/boot,
extensible behavioral-model adapters and richer bus/clock/reset/
translation IR. One flat functional bus is not an AXI/TileLink timing model,
coherent DMA or complete SoC synthesis. The full AI_SOC/AIZ_SOC inventories remain
open, and CHIA orchestration is deferred behind the native generator priority.
