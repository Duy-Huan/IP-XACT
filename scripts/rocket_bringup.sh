#!/usr/bin/env bash
# Native source-backed Rocket CPU, AXI target, Sv39 and kernel acceptance.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
export PATH="$repo/.venv/bin:$PATH"
upstream_root=${USP_UPSTREAM_ROOT:-$repo/.tools/upstream}
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
    [[ -z $(git -C "$checkout" status --porcelain --untracked-files=no) ]]
}
fetch_exact "$upstream_root/rocket-chip" https://github.com/chipsalliance/rocket-chip.git 72690b07c0939a58670418b97e5442835b2049b5
fetch_exact "$upstream_root/rocket-diplomacy" https://github.com/chipsalliance/diplomacy.git 6b7dc988a771ff21237b102fd02a1f2d2c2e7b72
fetch_exact "$upstream_root/rocket-hardfloat" https://github.com/ucb-bar/berkeley-hardfloat.git 4225367ed282aadd70f4cbd131f6959f4d00c502
fetch_exact "$upstream_root/rocket-cde" https://github.com/chipsalliance/cde.git 2bcaeae2b9914bd25497ce3c6fa62dc5ca80e09f
bash scripts/setup_rocket_tools.sh
export USP_ROCKET_ROOT=${USP_ROCKET_ROOT:-$repo/build/rocket-cpu}
export USP_ROCKET_SOFTWARE_ROOT=${USP_ROCKET_SOFTWARE_ROOT:-$repo/build/rocket-software}
export USP_ROCKET_ZEPHYR_ROOT=${USP_ROCKET_ZEPHYR_ROOT:-$repo/build/rocket-zephyr}
export USP_IP_GENERATED_DIR=${USP_IP_GENERATED_DIR:-$repo/build/rv64-ips}
export USP_DUAL_BUILD_ROOT=${USP_DUAL_BUILD_ROOT:-$repo/build}
export USP_IP_MODELS_DIR=${USP_IP_MODELS_DIR:-$repo/build/source-ip-models}
: "${USP_RTL_MASTER_BUILD:?Provide the verified gem5 external memory master}"
for name in rv64-ips AI_SOC AIZ_SOC; do
    if [[ "$name" == rv64-ips ]]; then
        project=examples/rv64-ips
        generated=$USP_IP_GENERATED_DIR
    else
        project="systems/$name/bringup"
        generated="$USP_DUAL_BUILD_ROOT/$name"
    fi
    usp rocket build "$project" --out "$USP_ROCKET_ROOT/$name" --upstream "$upstream_root" \
        --tools "${USP_CHIPYARD_TOOLS:-$repo/.tools/chipyard}" \
        --java-home "${USP_ROCKET_JAVA_HOME:-$repo/.tools/rocket/jdk-17.0.17+10}" \
        --scala-cache "${USP_SCALA_CACHE:-$repo/.cache/scala}" --jobs "${USP_JOBS:-2}"
    python - "$project" "$generated" "$name" "$upstream_root" <<'PY'
import os, sys
from pathlib import Path
from socsim.project import load_project
from socsim.rocket_software import build_freertos, build_mmu
from socsim.rocket_zephyr import build as build_zephyr
project = load_project(Path(sys.argv[1]))
generated = Path(sys.argv[2]).resolve()
name, upstream = sys.argv[3], Path(sys.argv[4]).resolve()
cpu = Path(os.environ['USP_ROCKET_ROOT']) / name
out = Path(os.environ['USP_ROCKET_SOFTWARE_ROOT']) / name
build_mmu(project, cpu, out / 'mmu')
for case in ('smoke', 'assert-failure', 'tick-disabled'):
    build_freertos(project, cpu, generated, upstream / 'freertos', out / ('freertos-' + case), case)
for case in ('smoke', 'tick-disabled'):
    build_zephyr(project, cpu, generated, upstream / 'zephyr',
                 Path(os.environ['USP_ROCKET_ZEPHYR_ROOT']) / name / case, case)
PY
done
export USP_REQUIRE_ROCKET=1
python -m pytest tests/test_rocket_build.py tests/test_rocket_elaboration.py tests/test_rocket_cpu.py \
    -v --junitxml=build/rocket-junit.xml
