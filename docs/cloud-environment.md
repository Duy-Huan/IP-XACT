# Cloud setup and startup

The current workspace uses `/workspace/IP-XACT` and a rootless tools prefix at
`/workspace/.local/socsim-tools`. These local paths are not a requirement for other
hosts. Commands below were exercised here; no simulator service needs to remain
running across tasks.

## Reproduce or refresh installation

```bash
set -euo pipefail
cd /workspace/IP-XACT
bash scripts/setup.sh
export SOCSIM_TOOLS_DIR=/workspace/.local/socsim-tools
if [[ ! -f "$SOCSIM_TOOLS_DIR/env.sh" ]]; then
    bash scripts/setup_user_tools.sh
fi
source "$SOCSIM_TOOLS_DIR/env.sh"
source .venv/bin/activate
socsim doctor
bash scripts/regression.sh
```

The host-specific tools script requires Debian trixie amd64 and its installed
Debian archive keyring. It does not install packages as root or alter upstream
source. On other hosts, use a reviewed compatible toolchain and the pinned QEMU
profile instead. Do not bypass signature/TLS/checksum checks when resolving access.

## Start a new task

Read agent.md, docs/roadmap.md and skills/setup/SKILL.md. Use the existing isolated
checkout; do not create a worktree unless requested. Preserve current work and
develop on dev. Activate both environments:

```bash
cd /workspace/IP-XACT
source /workspace/.local/socsim-tools/env.sh
source .venv/bin/activate
socsim doctor
socsim plan examples/riscv-virt
```

Build the reference firmware with `bash scripts/build_firmware.sh`; execute it
with `socsim run examples/riscv-virt --firmware build/firmware/boot.elf --out
results/<new-id>`. Keep proprietary inputs and run bundles local.

The environment draft API returned INVALID_ARGUMENT when attempting to save the
install/start fields in this session. Persistence and snapshot publication are
unconfirmed. This document retains the tested instructions in Git; it does not
claim they were registered in cloud settings or tested in a fresh cloud task.

## Current native development startup

The earlier minimal script supports the legacy smoke only. For the master-spec
profiles, use the frozen bringup/model extras, the user-prefix development headers
and libraries, and the explicit public source cache:

```bash
cd /workspace/IP-XACT
uv sync --frozen --extra dev --extra bringup --extra models
source .venv/bin/activate
export SOCSIM_TOOLS_DIR=/workspace/.local/socsim-tools
# Run these installers only if refreshing an absent/incompatible user-prefix toolchain.
bash scripts/setup_user_tools.sh
USP_INSTALL_MODELS=1 bash scripts/setup_bringup_tools.sh
source "$SOCSIM_TOOLS_DIR/env_bringup.sh"
export USP_UPSTREAM_ROOT=/workspace/.cache/usp-upstream
USP_FETCH_LINUX=1 USP_FETCH_MODELS=1 bash scripts/fetch_upstreams.sh
export CROSS_COMPILE="$SOCSIM_TOOLS_DIR/usr/bin/riscv64-unknown-elf-"
export CROSS_COMPILE_LINUX="$SOCSIM_TOOLS_DIR/usr/bin/riscv64-linux-gnu-"
bash scripts/bringup.sh
bash scripts/linux_bringup.sh
bash scripts/hybrid_bringup.sh
bash scripts/gem5_bringup.sh
```

After a prepared environment is restored, only activate `.venv` and
`env_bringup.sh`, then select the needed commands. Native build directories retain
valid outputs; installed dependencies do not imply processes are running. QEMU and
bridge processes start per test and exit/are cleaned up. Use the existing checkout
on dev, no unsolicited worktrees. Yocto image acquisition remains blocked until
its required destinations are permitted; no runtime secret is required for those
public downloads. Draft script/network persistence and fresh cloud snapshot restore
are still separate from current-instance validation.
