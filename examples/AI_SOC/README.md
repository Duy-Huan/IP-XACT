# AI_SOC: executable source-IP example

This directory contains a complete copy of the canonical nine-component
[bring-up project](../../systems/AI_SOC/bringup). The parity test rejects changes
that diverge from that project. The full architecture is separately defined in
[systems/AI_SOC](../../systems/AI_SOC); this example does not execute its full
46-instance inventory.

The same IP-XACT design binds one native RV64 CPU, RAM/ROM, UART, CLINT, PLIC,
finisher, real Chipyard GCD and OpenTitan GPIO RTL. Both source IPs execute in a
shared SystemC process and deliver actual interrupts to the selected QEMU/gem5
engine. GCD is a conformance accelerator, not an AI engine.

```sh
mkdir -p build
usp validate examples/AI_SOC
usp elaborate examples/AI_SOC --out build/AI_SOC-ir.json
usp platform plan examples/AI_SOC --config platforms/gem5-timing.yaml
usp unified accept examples/AI_SOC --generated build/AI_SOC \
  --models build/source-ip-models --bridge build/unified-gem5 \
  --out results/AI_SOC-unified-new
usp unified verify results/AI_SOC-unified-new
```

The generated machine/Zephyr ELF and pinned native model/bridge builds must exist
and match this IR. Build them with scripts/dual_soc_bringup.sh and the source-IP/
unified guides; these commands do not silently acquire or substitute models.
The native acceptance test runs this example on both engines using the same ELF,
checks MMIO/results/IRQs and verifies retained evidence. Use a new output path.

For native gem5 two-hart coherent DMA, use platforms/AI_SOC-coherent.yaml
with `usp coherent run`; see [coherent DMA](../../docs/coherent-dma.md). For
actual RTL CPU plus the four kernel profiles, see
[RTL software](../../docs/rtl-software-platform.md). These are separate execution
contracts. Full Rocket/BOOM, arbitrary AXI/TileLink/MMU, complete AI accelerators
and full-inventory RTL/ASIC are not supplied by this nine-component example.
