# AIZ_SOC: executable source-IP example

This directory contains a complete copy of the canonical nine-component
[bring-up project](../../systems/AIZ_SOC/bringup). The parity test rejects changes
that diverge from that project. The full architecture is separately defined in
[systems/AIZ_SOC](../../systems/AIZ_SOC); this example does not execute its full
37-instance inventory.

The same IP-XACT design binds one native RV64 CPU, RAM/ROM, UART, CLINT, PLIC,
finisher, real Chipyard GCD and OpenTitan GPIO RTL. Both source IPs execute in a
shared SystemC process and deliver actual interrupts to the selected QEMU/gem5
engine. GCD is a conformance accelerator, not an AI engine.

```sh
mkdir -p build
usp validate examples/AIZ_SOC
usp elaborate examples/AIZ_SOC --out build/AIZ_SOC-ir.json
usp platform plan examples/AIZ_SOC --config platforms/gem5-timing.yaml
usp unified accept examples/AIZ_SOC --generated build/AIZ_SOC \
  --models build/source-ip-models --bridge build/unified-gem5 \
  --out results/AIZ_SOC-unified-new
usp unified verify results/AIZ_SOC-unified-new
```

The generated machine/Zephyr ELF and pinned native model/bridge builds must exist
and match this IR. Build them with scripts/dual_soc_bringup.sh and the source-IP/
unified guides; these commands do not silently acquire or substitute models.
The native acceptance test runs this example on both engines using the same ELF,
checks MMIO/results/IRQs and verifies retained evidence. Use a new output path.

For native gem5 two-hart coherent DMA, use platforms/AIZ_SOC-coherent.yaml
with `usp coherent run`; see [coherent DMA](../../docs/coherent-dma.md). For
actual RTL CPU plus the four kernel profiles, see
[RTL software](../../docs/rtl-software-platform.md). These are separate execution
contracts. Full Rocket/BOOM, arbitrary AXI/TileLink/MMU, complete AI accelerators
and full-inventory RTL/ASIC are not supplied by this nine-component example.
