# Create and bind a SoC project

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
