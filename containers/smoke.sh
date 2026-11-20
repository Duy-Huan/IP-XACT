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
usp qa run --suite unseen --root /opt/socsim --out "$out/qa-unseen" > "$out/qa-unseen.json"
usp qa scorecard "$out/qa-unseen" > "$out/qa-scorecard.json"
usp package build specs/multiplatform/packages/mac_npu/package.yaml --out "$out/mac-npu-build" \
    > "$out/mac-npu-build.json"
usp qa run --suite multiplatform --root /opt/socsim --out "$out/qa-multiplatform" \
    > "$out/qa-multiplatform.json"
usp qa scorecard "$out/qa-multiplatform" > "$out/qa-multiplatform-scorecard.json"
usp mcp tools --root /opt/socsim > "$out/mcp-tools.json"
usp mcp config --client podman --root /opt/socsim > "$out/mcp-podman-config.json"
usp upgrade --root /opt/socsim --audit > "$out/upgrade-audit.json"
SOCSIM_REQUIRE_INTEGRATION=1 python -m pytest \
    tests/test_qemu.py tests/test_sdk_qa.py tests/test_milestone_contracts.py \
    tests/test_multiplatform_soc.py tests/test_mcp_server.py -q \
    -o cache_dir="$out/pytest-cache" --basetemp="$out/tests" --junitxml="$out/junit.xml"
printf 'SDK native smoke + QA + multiplatform SystemC + MCP passed; source revision: '
cat /usr/local/share/socsim-revision
