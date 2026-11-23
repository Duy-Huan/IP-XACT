# Rootless Podman SDK & OCI Release Guide (Linux amd64)

| Metadata Field | Value |
|---|---|
| **Document ID** | `SOCSIM-OCI-001` |
| **Current Version** | `0.3.0a5-r1` |
| **Status** | Active / CI-Verified (`Podman SDK and OCI release`) |
| **Normative Authority** | `Containerfile`, `scripts/podman.sh`, `containers/smoke.sh`, `containers/compose.yaml`, `tests/test_container.py` |

---

## Document Revision History

| Version | Release Date | Milestone / Gate | Author / Scope | Detailed Summary of Changes |
|---|---|---|---|---|
| `0.2.0-r1` | `2026-11-18` | `container-v0.2.0-r1` | Container Baseline | Initial digest-pinned rootless Podman SDK (`uv 0.13.5` + `debian:trixie-slim`), committed-source `git archive` build, offline QEMU smoke, OCI archive export with `SHA256SUMS`. |
| `0.3.0a5-r1` | `2026-11-23` | `PR #1–#3` / Refactor | Enterprise SDK | Added `usp qa`, `socsim.sdk` (`EmulatorNode.run_in_podman`), `scripts/podman.sh mcp` stdio bridge, Dual-Hart SMP + 6× Verilator RTL + CHIA loop + Dashboard smoke, and `containers/compose.yaml`. |

---

## 1. Container Image Anatomy (`Containerfile`)

| Layer / Component | Exact Pin | Role |
|---|---|---|
| Base `uv` stage | `ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39...` | Deterministic frozen Python sync (`uv.lock`) |
| Base runtime stage | `debian:trixie-slim@sha256:a72b4433...` | Minimal Linux amd64 userland |
| Python runtime | Python `3.12.14` (`UV_PYTHON_INSTALL_DIR=/opt/python`) | Frozen `--all-extras --no-editable` environment |
| Native EDA / Simulators | `libsystemc-dev=3.0.1-1`, `verilator=5.032-1+b2`, `yosys=0.52-2`, `qemu-system-riscv=1:10.0.13+ds-0+deb13u1` | Real SystemC/TLM-2.0, Verilator RTL, synthesis, and QEMU `virt` smoke |
| Cross Toolchains | `gcc-riscv64-unknown-elf=14.2.0+19`, `gcc-riscv64-linux-gnu`, `openjdk-21-jdk-headless` | Bare-metal, Linux, and Chipyard/Chisel compilation |
| Packaging & Security | `tini`, `shellcheck=0.10.0-1`, `skopeo`, `USER 1000:1000`, `/usr/local/share/sdk-packages.tsv` | Rootless PID 1 reaping, OCI export, full package audit trail |

KDE Plasma (Konsole) and headless Linux workstations run the CLI directly in the terminal: **no X11/Wayland socket, privileged mode, host Docker/Podman socket, or KVM access is required.**

---

## 2. Build, Verify Offline & Export Locally

```bash
git switch dev
bash scripts/podman.sh build               # Builds strictly from committed git archive HEAD
bash scripts/podman.sh smoke               # Offline (--network=none) full native + 6x RTL + MCP smoke
bash scripts/podman.sh export dist/container
```

- **Strict Context Hygiene**: `build` refuses to run with uncommitted tracked changes and filters inputs through byte-identical [`.containerignore`](../.containerignore) and [`.dockerignore`](../.dockerignore) (verified by [`tests/test_container.py`](../tests/test_container.py)). Git history, private archives (`*.zip`), keys (`*.key`, `*.pem`), and host caches are never sent to the builder.
- **Custom Proxy CA (Optional)**: Set `USP_BUILD_CA_FILE=/path/to/ca-bundle.pem` to pass a build-time `--secret id=proxy_ca` via [`containers/with-trust.sh`](../containers/with-trust.sh); the certificate is never copied into image layers.

---

## 3. Running Workloads, QA, MCP & Compose

`scripts/podman.sh` executes with `--userns=keep-id --user "$(id -u):$(id -g)" --cap-drop=ALL --security-opt=no-new-privileges --pids-limit=2048` and isolates container-built binaries from host caches:

| Host Directory | Container Mount | Purpose |
|---|---|---|
| `<repo>` | `/workspace` | Checkout source tree (`PYTHONPATH=/workspace/src`) |
| `.podman/build` | `/workspace/build` | Generated models, firmware, and native C++/RTL builds |
| `.podman/tools` | `/workspace/.tools` | Pinned upstream checkouts and generator tools |
| `.podman/results` | `/workspace/results` | JSON/Markdown reports, VCD waveforms, HTML5 dashboards |
| `.podman/venv` | `/workspace/.venv` | Optional container-local Python venv |
| `.podman/cache` | `/cache` | `HOME`, `XDG_CACHE_HOME`, and `UV_CACHE_DIR` |

Override `USP_CONTAINER_STATE` to relocate `.podman/`. On SELinux hosts, `:Z` relabeling is applied by default (override via `USP_SELINUX_LABEL=z` for shared concurrent containers or empty to disable).

```bash
# Interactive SDK shell
bash scripts/podman.sh run

# Execute single commands directly
bash scripts/podman.sh run usp unified-soc run specs/multiplatform/unified_hetero_soc.yaml --out results/unified-soc
bash scripts/podman.sh run usp generate systems/AI_SOC/bringup --out build/AI_SOC

# Run full QA/QC scorecard (quick | unseen | multiplatform | wheel | full)
bash scripts/podman.sh qa full

# Start MCP JSON-RPC 2.0 stdio server (no PTY)
bash scripts/podman.sh mcp
```

### Podman / OCI Compose ([`containers/compose.yaml`](../containers/compose.yaml))

```bash
mkdir -p .podman/{build,tools,results,venv,cache}
podman compose -f containers/compose.yaml run --rm sdk
podman compose -f containers/compose.yaml run --rm qa
podman compose -f containers/compose.yaml run --rm unified-soc
podman compose -f containers/compose.yaml run --rm -T mcp
```

---

## 4. Loading a Published OCI Release

Install rootless Podman (`sudo dnf install podman` on Fedora; `sudo apt install podman uidmap slirp4netns fuse-overlayfs` on Debian/Ubuntu) and verify `podman info` as a normal user (never `sudo`):

```bash
sha256sum --check --ignore-missing SHA256SUMS
podman load --input socsim-sdk-0.3.0a5-r1-linux-amd64.oci.tar
bash scripts/podman.sh smoke
```

If an import tool renames the image tag, set `USP_CONTAINER_IMAGE` to the loaded tag or immutable image ID.

---

## 5. Native Source Builds Inside the SDK & Scope Boundaries

Full prebuilt gem5, Chipyard, OpenTitan, Zephyr, Linux, Yocto images, and OpenROAD/PDK layouts are **not** baked into the image. To fetch and build them inside the isolated SDK container (budget `>= 100 GiB` disk and `>= 32 GiB` RAM):

```bash
bash scripts/podman.sh run bash -c '
  bash scripts/setup_all.sh --all --fetch-only
  bash scripts/setup_chipyard.sh --tools-only
'
```

- **Docker Fallback**: On cloud hosts lacking nested user namespaces, `USP_OCI_ENGINE=docker` builds with the identical `Containerfile` and converts exports offline to a true OCI archive using bundled `skopeo`. Local Docker tests remain distinct from native rootless Podman acceptance in GitHub Actions.
- **OpenROAD Physical Runner**: Uses its separate Docker/OpenROAD contract; no host container socket is mounted inside this SDK.
