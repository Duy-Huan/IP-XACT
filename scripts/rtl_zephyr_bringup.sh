#!/usr/bin/env bash
# Source-built M-mode Zephyr, actual RTL instruction ownership, both engines.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PATH="$PWD/.venv/bin:$PATH"
upstream_root=${USP_UPSTREAM_ROOT:-$PWD/.tools/upstream}
export USP_RTL_ZEPHYR_ROOT=${USP_RTL_ZEPHYR_ROOT:-$PWD/build/rtl-zephyr}
export USP_IP_GENERATED_DIR=${USP_IP_GENERATED_DIR:-$PWD/build/rv64-ips}
export USP_DUAL_BUILD_ROOT=${USP_DUAL_BUILD_ROOT:-$PWD/build}
: "${USP_IP_MODELS_DIR:?Provide verified source-IP RTL models}"
: "${USP_RTL_CPU_IBEX_BUILD:?Provide the verified Chipyard Ibex executable}"
: "${USP_RTL_CPU_CUSTOM_BUILD:?Provide the verified custom RTL executable}"
: "${USP_RTL_MASTER_BUILD:?Provide the verified gem5 external bus master}"
for name in rv64-ips AI_SOC AIZ_SOC; do
    if [[ "$name" == rv64-ips ]]; then
        project=examples/rv64-ips
        generated=$USP_IP_GENERATED_DIR
    else
        project="systems/$name/bringup"
        generated="$USP_DUAL_BUILD_ROOT/$name"
    fi
    usp rtl-zephyr build "$project" --generated "$generated" --source "$upstream_root/zephyr" --out "$USP_RTL_ZEPHYR_ROOT/$name"
done
usp rtl-zephyr build examples/rv64-ips --generated "$USP_IP_GENERATED_DIR" --source "$upstream_root/zephyr" --case tick-disabled --out "$USP_RTL_ZEPHYR_ROOT/tick-disabled"
export USP_REQUIRE_RTL_ZEPHYR=1
python -m pytest tests/test_rtl_zephyr.py -v --junitxml=build/rtl-zephyr-junit.xml
