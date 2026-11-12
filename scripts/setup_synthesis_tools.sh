#!/usr/bin/env bash
# Rootless Debian trixie amd64 synthesis tools, verified by existing signed APT indexes.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tools_dir=${SOCSIM_TOOLS_DIR:-$repo/.tools/host}
[[ -f "$tools_dir/env.sh" ]] || { echo 'Run setup_user_tools.sh first' >&2; exit 1; }
tools_dir=$(cd "$tools_dir" && pwd)
[[ "$(dpkg --print-architecture)" == amd64 ]] || exit 1
cache="$tools_dir/cache"
options=(-o "Dir::Etc::sourcelist=$cache/sources.list" -o Dir::Etc::sourceparts=-
    -o Dir::Etc::parts=- -o Dir::Etc::main=- -o "Dir::State::lists=$cache/lists"
    -o "Dir::Cache=$cache/archives" -o "APT::Sandbox::User=$(id -un)")
apt-get "${options[@]}" update
download_dir=$(mktemp -d "$cache/synthesis.XXXXXXXX")
trap 'rm -rf -- "$download_dir"' EXIT
cd "$download_dir"
apt-get "${options[@]}" download yosys=0.52-2 yosys-abc=0.52-2
sha256sum ./*.deb > "$tools_dir/synthesis-packages.sha256"
printf '%s\n' yosys=0.52-2 yosys-abc=0.52-2 > "$tools_dir/synthesis-packages.lock"
for package in ./*.deb; do dpkg-deb -x "$package" "$tools_dir"; done
source "$tools_dir/env.sh"
yosys -V
