# Local address spaces and static decoder lowering

`usp soc generate specs/fabric/AI_SOC.yaml --input-format fabric --engine both
--out build/AI_SOC-fabric` generates native IR v4 and the existing engine/software
artifacts. For the common lifecycle, use `usp flow all specs/fabric/AI_SOC-flow.yaml`
with the source/tool/model options documented in native-flow.md. AIZ_SOC has its
own fixture, map and software configuration under specs/fabric/.

The input declares `routing.root`, bounded `spaces` with address widths, `bridges`
and one local `placement` per device. Device sizes/models/IRQs and CPU/boot contracts
retain their native meaning. CPU boot addresses are physical. Each bridge declares
a source aperture, target aperture and `static-address-decoder-v1` capability.
For each hop, physical = source_base + local_address - target_base. The complete
target must fit at every hop, including empty nested bridge windows.

IR v4 retains the immutable routing graph and physical executable projection.
Canonical reading recomputes all routes and rejects disagreement with that
projection; sorted node identity, exact inventory, width and all existing
CPU/IRQ/clock/reset contracts remain checked. Compiler/source hashes include
the fabric adapter and original fabric input. Flat IR v3 retains its original
serialization and semantics. Backends/BSPs consume canonical IR, not source YAML.

Current native lowering supports a rooted tree, one homogeneous CPU physical
view, 4 KiB aligned decoder windows and 12..48-bit spaces. Cycles, disconnected
nodes, multiple parents/aliases, local shadowing, clipped mappings, unknown
protocols and width overflow fail before output. Source model packages in a
fabric flow require explicit prebuilt executable/hash/license bindings for now.

The two fixtures place Chipyard GCD and OpenTitan GPIO behind two decoder levels.
Their leaf address spaces deliberately reuse local addresses; CPU-visible MMIO
is above 4 GiB. Mandatory acceptance runs actual independent source-IP MMIO/IRQ
and IR-derived bare-metal/FreeRTOS/Zephyr/µC/OS-II/III guests on QEMU and gem5.
Fixtures are separate from the full architectural inventories.

The decoder is lowered into functional address dispatch. It is not an AXI or
TileLink transaction engine, timed network, asynchronous clock/reset bridge,
IOMMU, DMA/coherence model or arbitrary per-initiator remapping implementation.
Direct structural IP-XACT intake retains its prior scope; importing general
standard bus/addressSpace/bridge XML is a separate open M1 exit. Linux on these
particular translated fixtures is not qualified merely by the existing Linux
profiles. Broad M1-M9 completion remains tracked under upgrade/.
