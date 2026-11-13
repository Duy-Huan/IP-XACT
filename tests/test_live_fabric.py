import json
import os
from pathlib import Path

import pytest

from socsim.domain import ContractError
from socsim.fabric import run, verify
from socsim.project import load_project

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["rv64-ips", "AI_SOC", "AIZ_SOC"])
def test_actual_zephyr_source_ip_irqs_and_live_garnet(tmp_path, name):
    if os.environ.get("USP_REQUIRE_LIVE_GARNET") != "1":
        pytest.skip("actual unified bridge and pinned Ruby source explicitly required")
    project = ROOT / ("examples/rv64-ips" if name == "rv64-ips" else f"systems/{name}/bringup")
    generated = (
        Path(os.environ["USP_IP_GENERATED_DIR"])
        if name == "rv64-ips"
        else Path(os.environ["USP_DUAL_BUILD_ROOT"]) / name
    )
    out = tmp_path / "run"
    r = run(
        load_project(project),
        generated,
        Path(os.environ["USP_IP_MODELS_DIR"]),
        Path(os.environ["USP_UNIFIED_GEM5_BUILD"]),
        Path(os.environ["USP_GEM5_SOURCE"]),
        out,
    )
    assert r["status"] == "PASSED"
    assert verify(out)["status"] == "PASSED"
    native = json.loads((out / "report.json").read_text())
    assert all(native["fabric"]["checks"].values())
    assert native["fabric"]["statistics"]["system.ruby.network.average_hops"] > 0
    # A correct guest transcript cannot conceal a CPU port that bypasses Ruby.
    original = (out / "config.json").read_text()
    config = json.loads(original)
    config["system"]["cpu"][0]["dcache_port"]["peer"] = "system.bus.cpu_side_ports[0]"
    (out / "config.json").write_text(json.dumps(config))
    with pytest.raises(ContractError, match="modified live Garnet artifact"):
        verify(out)
    (out / "config.json").write_text(original)
    verify(out)
