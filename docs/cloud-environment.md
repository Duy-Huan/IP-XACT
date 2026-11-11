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
