# One platform configuration for RTL CPU and software layers

`usp platform` now selects bare-metal, FreeRTOS, µC/OS-II, µC/OS-III and Zephyr
through one configuration for either actual supported RTL CPU and QEMU/gem5
memory engines. It dispatches to the existing source-verified kernel/BSP adapters;
it does not replace RTL instruction execution with an ISS. Native RTL clocks/RVFI
remain the source of CPI/IPC. Native gem5 IRQ contexts must execute no instructions.

The checked-in `platforms/{chipyard-ibex,custom-ibex-wb}-rtos-{qemu,gem5}.yaml`
profiles select all five workloads. Each `software_builds` entry names an existing
verified firmware build directory, resolved relative to the configuration file.
The example paths match the source-build scripts' default directories. Build those
first with scripts/rtl_os_bringup.sh and scripts/rtl_zephyr_bringup.sh, supplying
the pinned sources, generated machines, actual RTL CPU builds and memory engines
described in rtl-os.md and rtl-zephyr.md.

```sh
usp platform plan examples/rv64-ips --config platforms/chipyard-ibex-rtos-gem5.yaml
usp platform run examples/rv64-ips --config platforms/chipyard-ibex-rtos-gem5.yaml \
  --generated build/rv64-ips --models build/source-ip-models \
  --bridge build/rtl-master-gem5 --rtl-cpu-build build/rtl-cpu-ibex \
  --out results/rtl-all-software
usp platform verify results/rtl-all-software
```

For AI_SOC/AIZ_SOC, use their bring-up project/generated directory and each
kernel's matching map build directory. Do not substitute rv64-ips firmware for a
different map. Plan/selection rejects missing or extra software entries, Linux,
boot-sequence/MMU workloads, unsupported CPU/provider combinations, QEMU cost
estimates for RTL, and OS clock settings other than the verified 50 MHz BSP clock.
OS inputs must be source-built positive smoke profiles; arbitrary external ELFs,
swapped µC/OS kernels and intentionally disabled-tick builds fail before execution.

The aggregate records the immutable execution plan and derived RV32 target IR.
Each stage preserves its original kernel build, firmware, CPU/engine provenance,
IRQ/register evidence, native performance and independently verified report.
Verification checks all artifact hashes, workload membership, engine, CPU model,
source manifest identity, clock, base-map identity and every actual stage result.
The aggregate cannot promote a modified CPI/IPC or an invented full-architecture
completion claim. Reports are JSON and Markdown; output directories must be new.

This closes the earlier generic-platform-YAML gap for these four existing RV32
M-mode kernel/BSP profiles. It does not provide MMU/Linux RTL, arbitrary RTL bus
conversion, Rocket/BOOM, FPU/vector task context or global timing calibration.
FreeRTOS retains its timer/UART acceptance scope; the other selected kernel
profiles additionally test actual Chipyard GCD/OpenTitan GPIO drivers. Separate
standalone kernel commands remain available and their negative tests remain
mandatory. Local promotion adds USP_REQUIRE_RTL_PLATFORM=1; the manual RTL OS
workflow executes the unified configuration tests after building all firmware.
