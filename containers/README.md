# Container SDK Runtime & Packaging (`containers/`)

| Metadata Field | Value |
|---|---|
| **Document ID** | `SOCSIM-CNT-001` |
| **Current Version** | `0.3.0a5-r1` |
| **Status** | Active / CI-Verified (`Podman SDK and OCI release`) |
| **Normative Authority** | `Containerfile`, `scripts/podman.sh`, `tests/test_container.py`, `.github/workflows/podman-sdk.yml` |

---

## Document Revision History

| Version | Release Date | Milestone / Gate | Author / Scope | Summary of Changes |
|---|---|---|---|---|
| `0.2.0-r1` | `2026-11-18` | `container-v0.2.0-r1` | Container SDK | Initial rootless Podman SDK (`Containerfile`, `entrypoint.sh`, `with-trust.sh`, offline `smoke.sh`). |
| `0.3.0a5-r1` | `2026-11-23` | `E1–E12` / Refactor | Container SDK | Expanded `smoke.sh` to Dual-Hart SMP + 6× Verilator RTL + CHIA loop + IP scaffold + HTML5 dashboard + 20-tool MCP; added `compose.yaml` and standardized directory index. |

---

## Directory Manifest

| File | Role | Execution Context |
|---|---|---|
| [`../Containerfile`](../Containerfile) | Multi-stage OCI image definition (`uv:0.13.5` + `debian:trixie-slim`, digest-pinned) | Build-time (`scripts/podman.sh build`) |
| [`../.containerignore`](../.containerignore) / [`../.dockerignore`](../.dockerignore) | Byte-identical strict build-context allowlist (blocks `.git`, secrets, `.zip`, host caches) | Build-time (verified by `tests/test_container.py`) |
| [`entrypoint.sh`](entrypoint.sh) | Minimal POSIX entrypoint: sets `umask 022`, ensures writable cache dirs, `exec "$@"` under `tini` | Runtime PID 1 child |
| [`with-trust.sh`](with-trust.sh) | Optional `/run/secrets/proxy_ca` TLS trust injection during build without baking secrets into image layers | Build-time `RUN --mount=type=secret` |
| [`smoke.sh`](smoke.sh) | Offline (`--network=none`) native acceptance script verifying QEMU, SystemC 3.0.1, Verilator 5.032, Dual-Hart SMP, 6× RTL, CHIA loop, IP scaffold, Dashboard, MCP, and pytest | `scripts/podman.sh smoke` & CI |
| [`compose.yaml`](compose.yaml) | Standard Podman/OCI Compose service definitions (`sdk`, `mcp`, `qa`, `unified-soc`) | Developer workstation (`podman compose`) |

---

## Security & Isolation Guarantees

1. **Committed-Source Build Only**: `scripts/podman.sh build` runs `git archive HEAD` into an isolated temp directory and refuses to build if tracked files have uncommitted edits.
2. **Rootless Least-Privilege Execution**: Runs as caller UID/GID (`--userns=keep-id` on Podman), `--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--pids-limit=2048`.
3. **Separated Native Caches**: Host `.podman/{build,tools,results,venv,cache}` directories shadow container `/workspace/{build,.tools,results,.venv}` and `/cache` so container ABI artifacts never collide with host binaries.
4. **Offline Smoke Verification**: `scripts/podman.sh smoke` executes with `--network=none` and zero host workspace mounts against `/opt/socsim` baked inside the image.

---

## Quick Commands

```bash
# Build, verify offline, and export OCI archive with SHA256SUMS
bash scripts/podman.sh build
bash scripts/podman.sh smoke
bash scripts/podman.sh export dist/container

# Launch interactive SDK shell, full QA suite, or MCP stdio server
bash scripts/podman.sh run
bash scripts/podman.sh qa full
bash scripts/podman.sh mcp

# Or via Podman Compose
mkdir -p .podman/{build,tools,results,venv,cache}
podman compose -f containers/compose.yaml run --rm unified-soc
```

For full workstation setup, KDE Plasma tips, SELinux labels, and OCI release loading instructions, see [`docs/podman.md`](../docs/podman.md).
