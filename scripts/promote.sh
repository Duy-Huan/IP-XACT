#!/usr/bin/env bash
# Local promotion only. Publishing is a separate explicit operation.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ "$(git branch --show-current)" == dev ]] || { echo 'Run on dev' >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo 'Working tree must be clean' >&2; exit 1; }
candidate=$(git rev-parse HEAD)
export USP_REQUIRE_BRINGUP=1 USP_REQUIRE_LINUX=1 USP_REQUIRE_MODELS=1 USP_REQUIRE_GEM5=1 USP_REQUIRE_FREERTOS=1 USP_REQUIRE_IPS=1 USP_REQUIRE_WORKSPACE=1
export USP_REQUIRE_DUAL_SOC=1
export USP_REQUIRE_ASIC=1
export USP_REQUIRE_TESTCHIP=1
: "${USP_GENERATED_DIR:?Promotion requires the verified custom QEMU/Zephyr build directory}"
: "${USP_MODEL_DIR:?Promotion requires the TLM/RTL model build}"
: "${USP_HYBRID_GENERATED_DIR:?Promotion requires the generated hybrid machine}"
: "${USP_GEM5_BUILD_DIR:?Promotion requires the verified gem5 build}"
: "${USP_FREERTOS_SOURCE:?Promotion requires the pinned FreeRTOS kernel for negative guest tests}"
: "${USP_IP_MODELS_DIR:?Promotion requires the real Chipyard/OpenTitan source models}"
: "${USP_IP_GENERATED_DIR:?Promotion requires the source IP machine/Zephyr build}"
: "${USP_NOC_BUILD_DIR:?Promotion requires the verified Garnet standalone build}"
: "${USP_DUAL_BUILD_ROOT:?Promotion requires the AI_SOC and AIZ_SOC bring-up builds}"
: "${USP_ASIC_BUILD_DIR:?Promotion requires an actual verified OpenROAD reference layout}"
: "${USP_RTL_CHIP_ROOT:?Promotion requires both CPU-owned RTL chip builds}"
: "${USP_CHIP_PHYSICAL_ROOT:?Promotion requires both complete test-chip layouts}"
: "${USP_GATE_CHIP_ROOT:?Promotion requires both mapped test-chip executables}"
bash scripts/regression.sh
[[ "$(git rev-parse HEAD)" == "$candidate" && -z "$(git status --porcelain)" ]] || exit 1
if git show-ref --verify --quiet refs/heads/main; then
    git merge-base --is-ancestor main dev || { echo 'Reconcile main into dev first' >&2; exit 1; }
    git switch main
    git merge --no-ff dev -m 'Merge regression-verified dev into main'
else
    # An empty repository has no release ancestor. First promotion uses the tested commit.
    git branch main "$candidate"
fi
git switch dev
git merge --ff-only main
