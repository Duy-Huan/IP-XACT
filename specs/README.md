# Native SoC input specifications

AI_SOC-native.yaml and AIZ_SOC-native.yaml are executable generator acceptance
inputs. They specify instance identity, model, memory/I/O placement, IRQ and boot
ownership. They deliberately use different instance counts and maps, independent
of legacy board roles. Read docs/native-soc-generator.md for build/run commands,
qualified model contracts and the current QEMU/bare-metal scope. These fixtures
do not implement the complete AI_SOC/AIZ_SOC architecture inventories.
