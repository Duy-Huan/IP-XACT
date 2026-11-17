#!/usr/bin/env bash
# Offline native smoke: source parsing/generation, real guest success/failure/timeout.
set -euo pipefail
cd /opt/socsim
out=$(mktemp -d /tmp/socsim-container-smoke.XXXXXXXX)
usp validate examples/rv64-ips > "$out/ipxact.json"
usp generate systems/AI_SOC/bringup --out "$out/AI_SOC" > "$out/AI_SOC-generation.json"
usp generate systems/AIZ_SOC/bringup --out "$out/AIZ_SOC" > "$out/AIZ_SOC-generation.json"
for name in AI_SOC AIZ_SOC; do
    usp soc generate "specs/$name-native.yaml" --engine both --out "$out/$name-native" \
        > "$out/$name-native-generation.json"
done
SOCSIM_REQUIRE_INTEGRATION=1 python -m pytest tests/test_qemu.py -q \
    -o cache_dir="$out/pytest-cache" --basetemp="$out/tests" --junitxml="$out/junit.xml"
printf 'SDK native smoke passed; source revision: '
cat /usr/local/share/socsim-revision
