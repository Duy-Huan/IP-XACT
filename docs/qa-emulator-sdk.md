# CHIA-style HW/SW Emulator SDK and Real-Execution QA/QC Guide

This guide documents the composable Python SDK (`src/socsim/sdk.py`), the
unseen-design QA fixture (`specs/qa/`), and the tamper-checked QA/QC runner
(`usp qa`).

## Emulator SDK (`socsim.sdk`)

`src/socsim/sdk.py` packages the USP HW/SW compiler and bounded subprocess
execution with CHIA-inspired ergonomics while keeping every provenance and
reproducibility contract strict:

- **`BuildArtifact`, `RunResult`, `CrossEngineDiffResult`**: typed, immutable
  dataclasses carrying SHA-256 digests, execution durations and an explicit
  `replayed: bool` field.
- **`ProcessRegistry`**: launches external commands in isolated process groups
  (`start_new_session=True`) and guarantees zero orphan child processes via
  `terminate_all()` (`SIGTERM` followed by bounded `SIGKILL`).
- **`ArtifactCache`**: content-addressed SHA-256 cache that verifies payload
  integrity (`E_CACHE_TAMPER` on modification) and always marks replayed
  entries with `replayed: true`.
- **`EmulatorNode`**: wraps `generate_soc()` and `run_command()` with timing
  profiling (`write_profile()`).

## Unseen-Design QA Fixture (`specs/qa/`)

`specs/qa/unseen_dual_ip_soc.yaml` (`Helios_QA_SoC`) uses brand-new instance
names, >4 GiB physical addresses, dual UART/GCD/GPIO/RAM instances and custom
IRQs to verify that no SoC-name branch is required in the compiler.

## Real-Execution QA/QC CLI (`usp qa`)

```sh
usp qa run --suite quick --out results/qa-quick
usp qa run --suite unseen --out results/qa-unseen
usp qa run --suite wheel --out results/qa-wheel
usp qa run --suite full --out results/qa-full
usp qa scorecard results/qa-full
```

Every `usp qa run` writes `scorecard.json`, `scorecard.md`, `profile.json`,
raw logs and `qa-manifest.json` (SHA-256 of every file in the bundle). Running
`usp qa scorecard <dir>` recomputes every digest and rejects tampered bundles
with `E_QA_VERIFY`.

## Honest Boundaries

Local QA suites verify static lint/typing/docs, unit contracts, process/cache
hygiene, unseen-design generation/BSP/domains/tamper defense and clean isolated
wheel installation. Full simulator/RTL/ASIC/Yocto/Podman release qualification
requires executing the corresponding native or hosted workflows (`regression`,
`custom-zephyr`, `gem5-se`, `scripts/promote.sh` and `scripts/podman.sh`).
