#!/usr/bin/env bash
# Qualified Rocket JDK/SBT; keep Chipyard's independent GCD tool profile intact.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tools_dir=${USP_CHIPYARD_TOOLS:-$repo/.tools/chipyard}
rocket_tools=${USP_ROCKET_TOOLS:-$repo/.tools/rocket}
mkdir -p "$tools_dir" "$rocket_tools"
acquire() {
    local target=$1 url=$2 sha=$3
    if [[ ! -f "$target" ]]; then curl -fL --retry 2 --max-time 300 "$url" -o "$target"; fi
    printf '%s  %s\n' "$sha" "$target" | sha256sum --check
}
acquire "$tools_dir/sbt-launch-1.8.2.jar" \
    https://repo.maven.apache.org/maven2/org/scala-sbt/sbt-launch/1.8.2/sbt-launch-1.8.2.jar \
    b14490ad2ce5b7650bf2da813ca0f57d123318f819a1ce69207b8351057386a6
acquire "$rocket_tools/jdk17.tar.gz" \
    'https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.17%2B10/OpenJDK17U-jdk_x64_linux_hotspot_17.0.17_10.tar.gz' \
    992f96e7995075ac7636bb1a8de52b0c61d71ed3137fafc979ab96b4ab78dd75
if [[ ! -d "$rocket_tools/jdk-17.0.17+10" ]]; then
    tar -xzf "$rocket_tools/jdk17.tar.gz" -C "$rocket_tools"
fi
export USP_ROCKET_JAVA_HOME="$rocket_tools/jdk-17.0.17+10"
"$repo/.venv/bin/python" - <<'PY'
import os
from pathlib import Path
from socsim.rocket_build import verify_jdk
print('Rocket JDK verified:', verify_jdk(Path(os.environ['USP_ROCKET_JAVA_HOME'])))
PY
