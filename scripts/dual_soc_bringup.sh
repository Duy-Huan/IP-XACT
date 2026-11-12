#!/usr/bin/env bash
# Explicitly scoped public-model bring-up; never full heterogeneous SoC signoff.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
export PATH="$repo/.venv/bin:$PATH"
upstream_root=${USP_UPSTREAM_ROOT:-$repo/.tools/upstream}
build_root=${USP_DUAL_BUILD_ROOT:-$repo/build}
models=${USP_IP_MODELS_DIR:-$repo/build/source-ip-models}
noc_build=${USP_NOC_BUILD_DIR:-$repo/build/garnet}
: "${CROSS_COMPILE:?Set the RISC-V unknown-elf compiler prefix}"
yosys -V | head -n 1
usp noc build --source-gem5 "$upstream_root/gem5" --out "$noc_build" --jobs "${USP_JOBS:-2}"
for soc in AI_SOC AIZ_SOC; do
    usp system validate "systems/$soc"
    usp system plan "systems/$soc" > "$build_root/$soc-architecture-plan.json"
    usp elaborate "systems/$soc/architecture" --out "$build_root/$soc-architecture-ir.json"
    generated="$build_root/$soc"
    if [[ ! -e "$generated" ]]; then
        usp generate "systems/$soc/bringup" --out "$generated"
    fi
    usp build "systems/$soc/bringup" --generated "$generated" \
        --source-qemu "$upstream_root/qemu" --jobs "${USP_JOBS:-2}"
    usp software build "systems/$soc/bringup" --os zephyr --generated "$generated" \
        --source-zephyr "$upstream_root/zephyr" --cross-compile "$CROSS_COMPILE" \
        --jobs "${USP_JOBS:-2}"
done
export USP_DUAL_BUILD_ROOT="$build_root" USP_NOC_BUILD_DIR="$noc_build"
export USP_IP_MODELS_DIR="$models" USP_REQUIRE_DUAL_SOC=1
python -m pytest tests/test_systems.py -v --junitxml=build/dual-soc-junit.xml
