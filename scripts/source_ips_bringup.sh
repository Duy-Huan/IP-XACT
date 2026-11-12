#!/usr/bin/env bash
# Generated SoC + real Chipyard Chisel GCD and OpenTitan TL-UL GPIO guest proof.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
export PATH="$repo/.venv/bin:$PATH"
upstream_root=${USP_UPSTREAM_ROOT:-$repo/.tools/upstream}
generated=${USP_IP_GENERATED_DIR:-$repo/build/rv64-ips}
models=${USP_IP_MODELS_DIR:-$repo/build/source-ip-models}
tools_dir=${USP_CHIPYARD_TOOLS:-$repo/.tools/chipyard}
scala_cache=${USP_SCALA_CACHE:-$repo/.cache/scala}
: "${CROSS_COMPILE:?Set the RISC-V unknown-elf compiler prefix}"
if [[ ! -e "$generated" ]]; then usp generate examples/rv64-ips --out "$generated"; fi
usp build examples/rv64-ips --generated "$generated" --source-qemu "$upstream_root/qemu" --jobs "${USP_JOBS:-2}"
usp software build examples/rv64-ips --os zephyr --generated "$generated" \
  --source-zephyr "$upstream_root/zephyr" --cross-compile "$CROSS_COMPILE" --jobs "${USP_JOBS:-2}"
usp ipblocks build --source-chipyard "$upstream_root/chipyard" --source-opentitan "$upstream_root/opentitan" \
  --tools "$tools_dir" --scala-cache "$scala_cache" --out "$models" --jobs "${USP_JOBS:-2}"
export USP_IP_MODELS_DIR="$models" USP_IP_GENERATED_DIR="$generated" USP_REQUIRE_IPS=1
python -m pytest tests/test_source_ips.py -v --junitxml=build/source-ips-junit.xml
