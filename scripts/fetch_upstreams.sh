#!/usr/bin/env bash
# Explicit dependency acquisition. No network downloads occur in generate/run.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
upstream_root=${USP_UPSTREAM_ROOT:-$repo/.tools/upstream}
mkdir -p "$upstream_root"
fetch() {
    local name=$1 url=$2 tag=$3 sha=$4
    if [[ ! -e "$upstream_root/$name" ]]; then
        git clone --depth 1 --branch "$tag" "$url" "$upstream_root/$name"
    fi
    [[ "$(git -C "$upstream_root/$name" rev-parse HEAD)" == "$sha" ]] || {
        echo "Revision mismatch for $name" >&2; exit 1;
    }
    [[ -z "$(git -C "$upstream_root/$name" status --porcelain)" ]] || {
        echo "Preserve user changes: upstream $name is dirty" >&2; exit 1;
    }
}
fetch qemu https://github.com/qemu/qemu.git v10.0.0 7c949c53e936aa3a658d84ab53bae5cadaa5d59c
fetch zephyr https://github.com/zephyrproject-rtos/zephyr.git v4.2.0 413b789deb391d3a37d06b463288a5fe765ee57e
if [[ "${USP_FETCH_LINUX:-0}" == 1 ]]; then
    fetch linux https://github.com/torvalds/linux.git v6.12 adc218676eef25575469234709c2d87185ca223a
    fetch opensbi https://github.com/riscv-software-src/opensbi.git v1.6 bd613dd92113f683052acfb23d9dc8ba60029e0a
fi

if [[ "${USP_FETCH_MODELS:-0}" == 1 ]]; then
    fetch opentitan https://github.com/lowRISC/opentitan.git earlgrey_silver_release_v5 ed044fc9760bdf9fc075d0015ba1db07fa075355
    fetch gem5 https://github.com/gem5/gem5.git v24.1.0.3 b9da2bfe1e21f6efa2730edd4e80e58c5d965458
fi
