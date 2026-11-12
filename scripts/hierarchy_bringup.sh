#!/usr/bin/env bash
# Structural designConfiguration -> generated machine -> real Zephyr boot/IRQ.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
export PATH="$repo/.venv/bin:$PATH"
upstream_root=${USP_UPSTREAM_ROOT:-$repo/.tools/upstream}
generated="$repo/build/rv64-hierarchy"
: "${CROSS_COMPILE:?Set the RISC-V unknown-elf compiler prefix}"
if [[ ! -e "$generated" ]]; then usp generate examples/rv64-hierarchy --out "$generated"; fi
usp build examples/rv64-hierarchy --generated "$generated" --source-qemu "$upstream_root/qemu" --jobs "${USP_JOBS:-2}"
usp software build examples/rv64-hierarchy --os zephyr --generated "$generated" \
  --source-zephyr "$upstream_root/zephyr" --cross-compile "$CROSS_COMPILE" --jobs "${USP_JOBS:-2}"
result_dir=$(mktemp -d "$repo/build/hierarchy-acceptance.XXXXXXXX")
usp test examples/rv64-hierarchy --generated "$generated" --out "$result_dir/run"
