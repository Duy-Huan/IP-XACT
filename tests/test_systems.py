import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from socsim.domain import ContractError
from socsim.noc import validate_noc
from socsim.pipeline import resolve
from socsim.systems import load_system, plan_system, run_system, verify_system

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name,count,routers", [("AI_SOC", 46, 16), ("AIZ_SOC", 37, 8)])
def test_complete_architecture_and_distinct_bringup_contract(name, count, routers):
    data, full, bringup = load_system(ROOT / "systems" / name)
    assert len(full.ir.instances) == count
    assert len(data["noc"]["endpoints"]) == routers
    assert len(bringup.ir.instances) == 9
    plan = plan_system(ROOT / "systems" / name)
    assert plan["cpu_groups"]["cpu-application"] == 4
    assert plan["cpu_groups"]["cpu-realtime"] == 2
    assert plan["full_system_complete"] is False
    assert "live-noc" in plan["open_requirements"]
    assert full.ir.cpu is None  # Architecture metadata is not an executable CPU contract.
    with pytest.raises(ContractError, match="custom pipeline"):
        resolve(full)


@pytest.mark.parametrize(
    "case", ["path", "identity", "extra-key", "source", "block-missing", "endpoint-missing"]
)
def test_invalid_system_contract_rejected(tmp_path, case):
    root = tmp_path / "soc"
    shutil.copytree(ROOT / "systems/AI_SOC", root)
    path = root / "system.yaml"
    data = yaml.safe_load(path.read_text())
    if case == "path":
        data["bringup"] = "../outside"
    elif case == "identity":
        data["name"] = "AIZ_SOC"
    elif case == "extra-key":
        data["command"] = "echo success"
    elif case == "source":
        data["sources"]["gem5"] = "0" * 40
    elif case == "block-missing":
        data["blocks"].pop()
    else:
        data["noc"]["endpoints"][-1] = "unused"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ContractError):
        load_system(root)


@pytest.mark.parametrize(
    "key,value",
    [
        ("mesh_rows", 3),
        ("simulation_ticks", True),
        ("simulation_ticks", 0),
        ("injection_rate", float("nan")),
        ("injection_rate", 0),
        ("injection_rate", True),
        ("link_width_bits", 17),
        ("router_latency", 0),
        ("endpoints", ["a", "a"]),
        ("endpoints", ["a", "b", "c"]),
    ],
)
def test_bad_noc_experiment_rejected(key, value):
    data, _, _ = load_system(ROOT / "systems/AI_SOC")
    data["noc"][key] = value
    with pytest.raises(ContractError):
        validate_noc(data["noc"])


def test_missing_native_artifacts_fail_all_stages_without_skip(tmp_path):
    report = run_system(
        ROOT / "systems/AI_SOC",
        tmp_path / "missing",
        tmp_path / "missing",
        tmp_path / "missing",
        tmp_path / "result",
    )
    assert report["status"] == "FAILED"
    assert [s["status"] for s in report["stages"]] == ["ERROR"] * 3
    assert not report["full_system_complete"]
    verify_system(tmp_path / "result")


@pytest.mark.parametrize("name", ["AI_SOC", "AIZ_SOC"])
def test_real_dual_soc_guest_garnet_and_synthesis(tmp_path, name):
    if os.environ.get("USP_REQUIRE_DUAL_SOC") != "1":
        pytest.skip("explicit dual-SoC native artifacts required")
    generated = Path(os.environ.get("USP_DUAL_BUILD_ROOT", str(ROOT / "build"))) / name
    models = Path(os.environ.get("USP_IP_MODELS_DIR", str(ROOT / "build/source-ip-models")))
    noc_build = Path(os.environ.get("USP_NOC_BUILD_DIR", str(ROOT / "build/garnet")))
    scope = "bringup" if name == "AI_SOC" else "full"
    report = run_system(
        ROOT / "systems" / name,
        generated,
        models,
        noc_build,
        tmp_path / "result",
        scope,
        Path(os.environ["USP_ASIC_BUILD_DIR"])
        if os.environ.get("USP_REQUIRE_ASIC") == "1"
        else None,
    )
    assert report["selected_stages_status"] == "PASSED", report
    assert report["status"] == ("PASSED" if scope == "bringup" else "BLOCKED")
    assert not report["full_system_complete"]
    verify_system(tmp_path / "result")
    raw = tmp_path / "result/noc/stats.txt"
    raw.write_text(raw.read_text() + "\nmodified\n")
    with pytest.raises(ContractError, match="modified"):
        verify_system(tmp_path / "result")
    path = tmp_path / "result/report.json"
    changed = json.loads(path.read_text())
    changed["full_system_complete"] = True
    path.write_text(json.dumps(changed))
    with pytest.raises(ContractError, match="full-system"):
        verify_system(tmp_path / "result")
