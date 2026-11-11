#!/usr/bin/env bash
# Generate, compile, boot and verify the real custom machine + generated Zephyr BSP.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
export PATH="$repo/.venv/bin:$PATH"
upstream_root=${USP_UPSTREAM_ROOT:-$repo/.tools/upstream}
: "${CROSS_COMPILE:?Set CROSS_COMPILE to the RISC-V unknown-elf tool prefix}"
generated=${USP_GENERATED_DIR:-$repo/build/rv64-minimal}
if [[ ! -e "$generated" ]]; then
    usp generate examples/rv64-minimal --out "$generated"
fi
usp build examples/rv64-minimal --generated "$generated" \
    --source-qemu "$upstream_root/qemu" --jobs "${USP_JOBS:-4}"
usp software build examples/rv64-minimal --os zephyr --generated "$generated" \
    --source-zephyr "$upstream_root/zephyr" --cross-compile "$CROSS_COMPILE"
result_dir=$(mktemp -d "$repo/build/acceptance.XXXXXXXX")
usp test examples/rv64-minimal --generated "$generated" --out "$result_dir/run"
export USP_GENERATED_DIR="$generated" USP_REQUIRE_BRINGUP=1
python -m pytest tests/test_custom.py -v --junitxml=build/custom-junit.xml
