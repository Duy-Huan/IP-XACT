#!/usr/bin/env bash
# Linux x86_64 generator tools, immutable Maven artifacts and content verification.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tools_dir=${USP_CHIPYARD_TOOLS:-$repo/.tools/chipyard}
mkdir -p "$tools_dir"
acquire() {
    local path=$1 url=$2 sha=$3
    if [[ ! -f "$path" ]]; then curl -fL --retry 2 --max-time 120 "$url" -o "$path"; fi
    printf '%s  %s\n' "$sha" "$path" | sha256sum --check
}
acquire "$tools_dir/sbt-launch-1.10.7.jar" \
    https://repo.maven.apache.org/maven2/org/scala-sbt/sbt-launch/1.10.7/sbt-launch-1.10.7.jar \
    3cca02818047327a83efde776103a1ef92f76f72c062badbbb062499a3270c07
acquire "$tools_dir/llvm-firtool-1.62.0-linux-x64.jar" \
    https://repo.maven.apache.org/maven2/org/chipsalliance/llvm-firtool/1.62.0/llvm-firtool-1.62.0-linux-x64.jar \
    2da2cd5274b9470e461a1f5e384d134b6da7b73ca7020cf21b371b097ff7cb01
python3 - "$tools_dir" <<'PY'
import sys, zipfile, hashlib
from pathlib import Path
root = Path(sys.argv[1])
with zipfile.ZipFile(root / 'llvm-firtool-1.62.0-linux-x64.jar') as archive:
    data = archive.read('org.chipsalliance/llvm-firtool/linux-x64/bin/firtool')
assert hashlib.sha256(data).hexdigest() == '0dd855e895e7763d4fe07f537c48631f8cbd79725a01256995ebcc1fcacb97aa'
(root / 'firtool').write_bytes(data)
(root / 'firtool').chmod(0o755)
PY
"$tools_dir/firtool" --version
