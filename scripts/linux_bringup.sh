#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
export PATH="$repo/.venv/bin:$PATH"
upstream_root=${USP_UPSTREAM_ROOT:-$repo/.tools/upstream}
generated=${USP_GENERATED_DIR:-$repo/build/rv64-minimal}
: "${CROSS_COMPILE_LINUX:?Set CROSS_COMPILE_LINUX to the RISC-V Linux GNU tool prefix}"
usp software build examples/rv64-minimal --os linux --generated "$generated" \
    --source-linux "$upstream_root/linux" --source-opensbi "$upstream_root/opensbi" \
    --cross-compile "$CROSS_COMPILE_LINUX"
result_dir=$(mktemp -d "$repo/build/linux-acceptance.XXXXXXXX")
usp test examples/rv64-minimal --suite linux-boot --generated "$generated" --out "$result_dir/run"
export USP_GENERATED_DIR="$generated" USP_REQUIRE_LINUX=1
python -m pytest tests/test_custom.py -v --junitxml=build/linux-junit.xml
