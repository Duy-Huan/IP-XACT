#!/usr/bin/env bash
# Source-built RTOS acceptance on actual RTL CPUs; outputs must be fresh.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PATH="$PWD/.venv/bin:$PATH"
upstream_root=${USP_UPSTREAM_ROOT:-$PWD/.tools/upstream}
fetch_exact() {
    local checkout=$1 public_url=$2 commit=$3
    if [[ ! -d "$checkout/.git" ]]; then
        mkdir -p "$checkout"
        git -C "$checkout" init
        git -C "$checkout" remote add origin "$public_url"
        git -C "$checkout" fetch --depth 1 origin "$commit"
        git -C "$checkout" checkout --detach FETCH_HEAD
    fi
    [[ $(git -C "$checkout" rev-parse HEAD) == "$commit" ]]
    [[ -z $(git -C "$checkout" status --porcelain --untracked-files=all) ]]
}
fetch_exact "$upstream_root/ucos2" https://github.com/SiliconLabs/uC-OS2.git bfc6120f20cb27a927a9fc5dd667f9e455495777
fetch_exact "$upstream_root/ucos3" https://github.com/SiliconLabs/uC-OS3.git 9a3fc5f45d75c0b4c3b6eaecf903720c74329747
fetch_exact "$upstream_root/uc-cpu" https://github.com/SiliconLabs/uC-CPU.git ae5796e2049b043f69ef4f29fc330f29217b6344
fetch_exact "$upstream_root/uc-lib" https://github.com/SiliconLabs/uC-LIB.git 71b5f65da27859028a89c4ceac1df53c880916dc
export USP_RTL_OS_ROOT=${USP_RTL_OS_ROOT:-$PWD/build/rtl-os}
export USP_UCOS_ROOT=${USP_UCOS_ROOT:-$PWD/build/ucos}
export USP_IP_GENERATED_DIR=${USP_IP_GENERATED_DIR:-$PWD/build/rv64-ips}
export USP_DUAL_BUILD_ROOT=${USP_DUAL_BUILD_ROOT:-$PWD/build}
: "${USP_FREERTOS_SOURCE:?Provide the pinned FreeRTOS source}"
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
    usp rtl-os build "$project" --generated "$generated" --source "$USP_FREERTOS_SOURCE" --out "$USP_RTL_OS_ROOT/$name"
    for kernel in ucos2 ucos3; do
        usp ucos build "$project" --generated "$generated" --upstream "$upstream_root" --kernel "$kernel" --out "$USP_UCOS_ROOT/$kernel/$name"
    done
done
for case in assert-failure tick-disabled; do
    usp rtl-os build examples/rv64-ips --generated "$USP_IP_GENERATED_DIR" --source "$USP_FREERTOS_SOURCE" --case "$case" --out "$USP_RTL_OS_ROOT/$case"
    for kernel in ucos2 ucos3; do
        usp ucos build examples/rv64-ips --generated "$USP_IP_GENERATED_DIR" --upstream "$upstream_root" --kernel "$kernel" --case "$case" --out "$USP_UCOS_ROOT/$kernel/$case"
    done
done
export USP_REQUIRE_RTL_OS=1 USP_REQUIRE_UCOS=1
python -m pytest tests/test_rtl_os.py tests/test_ucos.py -v --junitxml=build/rtl-os-junit.xml
