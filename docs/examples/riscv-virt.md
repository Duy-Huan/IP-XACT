# RISC-V virt reference project

This original synthetic design instantiates four exact VLNV components under
`socsim.example:reference`: rv64, ram, uart and finisher, each at version 1.0.
It describes the subset exercised by the reference test, not all devices on virt.

| Instance | Address | Range | Model |
|---|---|---|---|
| rv64 | No MMIO | One hart | QEMU rv64 |
| ram | 0x80000000 | 128 MiB | virt RAM |
| uart | 0x10000000 | 0x100 | virt NS16550 |
| finisher | 0x00100000 | 0x1000 | virt SiFive test |

From the repository root, activate `.venv`, run `bash scripts/build_firmware.sh`,
then `socsim run examples/riscv-virt --firmware build/firmware/boot.elf --out
results/example-001`. Passing serial output is exactly `SOCSIM_BOOT_OK` plus a
newline and simulator exit is zero. Use a fresh output directory for each run.

Inspect `report.json`, `report.md`, `stdout.log`, `stderr.log`, `soc-ir.json`,
`plan.json`, `command.json` and snapshots under `inputs/`. A test marker printed
by the host is not sufficient; the real firmware must execute on the simulator.
`tests/test_qemu.py` compiles independent fail/hang workloads as negative controls.

Do not change placements and keep calling it this profile. Add a custom machine
adapter when topology differs. These tests do not assert interrupt, SMP, cache,
security or OS support. No company data or upstream firmware is used.
