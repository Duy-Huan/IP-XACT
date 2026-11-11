# IP-XACT RISC-V virtual platform

Define hardware metadata with IEEE 1685-2022 IP-XACT, elaborate a deterministic IR,
and bind it to explicitly supported simulator models. This initial release runs
original bare-metal firmware on **QEMU 10.0.13 RISC-V `virt`** and records evidence.

This is the first working vertical slice. Arbitrary SoC machine generation, gem5,
Chipyard, OpenTitan, SystemC bridges, Zephyr and Yocto execution are planned.
See [capabilities and roadmap](docs/roadmap.md) for acceptance criteria.

## Quick start

Requirements: Python 3.11+, `uv`, `qemu-system-riscv64` 10.0.13,
`riscv64-unknown-elf-gcc`, and `riscv64-unknown-elf-readelf`.
On Debian trixie the simulator package is `qemu-system-riscv`, not `qemu-system-misc`.
See [setup skill](skills/setup/SKILL.md) for installation options.

```bash
bash scripts/setup.sh
source .venv/bin/activate
socsim doctor
socsim validate examples/riscv-virt
socsim elaborate examples/riscv-virt --out build/soc-ir.json
socsim plan examples/riscv-virt --backend qemu
bash scripts/build_firmware.sh
socsim run examples/riscv-virt --firmware build/firmware/boot.elf --out results/first
bash scripts/regression.sh
```

Use a new `--out` directory for every run. Passing requires an actual guest serial
marker and a zero exit through the SiFive test finisher; timeouts and guest failures
produce failed reports. The runner currently verifies this smoke workload contract,
not arbitrary OS success. `socsim backends` reports capability status.

## Guides

- [Architecture and supported IP-XACT subset](docs/architecture.md)
- [Create a SoC project](docs/creating-soc.md)
- [Firmware, RTOS and Yocto direction](docs/firmware-and-os.md)
- [Reference design and real execution](docs/examples/riscv-virt.md)
- [Development, CI and promotion](docs/development.md)
- [Agent rules](agent.md) and [project skills](skills/README.md)

No internal reference archive, source or Git history is included. Example XML and
firmware are original synthetic assets. Public Accellera schema files are retained
under their Apache-2.0 terms; see `src/socsim/schemas/NOTICE.md`, `LICENSE.md` and
`provenance.json`. No project-wide license for original code is selected yet;
the owner must choose redistribution terms before publishing a release.
