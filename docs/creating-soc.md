# Create and bind a SoC project

For a spec-driven native machine with variable instance names/counts, start with
[the native generator](native-soc-generator.md) and specs/AI_SOC-native.yaml.
It generates custom QEMU hardware and bare-metal software from the input map.
The sections below describe the earlier fixed-profile APIs, which remain useful
for their existing OS and backend regressions.

Start from `examples/riscv-virt` for the implemented reference profile. Author
components with exact vendor/library/name/version identities and memory blocks,
then instantiate them in `ipxact/design.xml`. Use the 2022 namespace and bundled
XSD ordering. Memory baseAddress is a local offset; simulation.yaml placements
add the board address. Keep all XML and manifest paths under the project root.

The three supplemental files serve different purposes:

- `simulation.yaml`: schema version, selected design/profile, manifest paths and
  explicit placements for every instance, including non-MMIO CPU at offset zero.
- `models.yaml`: exact VLNV/backend/implementation triples. Backend identifiers
  select built-in reviewed implementations, not arbitrary commands or modules.
- `toolchain.lock.yaml`: required simulator release for the current profile.

Run `socsim validate <project>` for XSD and subset semantics, `socsim elaborate
<project> --out <new-file>` for IR, then `socsim plan <project> --backend qemu` for
binding and board compatibility. Validation alone does not promise executable IP.
Change the UART address to an unsupported location to confirm the plan fails.

For new IP, first specify register side effects, access widths, reset behavior,
interrupt signaling, DMA reachability and protocol semantics. Add native tested
models per engine, fixtures and binding validation. Extend the parser deliberately
with negative tests when additional IP-XACT elements become supported. Never
replace unsupported devices with empty handlers. Arbitrary custom layouts require
a new machine adapter; merely changing the manifest cannot rewire upstream `virt`.

Build firmware only after a compatible plan exists. The current example linker
and addresses are manually maintained for its fixed board and cross-checked by
the profile. Generated BSP/header/linker output is a later milestone. Record inputs,
build commands, tool identity and firmware hashes; keep company models and run
bundles out of public commits and CI.

## Custom-machine and hybrid examples

Use examples/rv64-minimal for the generated-machine path, rather than modifying
legacy virt. Its schema-version-2 manifest names hardware.yaml. The strict CPU
contract supplies required ISA/privilege/MMU/reset/entry/timebase; five explicit IRQ
routes connect CLINT, PLIC, UART and the hart. The model catalog resolves exact
VLNV/implementation/contract identities and pins QEMU source. The hybrid example
adds the FIFO and a separate PLIC source, selected explicitly at execution.

`usp generate` writes machine.c, soc.h, memory.ld, software-contract.json, model
lock and generated Zephyr artifacts. The non-hybrid profile also emits a Linux DTS
and provisional Yocto layer metadata. A generated file is not proof its consumer
built successfully: inspect the relevant software/model/run reports. Only the
selected documented profiles are accepted; this release does not synthesize an
arbitrary company SoC from registers or RTL.
