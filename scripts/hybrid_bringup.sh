#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
export PATH="$repo/.venv/bin:$PATH"
upstream_root=${USP_UPSTREAM_ROOT:-$repo/.tools/upstream}
generated=${USP_HYBRID_GENERATED_DIR:-$repo/build/rv64-hybrid}
models=${USP_MODEL_DIR:-$repo/build/fifo-models}
: "${CROSS_COMPILE:?Set the RISC-V unknown-elf tool prefix}"
if [[ ! -e "$generated" ]]; then usp generate examples/rv64-hybrid --out "$generated"; fi
usp build examples/rv64-hybrid --generated "$generated" --source-qemu "$upstream_root/qemu" --jobs "${USP_JOBS:-4}"
usp software build examples/rv64-hybrid --os zephyr --generated "$generated" \
  --source-zephyr "$upstream_root/zephyr" --cross-compile "$CROSS_COMPILE"
usp models build --out "$models" --native-source "$repo/native/systemc" --source-opentitan "$upstream_root/opentitan"
export USP_MODEL_DIR="$models" USP_HYBRID_GENERATED_DIR="$generated" USP_REQUIRE_MODELS=1
python -m pytest tests/test_models.py -v --junitxml=build/hybrid-junit.xml
