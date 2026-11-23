# Automation & Toolchain Scripts (`scripts/`)

| Metadata Field | Value |
|---|---|
| **Document ID** | `SOCSIM-SCR-001` |
| **Current Version** | `0.3.0a5` |
| **Status** | Active / CI-Verified (`bash -n` + `shellcheck` + `actionlint`) |
| **Normative Authority** | `scripts/ci_lint.sh`, `scripts/check_docs.py`, `tests/test_project_standardization.py` |

---

## Document Revision History

| Version | Release Date | Milestone / Gate | Author / Scope | Summary of Changes |
|---|---|---|---|---|
| `0.2.0` | `2026-11-18` | `v0.2.0` Baseline | Bring-up & CI | Added `setup.sh`, `fetch_upstreams.sh`, `podman.sh`, `ci_lint.sh`, `regression.sh`, `promote.sh`, and OS/RTL bring-up wrappers. |
| `0.3.0a5` | `2026-11-23` | Refactoring Gate | Environment Standardization | Added shared `scripts/lib/common.sh` and dedicated modular setup scripts (`setup_venv.sh`, `setup_qemu.sh`, `setup_gem5.sh`, `setup_chipyard.sh`, `setup_opentitan.sh`, `setup_all.sh`) plus this catalog. |

---

## Script Catalog

### 1. Standardized Environment & Platform Setup (Recommended Entry Points)

| Script | Purpose | Key Flags |
|---|---|---|
| [`lib/common.sh`](lib/common.sh) | Shared logging, command/library checks, Python version gate, pinned Git verification | Sourced by `setup_*.sh` |
| [`setup_venv.sh`](setup_venv.sh) | Create/sync `.venv` via `uv sync --frozen` or `python3 -m venv` + `requirements*.txt` | `--method auto\|uv\|pip`, `--extra base\|dev\|models\|bringup\|all`, `--check`, `--verify` |
| [`setup_qemu.sh`](setup_qemu.sh) | Audit, fetch, or build pinned QEMU `v10.0.0` (`7c949c53e936`) | `--check`, `--fetch-only`, `--build`, `--jobs N` |
| [`setup_gem5.sh`](setup_gem5.sh) | Audit, fetch, or build pinned gem5 `v24.1.0.3` (`b9da2bfe1e21`, `USE_SYSTEMC=0`) | `--check`, `--fetch-only`, `--build`, `--jobs N` |
| [`setup_chipyard.sh`](setup_chipyard.sh) | Audit or fetch pinned Chipyard `1.13.0` (`69eba860a352`) + SHA-256 `sbt`/`firtool` | `--check`, `--fetch-only`, `--tools`, `--tools-only` |
| [`setup_opentitan.sh`](setup_opentitan.sh) | Audit or fetch pinned OpenTitan `earlgrey_silver_release_v5` (`ed044fc9760b`) + Verilator/SystemC | `--check`, `--fetch-only`, `--verify-rtl` |
| [`setup_all.sh`](setup_all.sh) | Orchestrate all setup scripts with selective flags and a unified readiness summary | `--check`, `--fetch-only`, `--build`, `--all`, `--venv`, `--qemu`, `--gem5`, `--chipyard`, `--opentitan` |

### 2. Container, CI, Quality & Promotion Gates

| Script | Purpose |
|---|---|
| [`podman.sh`](podman.sh) | Build committed-source OCI SDK image, run offline `smoke`, `qa`, `mcp`, `run`, and `export` |
| [`ci_lint.sh`](ci_lint.sh) | Run `actionlint` + `shellcheck` on workflows and `bash -n` on every git-tracked `*.sh` |
| [`check_docs.py`](check_docs.py) | Verify all required engineering documents and example guides exist and are non-empty |
| [`setup.sh`](setup.sh) | Minimal CI virtual environment sync (`uv sync --frozen --extra dev` + `socsim validate`) |
| [`regression.sh`](regression.sh) | Full local Python regression suite (ruff, mypy, docs, pytest, upgrade audit) |
| [`promote.sh`](promote.sh) | Branch promotion verification helper (`dev` → `main`) |
| [`emit_ci_job.py`](emit_ci_job.py) | Emit structured CI job provenance JSON for Git-note receipts |

### 3. Upstream Dependency & Bring-up Helpers

| Script | Purpose |
|---|---|
| [`fetch_upstreams.sh`](fetch_upstreams.sh) | Batch pinned Git acquisition (QEMU, Zephyr, FreeRTOS, Chipyard, Linux, OpenSBI, OpenTitan, gem5) |
| [`setup_chipyard_tools.sh`](setup_chipyard_tools.sh) | SHA-256 verified acquisition of `sbt-launch-1.10.7.jar` and `llvm-firtool-1.62.0-linux-x64.jar` |
| [`setup_user_tools.sh`](setup_user_tools.sh) | User-space Debian APT prefix bootstrap (non-root) |
| [`setup_bringup_tools.sh`](setup_bringup_tools.sh) | Pinned Debian `trixie` cross-compiler / SystemC / Verilator user-prefix extraction |
| [`setup_rocket_tools.sh`](setup_rocket_tools.sh) | RocketTile elaboration helper prerequisites |
| [`setup_synthesis_tools.sh`](setup_synthesis_tools.sh) | Yosys / Slang synthesis helper setup |
| [`setup_openroad.sh`](setup_openroad.sh) | Pinned OpenROAD-flow-scripts reference layout container setup |
| [`bringup.sh`](bringup.sh), [`freertos_bringup.sh`](freertos_bringup.sh), [`linux_bringup.sh`](linux_bringup.sh), [`gem5_bringup.sh`](gem5_bringup.sh), [`hybrid_bringup.sh`](hybrid_bringup.sh), [`source_ips_bringup.sh`](source_ips_bringup.sh), [`native_soc_bringup.sh`](native_soc_bringup.sh), [`dual_soc_bringup.sh`](dual_soc_bringup.sh), [`hierarchy_bringup.sh`](hierarchy_bringup.sh), [`rocket_bringup.sh`](rocket_bringup.sh), [`rtl_cpu_bringup.sh`](rtl_cpu_bringup.sh), [`rtl_os_bringup.sh`](rtl_os_bringup.sh), [`rtl_zephyr_bringup.sh`](rtl_zephyr_bringup.sh), [`rtl_testchip_flow.sh`](rtl_testchip_flow.sh) | End-to-end target bring-up automation scripts for individual hardware/OS acceptance profiles |
| [`build_firmware.sh`](build_firmware.sh), [`native_external_bringup.py`](native_external_bringup.py), [`fetch_papers.py`](fetch_papers.py) | Bare-metal ELF builder, external SystemC model bring-up, and reference paper ledger fetcher |

---

## Verification Rules

Every shell script tracked in Git MUST:
1. Start with `#!/usr/bin/env bash` (or `#!/bin/sh` for POSIX container helpers) and `set -euo pipefail`.
2. Pass `bash -n <script>` and `shellcheck <script>` with zero warnings.
3. Never mutate host system packages (`/usr`, `/etc`) or overwrite dirty user checkouts.
