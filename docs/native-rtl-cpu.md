# Native IR with an actual source CPU

This is an unreleased development increment. It does not complete M1–M9 or
qualify arbitrary CPU buses, full reference inventories or physical chips.

`specs/rtl/AI_SOC-rocket.yaml` and `AIZ_SOC-rocket.yaml` select
`chipyard.rocket64-imac-sv39-axi32`. Native IR v6 records its actual source pins,
ISA/privilege/MMU, RAM reset entry and instruction owner. It records the QEMU
qtest and gem5 halted contexts as memory/IRQ services. Neither context replaces
Rocket's RTL instructions, L1 caches or PTW. Native v3/v4/v5 ISS inputs retain
their existing representation.

This profile supports one actual RV64IMAC RocketTile, PA32, an aligned
power-of-two primary RAM, explicit ROM/MMIO apertures and integer CPU periods.
Instance names/counts, UARTs, source GCD/GPIOs, addresses and IRQ assignments are
design data. Other CPU profiles, multiple RAM PMA banks and external coherent
DMA need adapters; unsupported input fails before generation.

The source-owned fixture counts are 12 and 11 instances, separate from the
46/37-instance complete inventories. The first has two UARTs, two source GCDs
and two source GPIOs. The second has a different RAM/reset/IO/IRQ map, three
GCDs and one GPIO; its development execution used standard bus IP-XACT XML.
No SoC-name branch implements either graph.

## Execute a development fixture

Use the pinned tools and public source checkouts described in rocket-cpu.md.
The existing source-IP model builder provides real Chipyard/OpenTitan RTL.

```bash
usp soc generate specs/rtl/AI_SOC-rocket.yaml --engine both --out build/native-ai
usp soc build --generated build/native-ai --source-qemu .tools/upstream/qemu
usp soc cpu-build --generated build/native-ai --out build/native-ai-cpu \
  --upstream .tools/upstream --tools .tools/chipyard \
  --java-home .tools/rocket/jdk-17.0.17+10 --scala-cache .cache/scala
usp soc gem5-rtl-build --source-gem5 .tools/upstream/gem5 --out build/native-rtl-engine
usp soc accept --generated build/native-ai --models build/source-ip-models \
  --gem5-build build/native-rtl-engine --rtl-cpu-build build/native-ai-cpu \
  --timeout 180 --out results/native-ai
usp soc verify results/native-ai
```

`usp flow` can build the source CPU from its explicit `--cpu-*` tool paths or
reuse an exactly matching `--rtl-cpu-build`. It retains source ownership and
eligible RV64 M-mode software builds; native RTL Linux still needs its own
generated boot BSP. The separate legacy Rocket Linux profile is documented in
rocket-cpu.md and must not be inferred for this native flow.

## Evidence and boundaries

The focused native probe in `results/continuous-upgrade-20261119/` passed both
fixtures with concurrent independent QEMU/gem5 execution, the same IR/ELF,
actual CPU retirement/AXI traces, all declared source IP commands/results/IRQs,
native finisher exit and independently repeated verification. The gem5 native
CPU instruction counters stayed zero. Raw clock edges and retirement provide
RTL CPI/IPC; backing memory is atomic and timing is not calibrated to silicon.

The failed SimObject parent cycle and incorrect UART artifact path were retained
before the corrected probe. The source-cache executable is held by an atomic
rename during a real gem5 link, then restored as the original entry. Completed
output artifacts have separate directory entries; restoring a source cache
does not overwrite their content. Interrupted/partial transfers are unqualified.

This focused evidence is not the complete software/reset/error matrix, aggregate
local CI, a main promotion, a version tag, observed hosted CI or M8–M9 closure.
