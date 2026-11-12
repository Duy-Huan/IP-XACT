#!/usr/bin/env bash
# Actual public RTL CPUs, native memory engines and real firmware acceptance.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
export PATH="$repo/.venv/bin:$PATH"
upstream_root=${USP_UPSTREAM_ROOT:-$repo/.tools/upstream}
wrapper=${USP_CHIPYARD_IBEX_SOURCE:-$upstream_root/chipyard-ibex}
core=${USP_CHIPYARD_IBEX_CORE:-$upstream_root/chipyard-ibex-core}
fetch_exact() {
    local source_dir=$1 public_url=$2 commit=$3
    if [[ ! -d "$source_dir/.git" ]]; then
        mkdir -p "$source_dir"
        git -C "$source_dir" init
        git -C "$source_dir" remote add origin "$public_url"
        git -C "$source_dir" fetch --depth 1 origin "$commit"
        git -C "$source_dir" checkout --detach FETCH_HEAD
    fi
    [[ $(git -C "$source_dir" rev-parse HEAD) == "$commit" ]]
    [[ -z $(git -C "$source_dir" status --porcelain --untracked-files=no) ]]
}
fetch_exact "$wrapper" https://github.com/ucb-bar/ibex-wrapper.git 1a030b1d20e0bfb3f8ca250549f4826f1d2b2c32
fetch_exact "$core" https://github.com/lowRISC/ibex.git 48f11c673348908c37d3eaa9bd1cd0a5b5c69253
export USP_RTL_CPU_IBEX_BUILD=${USP_RTL_CPU_IBEX_BUILD:-$repo/build/rtl-cpu-ibex}
export USP_RTL_CPU_CUSTOM_BUILD=${USP_RTL_CPU_CUSTOM_BUILD:-$repo/build/rtl-cpu-custom}
export USP_RTL_MASTER_BUILD=${USP_RTL_MASTER_BUILD:-$repo/build/rtl-master-gem5}
usp rtlcpu build --manifest models/cpus/chipyard-ibex.yaml --out "$USP_RTL_CPU_IBEX_BUILD" \
    --chipyard "$upstream_root/chipyard" --wrapper "$wrapper" --core "$core" --jobs "${USP_JOBS:-2}"
usp rtlcpu build --manifest models/cpus/custom-ibex-wb.yaml --out "$USP_RTL_CPU_CUSTOM_BUILD" \
    --chipyard "$upstream_root/chipyard" --wrapper "$wrapper" --core "$core" --jobs "${USP_JOBS:-2}"
usp rtlcpu build-engine --source "$upstream_root/gem5" --out "$USP_RTL_MASTER_BUILD" --jobs "${USP_JOBS:-2}"
export USP_REQUIRE_RTL_CPU=1
export USP_IP_GENERATED_DIR=${USP_IP_GENERATED_DIR:-$repo/build/rv64-ips}
export USP_IP_MODELS_DIR=${USP_IP_MODELS_DIR:-$repo/build/source-ip-models}
export USP_DUAL_BUILD_ROOT=${USP_DUAL_BUILD_ROOT:-$repo/build}
python -m pytest tests/test_rtl_cpu.py -v --junitxml=build/rtl-cpu-junit.xml
