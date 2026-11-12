#!/usr/bin/env bash
# Public pinned OpenROAD container, flattened to avoid VFS intermediate-layer duplication.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
export PATH="$repo/.venv/bin:$PATH"
out=${USP_OPENROAD_TOOLS:-$repo/build/openroad-tools}
mkdir -p "$out"
out=$(cd "$out" && pwd)
if [[ -f "$out/openroad-tools.json" ]]; then
    python - "$out" <<'PY'
import sys
from pathlib import Path
from socsim.asic import verify_toolchain
verify_toolchain(Path(sys.argv[1]))
print('Existing immutable OpenROAD toolchain verified')
PY
    exit 0
fi
command -v docker >/dev/null
docker_local() {
    env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY \
        -u DOCKER_CERT_PATH docker --host=unix:///var/run/docker.sock "$@"
}
docker_local info >/dev/null
scratch=$(mktemp -d "$out/acquire.XXXXXXXX")
trap 'rm -rf -- "$scratch"' EXIT
curl -fsSL --max-time 120 \
    https://github.com/google/go-containerregistry/releases/download/v0.20.2/go-containerregistry_Linux_x86_64.tar.gz \
    -o "$scratch/crane.tar.gz"
printf '%s  %s\n' c14340087103ba9dadf61d45acd20675490fd0ccbd56ac7901fc1b502137f44b \
    "$scratch/crane.tar.gz" | sha256sum -c -
tar -xzf "$scratch/crane.tar.gz" -C "$scratch" crane
source_image=$(python -c 'from socsim.asic import SOURCE_IMAGE; print(SOURCE_IMAGE)')
# The OCI registry and CA/proxy policy are inherited; no host network or privilege flags.
"$scratch/crane" export "$source_image" - 2> "$out/export.log" | \
    docker_local import --change 'WORKDIR /OpenROAD-flow-scripts' \
        --change 'CMD ["/bin/bash"]' - > "$out/flat-image-id.txt"
python - "$out" <<'PY'
import sys
from pathlib import Path
from socsim.asic import SOURCE_IMAGE, verify_toolchain
from socsim.runner import write_json
out = Path(sys.argv[1])
write_json(out / 'openroad-tools.json', {
    'source_image': SOURCE_IMAGE,
    'flat_image_id': (out / 'flat-image-id.txt').read_text().strip(),
    'packaging': 'verified upstream OCI export with crane v0.20.2, single-layer import',
})
verify_toolchain(out)
print('Immutable OpenROAD toolchain ready:', out)
PY
