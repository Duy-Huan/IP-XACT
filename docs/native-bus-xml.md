# Standard bus XML to native SoC

The qualified IEEE 1685-2022 profile imports busDefinition, abstractionDefinition,
busInterfaces, initiator addressSpaceRef, target memoryMapRef, addressSpaces,
segments, memoryMap subspaceMap and design interconnections. Real 2022 XSD and
strict semantic validation run before generation. Unsupported standard elements
or protocol families fail explicitly; metadata does not synthesize behaviour.

`specs/bus/AI_SOC/native.yaml` and AIZ_SOC select explicit model/CPU/boot/IRQ
bindings and address-space names only. Placements and bridge intervals come from
the XML rather than a duplicate YAML memory map. Canonical native IR v5 preserves
original local windows, parameters, interfaces and connection facts. It derives
and checks the v4 static routing projection on serialization, reload and build.
Flat v3 and static routing v4 remain compatible.

```sh
usp soc validate specs/bus/AI_SOC/native.yaml --input-format ipxact
usp flow plan specs/bus/AI_SOC-flow.yaml --out build/xml-plan
usp flow build specs/bus/AI_SOC-flow.yaml --out build/xml-flow --help
usp soc export-bus specs/fabric/AI_SOC.yaml --out build/exported-bus
```

`export-bus` exports supported fabric inputs to standard XML, validates every
file and imports it back to check hardware equivalence. Generated export.json
records source/exporter/file identities. It never overwrites an existing output.
See specs/bus/README.md and docs/native-flow.md for source/build options.

The static functional adapter uses bus VLNV socsim.native:bus:mmio:1.0, optional
transactional abstraction socsim.native:abstraction:mmio:1.0 and decoder VLNV
socsim.native:fabric:static_decoder:1.0. One RV64 hart, one initiator per connected
bus, 64-bit data width, byte addressing, little endian and decoder trees are
qualified. Segments select decoder apertures; nonzero leaf BASE is resolved into
physical placement. Cycles, aliases, clipping, duplicate endpoints, unmapped
references, inconsistent decoder parameters and unsupported adaptations fail.
AXI/TileLink transactions, burst timing, arbitration, multiple CPU views, DMA,
coherence and asynchronous reset/clock crossings need distinct adapters.

Acceptance compares XML and fabric canonical hardware plus generated headers,
DTS, linker and firmware. Native acceptance requires identical bare-metal ELF
and real translated Chipyard/OpenTitan MMIO/IRQ, FreeRTOS, Zephyr and both µC/OS
kernels on QEMU and gem5 for both maps. USP_REQUIRE_BUS_XML makes these tests
mandatory in promotion. A frontend-only pass does not qualify execution.
