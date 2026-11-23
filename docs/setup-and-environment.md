# Environment Setup & Multi-Platform Toolchain Guide

| Metadata Field | Value |
|---|---|
| **Document ID** | `SOCSIM-ENV-001` |
| **Current Version** | `0.3.0a5` |
| **Status** | Active / Verified (`setup_venv.sh`, `setup_all.sh`, `tests/test_project_standardization.py`) |
| **Normative Authority** | `pyproject.toml`, `uv.lock`, `requirements*.txt`, `scripts/setup_*.sh`, `Containerfile` |

---

## Document Revision History

| Version | Release Date | Milestone / Gate | Author / Scope | Summary of Changes |
|---|---|---|---|---|
| `0.2.0` | `2026-11-18` | `v0.2.0` | Build Reproducibility | Initial `uv sync` + `fetch_upstreams.sh` + `setup_user_tools.sh` workflow. |
| `0.3.0a5` | `2026-11-23` | Refactoring Gate | Environment Standardization | Added pinned `requirements*.txt` profiles, `scripts/lib/common.sh`, dedicated `setup_venv.sh`, `setup_qemu.sh`, `setup_gem5.sh`, `setup_chipyard.sh`, `setup_opentitan.sh`, `setup_all.sh`, `Makefile`, and `containers/compose.yaml`. |

---

## 1. Execution Modes Overview

Choose the workflow that matches your host environment:

| Mode | Best For | Requirements | Entry Point |
|---|---|---|---|
| **Rootless Podman SDK (Recommended)** | Full native emulation (QEMU + SystemC 3.0.1 + Verilator 5.032 + RISC-V GCC + Yosys + JDK 21) with zero host pollution | `podman` (or `docker`) on Linux amd64 | `bash scripts/podman.sh build && bash scripts/podman.sh run` |
| **Native Python Virtualenv (`uv`)** | Fast local development, compiler tests, MCP server, linting, and CI checks | Python `>= 3.11`, `uv` | `bash scripts/setup_venv.sh --method uv --extra all --verify` |
| **Native Python Virtualenv (`pip` + `requirements*.txt`)** | Air-gapped / corporate mirrors or standard Python tooling without `uv` | Python `>= 3.11`, `venv`, `pip` | `bash scripts/setup_venv.sh --method pip --extra all --verify` |
| **Full Native Multi-Platform Source Builds** | Building custom QEMU `v10.0.0`, gem5 `v24.1.0.3`, Chipyard `1.13.0`, OpenTitan RTL on host Linux | 32 GiB RAM, 100 GiB disk, Debian/Ubuntu/Fedora build deps | `bash scripts/setup_all.sh --all --build --jobs "$(nproc)"` |

---

## 2. Python Virtual Environment & Pinned `requirements*.txt`

All Python dependencies are strictly pinned in [`pyproject.toml`](../pyproject.toml) and [`uv.lock`](../uv.lock), and mirrored verbatim in five `requirements*.txt` profiles (enforced by automated unit test [`tests/test_project_standardization.py`](../tests/test_project_standardization.py)):

| File | Profile (`--extra`) | Packages Included |
|---|---|---|
| [`requirements.txt`](../requirements.txt) | `base` | `lxml==6.0.2`, `PyYAML==6.0.3`, `jsonschema==4.25.1`, `pyelftools==0.32` |
| [`requirements-dev.txt`](../requirements-dev.txt) | `dev` | Core + `pytest==9.0.2`, `ruff==0.14.10`, `mypy==1.18.2`, `types-PyYAML==6.0.12.20261111` |
| [`requirements-models.txt`](../requirements-models.txt) | `models` | Core + `scons==4.8.1` (for gem5 builds) |
| [`requirements-bringup.txt`](../requirements-bringup.txt) | `bringup` | Core + `cmake==3.31.6`, `ninja==1.11.1.4`, `west==1.4.0`, `meson==1.7.0`, `pykwalify==1.8.0`, `packaging==26.3` |
| [`requirements-all.txt`](../requirements-all.txt) | `all` | Complete aggregate matching `uv sync --frozen --all-extras` |

