# Native flow fixtures

These original public examples exercise source packages and IR-derived software;
they are not the complete AI_SOC/AIZ_SOC architectural inventories.

- AI_SOC-package.yaml builds a high-map single-hart SoC, two independently owned
  SystemC counters, their source package/driver, FreeRTOS, Zephyr, µC/OS-II/III and
  OpenSBI/Linux, with actual QEMU per-hart events and native gem5 statistics.
- AIZ_SOC.yaml selects a different existing map and source IPs, then generates
  bare-metal and all four single-hart native RTOS/Zephyr BSPs from its IR.
- AIZ_SOC-gem5.yaml builds firmware and runs only gem5; no QEMU source is required.
- AI_SOC-smp-profile.yaml runs four RV64 harts on both engines and verifies actual
  per-hart QEMU execution/data events against native gem5 instruction/cycle counters.

See [the execution guide](../../docs/native-flow.md) for prerequisites, commands,
ownership, provenance, negative tests and the remaining capability boundaries.
