import shutil
from pathlib import Path

import pytest
import yaml

from socsim.domain import ContractError
from socsim.upgrade import validate_ledger

ROOT = Path(__file__).resolve().parents[1]


def test_approved_programme_is_sequential_and_completion_is_not_inferred():
    result = validate_ledger(ROOT)
    assert result["execution_order"] == [f"M{i}" for i in range(1, 10)]
    assert result["aggregate_ci"] == "after_complete_batch"
    assert result["milestones"]["M8"] != "verified"


@pytest.mark.parametrize("case", ["cycle", "false-completion", "missing-evidence", "impl-ref-only"])
def test_upgrade_rejects_invalid_dependencies_and_unproved_completion(tmp_path, case):
    shutil.copytree(ROOT / "upgrade", tmp_path / "upgrade")
    path = tmp_path / "upgrade/milestones.yaml"
    data = yaml.safe_load(path.read_text())
    milestone = data["milestones"][0]
    if case == "cycle":
        milestone["depends_on"] = ["M9"]
    elif case == "false-completion":
        milestone["status"] = "verified"
    elif case == "missing-evidence":
        milestone["required_exits"][0]["status"] = "verified"
        milestone["required_exits"][0]["evidence"] = []
    else:
        milestone["required_exits"][0]["status"] = "verified"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ContractError):
        validate_ledger(tmp_path)