### Creating & Auditing the Virtual Environment

```bash
# Auto-detect uv (falls back to python3 -m venv + pip + requirements-all.txt)
bash scripts/setup_venv.sh --extra all --verify

# Force pure python3 -m venv + pip workflow (no uv required)
bash scripts/setup_venv.sh --method pip --extra dev --venv .venv --verify

# Non-destructive audit of existing .venv, Python >= 3.11, CLI & requirements*.txt
bash scripts/setup_venv.sh --check
```

Verified output of `bash scripts/setup_venv.sh --check`:

```text
[INFO] Auditing Python environment for socsim-platform (venv=.../.venv)
[ OK ] Python version: 3.11.2 (>= 3.11) at python3
[ OK ] Pinned file present: requirements.txt
[ OK ] Pinned file present: requirements-dev.txt
[ OK ] Pinned file present: requirements-models.txt
[ OK ] Pinned file present: requirements-bringup.txt
[ OK ] Pinned file present: requirements-all.txt
[ OK ] uv available: uv 0.13.7 (x86_64-unknown-linux-gnu)
[ OK ] CLI installed: usp (0.3.0a5)
[ OK ] Core Python imports verified (lxml, yaml, jsonschema, elftools, socsim)
[ OK ] Python venv audit PASSED.
```

---

## 3. Modular Multi-Platform Toolchain Setup Scripts

Every upstream simulator/IP repository is pinned to an immutable Git tag **and** exact 40-character SHA-1 commit hash. Scripts never overwrite dirty checkouts or install system-wide APT packages silently.

### 3.1 Unified Orchestrator (`scripts/setup_all.sh`)

```bash
# Non-destructive audit of Python venv + QEMU + gem5 + Chipyard + OpenTitan
bash scripts/setup_all.sh --check

# Clone & SHA-verify all pinned upstream repositories (without heavy compilation)
bash scripts/setup_all.sh --all --fetch-only

# Selective setup: fetch & build QEMU + gem5 only with 8 parallel jobs
bash scripts/setup_all.sh --qemu --gem5 --build --jobs 8
```

### 3.2 QEMU (`scripts/setup_qemu.sh`)

- **Pinned Upstream:** `https://github.com/qemu/qemu.git` — tag `v10.0.0`, SHA `7c949c53e936aa3a658d84ab53bae5cadaa5d59c`.
- **Host Prerequisites:** `gcc`, `g++`, `make`, `ninja`, `meson`, `pkg-config`, `glib-2.0`, `pixman-1`, `dtc`.
- **Commands:**
  ```bash
  bash scripts/setup_qemu.sh --check
  bash scripts/setup_qemu.sh --fetch-only
  bash scripts/setup_qemu.sh --build --jobs "$(nproc)"
  ```
- **Output Binary:** `.tools/build/qemu/qemu-system-riscv64` and `qemu-system-riscv32`.

### 3.3 gem5 (`scripts/setup_gem5.sh`)

- **Pinned Upstream:** `https://github.com/gem5/gem5.git` — tag `v24.1.0.3`, SHA `b9da2bfe1e21f6efa2730edd4e80e58c5d965458`.
- **Host Prerequisites:** `gcc`, `g++`, `m4`, `pkg-config`, `python3-config` (`python3-dev`), `zlib`, `libboost-dev`, `scons==4.8.1` (from `--extra models`).
- **Build Policy:** Configures `USE_SYSTEMC=0` (external SystemC co-simulation process profile per [`CHANGELOG.md`](../CHANGELOG.md)) so gem5 avoids building its unused embedded SystemC kernel.
- **Commands:**
  ```bash
  bash scripts/setup_gem5.sh --check
  bash scripts/setup_gem5.sh --fetch-only
  bash scripts/setup_gem5.sh --build --jobs "$(nproc)"
  ```
- **Output Binary:** `.tools/upstream/gem5/build/RISCV/gem5.opt`.

### 3.4 Chipyard & Chisel/CIRCT Tools (`scripts/setup_chipyard.sh`)

