#!/usr/bin/env bash
# Complete functional RTL test-chip profiles; not the larger 46/37-instance targets.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
export PATH="$repo/.venv/bin:$PATH"
upstream_root=${USP_UPSTREAM_ROOT:-$repo/.tools/upstream}
models=${USP_IP_MODELS_DIR:-$repo/build/source-ip-models}
tools=${USP_OPENROAD_TOOLS:-$repo/build/openroad-tools}
rtl_root=${USP_RTL_CHIP_ROOT:-$repo/build/rtl-chips}
physical_root=${USP_CHIP_PHYSICAL_ROOT:-$repo/build/rtl-physical}
gate_root=${USP_GATE_CHIP_ROOT:-$repo/build/rtl-gates}
results_root=${USP_CHIP_RESULTS:-$repo/results/rtl-testchips}
[[ ! -e "$results_root" ]] || { echo 'Choose a new USP_CHIP_RESULTS directory' >&2; exit 1; }
mkdir -p "$rtl_root" "$physical_root" "$gate_root" "$results_root"
refresh=()
if [[ "${USP_REBUILD_CHIPS:-0}" == 1 ]]; then refresh+=(--rebuild); fi
for soc in AI_SOC AIZ_SOC; do
    project="systems/$soc/rtl-testchip"
    usp chip validate "$project"
    if [[ ! -e "$rtl_root/$soc/chip-build.json" || ${#refresh[@]} -gt 0 ]]; then
        usp chip build "$project" --out "$rtl_root/$soc" --source-opentitan "$upstream_root/opentitan" \
            --models "$models" --cross-compile "${CROSS_COMPILE:-riscv64-unknown-elf-}" \
            --jobs "${USP_JOBS:-2}" "${refresh[@]}"
    else
        usp chip verify "$rtl_root/$soc"
    fi
    if [[ ! -e "$physical_root/$soc" ||
          ( ! -f "$physical_root/$soc/report.json" && "${USP_RESUME_ASIC:-0}" == 1 ) ]]; then
        resume=()
        if [[ -e "$physical_root/$soc" ]]; then resume+=(--resume); fi
        usp asic build --toolchain "$tools" --rtl "$rtl_root/$soc/consolidated.sv" \
            --top "$soc" --sdc "$rtl_root/$soc/chip.sdc" --frontend slang \
            --memory-limit-bits 16384 --out "$physical_root/$soc" \
            --timeout "${USP_ASIC_TIMEOUT:-7200}" "${resume[@]}"
    else
        usp asic verify "$physical_root/$soc"
    fi
    if [[ ! -e "$gate_root/$soc/gate-build.json" || ${#refresh[@]} -gt 0 ]]; then
        usp chip gate-build --generated "$rtl_root/$soc" --physical "$physical_root/$soc" \
            --out "$gate_root/$soc" --jobs "${USP_JOBS:-2}" "${refresh[@]}"
    fi
    usp chip accept --generated "$rtl_root/$soc" --physical "$physical_root/$soc" \
        --gate "$gate_root/$soc" --out "$results_root/$soc"
    usp chip verify-flow "$results_root/$soc"
done
export USP_RTL_CHIP_ROOT="$rtl_root" USP_CHIP_PHYSICAL_ROOT="$physical_root" USP_GATE_CHIP_ROOT="$gate_root"
USP_REQUIRE_TESTCHIP=1 python -m pytest tests/test_testchips.py -v --junitxml=build/rtl-testchips-junit.xml
