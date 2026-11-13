# IP-XACT RISC-V virtual platform

A headless compiler and execution toolchain: IEEE 1685-2022 IP-XACT → typed SoC IR →
strict model binding → generated QEMU machine and software artifacts → real guest
execution with evidence. The master-spec objective and development rules are in
[agent.md](agent.md).

The implemented reference path generates and compiles an RV64 machine, generates
an external Zephyr board, builds Zephyr and checks real CLINT timer and UART/PLIC
interrupts. A FreeRTOS BSP verifies actual tick preemption, saved registers and
UART/PLIC task notification. A separate OpenSBI/Linux path boots an original initramfs. A second
profile connects guest MMIO/IRQ to real SystemC TLM or an OpenTitan FIFO compiled
with Verilator. These are constrained, tested profiles, not arbitrary SoC support.

The source IP profile additionally compiles Chipyard's actual GCD Chisel core and
OpenTitan's complete GPIO/TL-UL peripheral, connected to guest MMIO and distinct
PLIC IRQs. It is a peripheral integration, not a Rocket/Earlgrey full-SoC build.

Actual RV32 RTL CPU masters now run source-built FreeRTOS and both µC/OS-II/III
kernels with QEMU or gem5 owning memory; see [RTL OS](docs/rtl-os.md). Native
gem5 CPU cache/memory traffic can also use a live Ruby/Garnet network while the
same Zephyr guest exercises SystemC source IPs; see [live Garnet](docs/live-garnet.md).
Both paths have mandatory native promotion gates and explicit remaining limits.

## Development setup

Python 3.11+; use the pinned dependencies through uv. Native build prerequisites
and exact source pins are documented in [build reproducibility](docs/build-reproducibility.md).

```bash
uv sync --frozen --extra dev --extra bringup --extra models
source .venv/bin/activate
bash scripts/fetch_upstreams.sh
export CROSS_COMPILE=riscv64-unknown-elf-
bash scripts/bringup.sh
```

Set USP_FETCH_LINUX=1, USP_FETCH_MODELS=1 or USP_FETCH_FREERTOS=1 during explicit dependency acquisition
for the additional sources. Normal generation and simulation do not download
workloads. QEMU's first Meson configuration fetches three immutable wrap-git
subprojects; those acquisition steps are documented with the upstream pins.

## Supported commands

```bash
usp ipxact validate examples/rv64-minimal
usp elaborate examples/rv64-minimal --out build/soc-ir.json
usp generate examples/rv64-minimal --out build/rv64-minimal
usp models explain examples/rv64-minimal --instance uart
usp build examples/rv64-minimal --generated build/rv64-minimal --source-qemu .tools/upstream/qemu
usp software build examples/rv64-minimal --os zephyr --generated build/rv64-minimal \
  --source-zephyr .tools/upstream/zephyr --cross-compile riscv64-unknown-elf-
usp test examples/rv64-minimal --generated build/rv64-minimal --out results/zephyr-001
usp report results/zephyr-001 --format md
```

`usp` and `socsim` share the CLI. Keep each run in a new output directory. Source,
generator, registry and artifact identity changes require regeneration/rebuild;
old evidence is not overwritten. Failed guests/timeouts remain failed results.
`usp backends` reports scope and availability, not universal engine equivalence.

## Guides and status

- [Verification status and phase exits](docs/verification-status.md)
- [Milestone checklist and multi-SoC acceptance](docs/milestone-checklist.md)
- [AI_SOC / AIZ_SOC designs and scoped flow](docs/dual-soc.md)
- [OpenROAD RTL-to-layout reference flow](docs/openroad.md)
- [Complete CPU-owned RTL test-chip flow](docs/rtl-testchips.md)
- [Architecture and supported IP-XACT subset](docs/architecture.md)
- [Component parameters and safe integer expressions](docs/parameters.md)
- [Scalar registers and executable model contracts](docs/registers.md)
- [Configured structural hierarchy](docs/examples/rv64-hierarchy.md)
- [Create a SoC](docs/creating-soc.md)
- [Generated QEMU/Zephyr reference](docs/examples/rv64-minimal.md)
- [SystemC/OpenTitan RTL bridge](docs/examples/rv64-hybrid.md)
- [Chipyard GCD and OpenTitan GPIO](docs/examples/rv64-ips.md)
- [One SoC/Zephyr ELF with SystemC IPs on QEMU or gem5](docs/unified-soc.md)
- [Configurable CPU providers, engines and workloads](docs/cpu-platforms.md)
- [Software layers and native/estimated CPI/IPC](docs/software-layers-performance.md)
- [Research direction and reference-paper ledger](docs/research-direction.md)
- [Full Rocket/Earlgrey integration boundary](docs/full-soc-integration.md)
- [Shared gem5 SE workload](docs/gem5-se.md)
- [FreeRTOS preemption and interrupt acceptance](docs/freertos.md)
- [Firmware, Linux, Yocto and RTOS](docs/firmware-and-os.md)
- [Development, CI and promotion](docs/development.md)
- [Cloud startup](docs/cloud-environment.md)
- [Project setup/update skills](skills/README.md)

The legacy `examples/riscv-virt` QEMU 10.0.13 bare-metal smoke remains a separate
compatibility test. The generated machine uses pinned QEMU 10.0.0 and deliberately
relocates UART away from virt. The parser supports bounded component integer
parameters, structural hierarchy and scalar register/field metadata. The GCD
profile checks its exact executable register contract and generates a header used
by the real guest. General access side effects, buses, unsupported parameter types
and nonstructural views are rejected.
Yocto image completion depends on permitted upstream downloads; consult the phase
status for observed blockers. Full Chipyard/Earlgrey SoCs and generic hardware/security integrations
remain future work. No calibrated timing or silicon equivalence is claimed.

No internal archive, source or private Git history is included. Examples and
adapters are original; public Accellera schemas retain their Apache-2.0 notices
and checksums under src/socsim/schemas. Referenced upstream source licenses remain
in their external checkouts. Choose project-wide redistribution terms before a
public binary release; per-file SPDX identifiers apply to the indicated files.

Actual Chipyard Ibex and normalized custom RTL CPUs run bare-metal with QEMU or
gem5 memory/peripherals. See [the adapter guide](docs/rtl-cpu-adapters.md).