- **Pinned Upstream:** `https://github.com/ucb-bar/chipyard.git` — tag `1.13.0`, SHA `69eba860a352343e4ac6b6df0f3638a79a86ec78`.
- **SHA-256 Locked Generator Tools (via `scripts/setup_chipyard_tools.sh`):**
  - `sbt-launch-1.10.7.jar` (SHA-256 `3cca02818047327a83efde776103a1ef92f76f72c062badbbb062499a3270c07`)
  - `llvm-firtool-1.62.0-linux-x64.jar` (SHA-256 `2da2cd5274b9470e461a1f5e384d134b6da7b73ca7020cf21b371b097ff7cb01`, extracted `firtool` SHA-256 `0dd855e895e7763d4fe07f537c48631f8cbd79725a01256995ebcc1fcacb97aa`)
- **Host Prerequisites:** OpenJDK 21 (`java`), `verilator` 5.032, `riscv64-unknown-elf-gcc`, `curl`.
- **Commands:**
  ```bash
  bash scripts/setup_chipyard.sh --check
  bash scripts/setup_chipyard.sh --fetch-only --tools
  bash scripts/setup_chipyard.sh --tools-only
  ```

### 3.5 OpenTitan RTL & SystemC/Verilator (`scripts/setup_opentitan.sh`)

- **Pinned Upstream:** `https://github.com/lowRISC/opentitan.git` — tag `earlgrey_silver_release_v5`, SHA `ed044fc9760bdf9fc075d0015ba1db07fa075355`.
- **Host Prerequisites:** `verilator` 5.032, `libsystemc-dev` 3.0.1 (`systemc.h`), `g++` (C++17), `yosys` 0.52.
- **Commands:**
  ```bash
  bash scripts/setup_opentitan.sh --check
  bash scripts/setup_opentitan.sh --fetch-only --verify-rtl
  ```

### 3.6 Additional OS Upstreams (Zephyr, FreeRTOS, Linux, OpenSBI)

Use [`scripts/fetch_upstreams.sh`](../scripts/fetch_upstreams.sh) when building full OS images:

```bash
USP_FETCH_FREERTOS=1 USP_FETCH_LINUX=1 USP_FETCH_CHIPYARD=1 USP_FETCH_MODELS=1 \
  bash scripts/fetch_upstreams.sh
```

---

## 4. Podman SDK Container Workflow

When you want an immediate, fully provisioned native emulator environment (QEMU + SystemC 3.0.1 + Verilator 5.032 + RISC-V GCC + Yosys + OpenJDK 21):

```bash
# Build OCI image from committed HEAD (clean working tree required)
make podman-build

# Run offline (--network=none) native smoke verifying Dual-Hart SMP + 6x RTL + MCP + pytest
make podman-smoke

# Launch full QA suite or interactive shell
make podman-qa
make podman-run
```

See [`docs/podman.md`](podman.md) and [`containers/README.md`](../containers/README.md) for SELinux labels, proxy CA secrets, and `containers/compose.yaml` usage.

---

## 5. Verification & Troubleshooting

| Symptom | Root Cause | Resolution |
|---|---|---|
| `setup_qemu.sh --check` warns `meson` or `ninja` missing | Bring-up Python tools not installed | Run `bash scripts/setup_venv.sh --extra bringup` and `source .venv/bin/activate` |
| `setup_gem5.sh --check` warns `SCons missing` | Models Python extra not installed | Run `bash scripts/setup_venv.sh --extra models` |
| `usp unified-soc run` reports `BLOCKED: libsystemc-dev / verilator missing` | Native EDA libraries not installed on host | Run inside Podman SDK (`bash scripts/podman.sh run`) or install `libsystemc-dev=3.0.1-1 verilator=5.032` |
| `scripts/podman.sh build` fails with dirty checkout | Uncommitted edits in tracked files | Commit changes on `dev` first (`git status -sb`) — builds strictly use `git archive HEAD` |
| `scripts/ci_lint.sh` fails on `actionlint` | Missing `.tools/ci/bin/actionlint` | Install `actionlint v1.7.9` and `shellcheck 0.10.0` per [`docs/ci-local.md`](ci-local.md) |
