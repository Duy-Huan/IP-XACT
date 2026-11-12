# Configured structural hierarchy

This original synthetic example selects an IEEE 1685-2022 designConfiguration.
Its top design contains the usual CPU/RAM/ROM/CLINT/PLIC/finisher leaves and a
serial wrapper. The wrapper's selected rtl view references a designInstantiation,
which references a nested design containing the parameterized UART. The leaf's
source path is serial.uart; an explicit supplemental instance_aliases entry maps
it to the logical uart identifier used by the strict model/hardware profile.

```bash
usp validate examples/rv64-hierarchy
usp generate examples/rv64-hierarchy --out build/rv64-hierarchy
usp build examples/rv64-hierarchy --generated build/rv64-hierarchy \
  --source-qemu .tools/upstream/qemu
usp software build examples/rv64-hierarchy --os zephyr --generated build/rv64-hierarchy \
  --source-zephyr .tools/upstream/zephyr --cross-compile riscv64-unknown-elf-
usp test examples/rv64-hierarchy --generated build/rv64-hierarchy --out results/hierarchy-001
```

Use the same frozen bringup dependencies and pinned QEMU/Zephyr sources as the
flat reference. The generated machine really boots Zephyr and requires UART,
timer and PLIC ISR assertions. The native workspace adds this as its eighth
target. CLI validation alone does not establish native readiness.

Supported structural input consists of component model/views/view and
instantiations/designInstantiation or designConfigurationInstantiation, and the
corresponding designRef/designConfigurationRef. A designConfiguration may select
a view per direct instance using viewConfiguration/instanceName/view@viewRef.
A single view is selected when no explicit choice is present; multiple views
without a choice fail. Configured-view parameter overrides remain unsupported.

The importer resolves exact document VLNVs from the project-local catalog. It
rejects missing/ambiguous references, invalid view choices, active design/view
configuration cycles, duplicate aliases and missing leaf placements. Recursion
is limited to 32 design levels and 4096 leaves. Cycle keys include selected
views, so two configurations of the same design are not automatically identical.
No document resolution fetches network sources or external files.

Instances retain their full hierarchy_path tuple in immutable IR. Without aliases,
their logical names are dotted source paths. Aliases must refer to existing leaves,
use safe unique logical identifiers and never change VLNV/model identity. Alias
configuration is included in input provenance. Generic import does not require
the supported backend role names, but lowering to a strict native profile does.

Placements explicitly name every full leaf path and supply absolute supplemental
offsets. They are not inherited parent offsets and do not infer bus translation.
Wrapper memory maps, wrapper parameters/propagation, physical ports, bus wiring,
nonstructural views and address translation remain unsupported and are rejected.
This is a structural hierarchy extension, not full IP-XACT elaboration semantics.

Tests cover configuration selection, forward references, two wrapper levels with
nested designConfigurationInstantiation, parameterized leaf overrides, deterministic
IR after relocation, cycles, ambiguous/unknown views, alias errors and actual
generated-machine Zephyr boot/IRQ acceptance.
