#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
export PATH="$repo/.venv/bin:$PATH"
upstream_root=${USP_UPSTREAM_ROOT:-$repo/.tools/upstream}
generated=${USP_GENERATED_DIR:-$repo/build/rv64-minimal}
build_dir=${USP_GEM5_BUILD_DIR:-$repo/build/gem5}
usp gem5 build --source-gem5 "$upstream_root/gem5" --out "$build_dir" --jobs "${USP_JOBS:-4}"
result_dir=$(mktemp -d "$repo/build/gem5-acceptance.XXXXXXXX")
usp gem5 run examples/rv64-minimal --generated "$generated" --build-dir "$build_dir" --out "$result_dir/run"
export USP_GENERATED_DIR="$generated" USP_GEM5_BUILD_DIR="$build_dir" USP_REQUIRE_GEM5=1
python -m pytest tests/test_gem5.py -v --junitxml=build/gem5-junit.xml
