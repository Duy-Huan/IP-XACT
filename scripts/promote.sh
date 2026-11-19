#!/usr/bin/env bash
# Local promotion only. Publishing is a separate explicit operation.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ "$(git branch --show-current)" == dev ]] || { echo 'Run on dev' >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo 'Working tree must be clean' >&2; exit 1; }
candidate=$(git rev-parse HEAD)
bash scripts/ci_lint.sh
export USP_REQUIRE_BRINGUP=1 USP_REQUIRE_LINUX=1 USP_REQUIRE_MODELS=1 USP_REQUIRE_GEM5=1 USP_REQUIRE_FREERTOS=1 USP_REQUIRE_IPS=1 USP_REQUIRE_WORKSPACE=1
export USP_REQUIRE_DUAL_SOC=1
export USP_REQUIRE_ASIC=1
export USP_REQUIRE_TESTCHIP=1
export USP_REQUIRE_UNIFIED=1
export USP_REQUIRE_SW_LAYERS=1
export USP_REQUIRE_RTL_CPU=1
export USP_REQUIRE_ROCKET=1
: "${USP_ROCKET_ROOT:?Promotion requires all three actual Rocket CPU builds}"
: "${USP_ROCKET_SOFTWARE_ROOT:?Promotion requires source-built Rocket kernels and MMU firmware}"
: "${USP_ROCKET_ZEPHYR_ROOT:?Promotion requires source-built Rocket Zephyr BSPs}"
export USP_REQUIRE_RTL_OS=1 USP_REQUIRE_UCOS=1
export USP_REQUIRE_RTL_ZEPHYR=1
export USP_REQUIRE_LIVE_GARNET=1
export USP_REQUIRE_COHERENT=1
export USP_REQUIRE_RTL_PLATFORM=1
export USP_REQUIRE_NATIVE_SOC=1
export USP_REQUIRE_NATIVE_SMP=1
export USP_REQUIRE_NATIVE_EXTERNAL=1
export USP_REQUIRE_NATIVE_IPXACT=1
export USP_REQUIRE_NATIVE_FLOW=1
export USP_REQUIRE_NATIVE_FABRIC=1
export USP_REQUIRE_BUS_XML=1
: "${USP_BUS_XML_AI_SOC:?Promotion requires standard bus XML AI_SOC execution}"
: "${USP_BUS_XML_AIZ_SOC:?Promotion requires standard bus XML AIZ_SOC execution}"
: "${USP_NATIVE_FABRIC_AI_SOC:?Promotion requires the translated AI_SOC HW/SW flow}"
: "${USP_NATIVE_FABRIC_AIZ_SOC:?Promotion requires the translated AIZ_SOC HW/SW flow}"
: "${USP_NATIVE_FLOW_SMP_PROFILE:?Promotion requires actual multi-hart QEMU/gem5 counters}"
: "${USP_NATIVE_FLOW_AI_SOC:?Promotion requires source-package HW/SW flow on both engines}"
: "${USP_NATIVE_FLOW_AIZ_SOC:?Promotion requires native BSP flow on the other mapped SoC}"
: "${USP_NATIVE_FLOW_GEM5_ONLY:?Promotion requires gem5-only execution without QEMU build}"
: "${USP_NATIVE_AI_SOC_IPXACT_BUILD:?Promotion requires direct IP-XACT AI_SOC execution}"
: "${USP_NATIVE_AIZ_SOC_IPXACT_BUILD:?Promotion requires direct IP-XACT AIZ_SOC execution}"
: "${USP_NATIVE_HIERARCHY_IPXACT_BUILD:?Promotion requires direct hierarchical IP-XACT execution}"
: "${USP_NATIVE_MAX_HART_BUILD:?Promotion requires the maximum-hart native boundary build}"
: "${USP_NATIVE_EXTERNAL_ROOT:?Promotion requires the compiled external SystemC model and user driver}"
: "${USP_NATIVE_AI_SOC_SMP_BUILD:?Promotion requires the native multi-hart AI_SOC and Linux build}"
: "${USP_NATIVE_AIZ_SOC_SMP_BUILD:?Promotion requires the native multi-hart AIZ_SOC and Linux build}"
: "${USP_NATIVE_AI_SOC_BUILD:?Promotion requires the generated native AI_SOC build}"
: "${USP_NATIVE_AIZ_SOC_BUILD:?Promotion requires the generated native AIZ_SOC build}"
: "${USP_NATIVE_GEM5_BUILD:?Promotion requires the built native multi-instance gem5 engine}"
: "${USP_COHERENT_BUILD:?Promotion requires the verified native two-hart coherent DMA engine}"
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
: "${USP_UNIFIED_GEM5_BUILD:?Promotion requires the actual gem5/SystemC full-system bridge}"
: "${USP_COUNTER_BUILD_DIR:?Promotion requires the source-built QEMU event counter}"
: "${USP_RTL_CPU_IBEX_BUILD:?Promotion requires the actual pinned Chipyard Ibex CPU}"
: "${USP_RTL_CPU_CUSTOM_BUILD:?Promotion requires the actual custom RTL CPU}"
: "${USP_RTL_MASTER_BUILD:?Promotion requires the actual gem5 RTL bus master}"
: "${USP_RTL_OS_ROOT:?Promotion requires all actual RV32 FreeRTOS conformance builds}"
: "${USP_UCOS_ROOT:?Promotion requires both actual µC/OS-II and µC/OS-III conformance builds}"
: "${USP_RTL_ZEPHYR_ROOT:?Promotion requires actual RV32 Zephyr conformance builds}"
: "${USP_GEM5_SOURCE:?Promotion requires the pinned Ruby/Garnet configuration sources}"
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
"$PWD/.venv/bin/python" -m socsim.release receipt --candidate "$candidate" --junit build/junit.xml \
    --out "${USP_PROMOTION_RECEIPT:-build/promotion-receipt.json}"
