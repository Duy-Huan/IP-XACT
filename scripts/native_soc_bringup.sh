#!/usr/bin/env bash
# Actual native generation/build/run; no implicit downloads or fallback board.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PATH="$PWD/.venv/bin:$PATH"
native_root="${USP_NATIVE_BUILD_ROOT:-$PWD/build/native-soc}"
native_results="${USP_NATIVE_RESULTS_ROOT:-$PWD/results/native-soc}"
: "${USP_IP_MODELS_DIR:?Set the verified Chipyard/OpenTitan model build}"
native_gem5="${USP_NATIVE_GEM5_BUILD:-$native_root/gem5}"
if [[ ! -e "$native_gem5/native-build.json" ]]; then
    usp soc gem5-build --source-gem5 "${USP_GEM5_SOURCE:-$PWD/.tools/upstream/gem5}" \
        --out "$native_gem5" --jobs "${USP_JOBS:-2}"
fi
for native_name in AI_SOC AIZ_SOC; do
    usp soc generate "specs/$native_name-native.yaml" --out "$native_root/$native_name"
    usp soc build --generated "$native_root/$native_name" \
        --source-qemu "${USP_QEMU_SOURCE:-$PWD/.tools/upstream/qemu}" \
        --cross-compile "${CROSS_COMPILE:-riscv64-unknown-elf-}" --jobs "${USP_JOBS:-2}"
    usp soc accept --generated "$native_root/$native_name" --models "$USP_IP_MODELS_DIR" \
        --gem5-build "$native_gem5" \
        --out "$native_results/$native_name"
    usp soc verify "$native_results/$native_name"
done
USP_REQUIRE_NATIVE_SOC=1 USP_NATIVE_AI_SOC_BUILD="$native_root/AI_SOC" \
USP_NATIVE_AIZ_SOC_BUILD="$native_root/AIZ_SOC" \
USP_NATIVE_GEM5_BUILD="$native_gem5" \
python -m pytest tests/test_native_soc.py -v --junitxml="$native_root/junit.xml"
