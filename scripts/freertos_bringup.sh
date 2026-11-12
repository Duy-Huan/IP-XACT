#!/usr/bin/env bash
# Real FreeRTOS guest, then actual failure and missing-tick controls.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
export PATH="$repo/.venv/bin:$PATH"
upstream_root=${USP_UPSTREAM_ROOT:-$repo/.tools/upstream}
generated=${USP_GENERATED_DIR:-$repo/build/rv64-minimal}
: "${CROSS_COMPILE:?Set CROSS_COMPILE to the RISC-V unknown-elf tool prefix}"
usp software build examples/rv64-minimal --os freertos --generated "$generated" \
    --source-freertos "$upstream_root/freertos" --cross-compile "$CROSS_COMPILE"
result_dir=$(mktemp -d "$repo/build/freertos-acceptance.XXXXXXXX")
usp test examples/rv64-minimal --suite freertos-boot --generated "$generated" --out "$result_dir/run"
export USP_GENERATED_DIR="$generated" USP_REQUIRE_FREERTOS=1
export USP_FREERTOS_SOURCE="$upstream_root/freertos"
python -m pytest tests/test_freertos.py -v --junitxml=build/freertos-junit.xml
