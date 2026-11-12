import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from socsim.domain import ContractError
from socsim.workspace import load_workspace, run_workspace, verify_bundle

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config(tmp_path):
    shutil.copytree(ROOT / "examples", tmp_path / "examples")
    path = tmp_path / "workspace.yaml"
    shutil.copy2(ROOT / "workspace.yaml", path)
    return path


@pytest.mark.parametrize("case", ["duplicate", "escape", "unknown", "extra", "wrong-profile"])
def test_workspace_rejects_invalid_contract(config, case):
    data = yaml.safe_load(config.read_text())
    if case == "duplicate":
        data["targets"].append(data["targets"][0])
    if case == "escape":
        data["targets"][0]["generated"] = "../outside"
    if case == "unknown":
        data["targets"][0]["kind"] = "arbitrary-command"
    if case == "extra":
        data["targets"][0]["shell"] = "execute this"
    if case == "wrong-profile":
        data["targets"][0]["kind"] = "source-ips"
        data["targets"][0]["models"] = "build/model"
    config.write_text(yaml.safe_dump(data))
    with pytest.raises(ContractError):
        load_workspace(config)


def test_missing_builds_fail_every_target_without_skip(config, tmp_path):
    result = run_workspace(config, tmp_path / "run")
    assert result["status"] == "FAILED"
    assert len(result["targets"]) == 8
    assert all(r["status"] == "ERROR" for r in result["targets"])
    assert verify_bundle(tmp_path / "run")["full_master_complete"] is False
    with pytest.raises(FileExistsError):
        run_workspace(config, tmp_path / "run")


@pytest.mark.integration
def test_real_multi_soc_acceptance_and_evidence_integrity(tmp_path):
    if os.environ.get("USP_REQUIRE_WORKSPACE") != "1":
        pytest.skip("full multi-SoC acceptance is an explicit native gate")
    out = tmp_path / "acceptance"
    result = run_workspace(ROOT / "workspace.yaml", out)
    assert result["status"] == "PASSED"
    assert len(result["targets"]) == 8
    assert verify_bundle(out)["status"] == "PASSED"
    serial = out / "zephyr/stdout.log"
    original = serial.read_bytes()
    serial.write_bytes(original + b"modified")
    with pytest.raises(ContractError, match="modified target artifact"):
        verify_bundle(out)
    serial.write_bytes(original)
    summary = json.loads((out / "report.json").read_text())
    summary["full_master_complete"] = True
    (out / "report.json").write_text(json.dumps(summary))
    with pytest.raises(ContractError, match="invalid workspace acceptance claim"):
        verify_bundle(out)
