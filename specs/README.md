# Native SoC input specifications

ipxact/AI_SOC, ipxact/AIZ_SOC and ipxact/AI_SOC-hierarchy provide direct structural
IP-XACT examples with native.yaml runtime bindings. Use --input-format ipxact.
See docs/canonical-native-ir.md; these share the checked native IR/backend path.

AI_SOC-native.yaml and AIZ_SOC-native.yaml are executable generator acceptance
inputs. They specify instance identity, model, memory/I/O placement, IRQ and boot
ownership. They deliberately use different instance counts and maps, independent
of legacy board roles. Read docs/native-soc-generator.md for build/run commands,
qualified model contracts and the QEMU/gem5, SMP/Linux and external-model contracts. These fixtures
do not implement the complete AI_SOC/AIZ_SOC architecture inventories.

AI_SOC-smp.yaml and AIZ_SOC-smp.yaml are four/two-hart native designs with 16/12
instances and physical addresses above 4 GiB. validation/smp-max.yaml tests the
maximum hart bound. These remain original regression fixtures. The external
counter example builds its descriptor with the actual compiled executable hash;
run scripts/native_external_bringup.py rather than using a fabricated checksum.
