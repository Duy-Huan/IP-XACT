#!/usr/bin/env bash
# Offline native smoke: source parsing/generation, real guest success/failure/timeout.
set -euo pipefail
cd /opt/socsim
out=$(mktemp -d /tmp/socsim-container-smoke.XXXXXXXX)
on_err() {
    local rc=$?
    local f
    echo "SMOKE FAILED (rc=$rc) at line $1" >&2
    ls -la "$out" >&2
    for f in "$out"/*.json; do
        [ -s "$f" ] && echo "=== $f ===" >&2 && tail -n 40 "$f" >&2
    done
    exit "$rc"
}
trap 'on_err $LINENO' ERR
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
usp ip scaffold xor_accel --out "$out/scaffold-xor-accel" > "$out/scaffold.json"
usp package build "$out/scaffold-xor-accel/package.yaml" --out "$out/xor-accel-build" \
    > "$out/xor-accel-build.json"
usp qa run --suite multiplatform --root /opt/socsim --out "$out/qa-multiplatform" \
    > "$out/qa-multiplatform.json"
usp qa scorecard "$out/qa-multiplatform" > "$out/qa-multiplatform-scorecard.json"
usp unified-soc run specs/multiplatform/unified_hetero_soc.yaml --out "$out/unified-soc" \
    > "$out/unified-soc.json"
cat "$out/unified-soc/console.log"
usp unified-soc calibrate specs/multiplatform/unified_hetero_soc.yaml \
    --out "$out/unified-calibrate" > "$out/unified-calibrate.json"
usp registry publish specs/multiplatform/packages/mac_npu/package.yaml \
    --registry "$out/ip-registry" --version 1.0.0 > "$out/reg-pub1.json"
usp registry publish "$out/scaffold-xor-accel/package.yaml" \
    --registry "$out/ip-registry" --version 1.0.0 > "$out/reg-pub2.json"
usp registry verify --registry "$out/ip-registry" > "$out/reg-verify.json"
usp dse sweep specs/multiplatform/unified_hetero_soc.yaml --out "$out/dse-sweep" \
    --workers 2 --cluster process --cache "$out/dse-cache" > "$out/dse-sweep.json"
usp loop run specs/multiplatform/unified_hetero_soc.yaml --out "$out/chia-loop" \
    --max-iters 4 --cache "$out/loop-cache" > "$out/chia-loop.json"
usp dashboard generate --out "$out/dashboard.html" \
    --unified "$out/unified-soc/report.json" \
    --calibration "$out/unified-calibrate/calibration.json" \
    --dse "$out/dse-sweep/dse-report.json" \
    --loop "$out/chia-loop/loop-report.json" > "$out/dashboard.json"
usp unified-soc run examples/ASoC/asoc_unified.yaml --out "$out/asoc-run" \
    > "$out/asoc-run.json"
echo "=== ASOC LIVE CONSOLE ==="
cat "$out/asoc-run/console.log"
echo "=== ASOC LIVE REPORT ==="
cat "$out/asoc-run/report.md"
usp unified-soc calibrate examples/ASoC/asoc_unified.yaml --out "$out/asoc-cal" \
    > "$out/asoc-cal.json"
echo "=== ASOC CALIBRATION JSON ==="
cat "$out/asoc-cal/calibration.json"
usp dse sweep examples/ASoC/asoc_unified.yaml --out "$out/asoc-dse" \
    --workers 2 --cluster process --cache "$out/asoc-dse-cache" > "$out/asoc-dse.json"
echo "=== ASOC DSE REPORT ==="
cat "$out/asoc-dse/dse-report.md"
usp loop run examples/ASoC/asoc_unified.yaml --out "$out/asoc-loop" \
    --max-iters 4 --cache "$out/asoc-loop-cache" > "$out/asoc-loop.json"
echo "=== ASOC CHIA LOOP REPORT ==="
cat "$out/asoc-loop/loop-report.md"
usp dashboard generate --out "$out/asoc-dashboard.html" \
    --unified "$out/asoc-run/report.json" \
    --calibration "$out/asoc-cal/calibration.json" \
    --dse "$out/asoc-dse/dse-report.json" \
    --loop "$out/asoc-loop/loop-report.json" > "$out/asoc-dashboard.json"
usp soc generate examples/ASoC/asoc_fabric.yaml --input-format fabric --engine both \
    --out "$out/asoc-fabric" > "$out/asoc-fabric.json"
usp flow preflight examples/ASoC/asoc_flow.yaml > "$out/asoc-flow.json"
usp mcp tools --root /opt/socsim > "$out/mcp-tools.json"
usp mcp config --client podman --root /opt/socsim > "$out/mcp-podman-config.json"
usp upgrade --root /opt/socsim --audit > "$out/upgrade-audit.json"
SOCSIM_REQUIRE_INTEGRATION=1 USP_REQUIRE_SYSTEMC=1 python -m pytest \
    tests/test_qemu.py tests/test_sdk_qa.py tests/test_milestone_contracts.py \
    tests/test_multiplatform_soc.py tests/test_unified_systemc_soc.py \
    tests/test_asoc_example.py \
    tests/test_enterprise_registry_dse.py tests/test_mcp_server.py \
    tests/test_project_standardization.py -q \
    -o cache_dir="$out/pytest-cache" --basetemp="$out/tests" --junitxml="$out/junit.xml"
printf 'SDK native smoke + QA + ASoC (2-Cluster 20-IP 7x RTL) + Dual-Hart SMP + CHIA Loop + IP Scaffold + Dashboard + MCP passed; source revision: '
cat /usr/local/share/socsim-revision
