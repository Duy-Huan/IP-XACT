import os
from pathlib import Path

import pytest

from socsim.ipxact import digest
from socsim.pipeline import resolve
from socsim.project import load_project
from socsim.unified import accept_system, verify_system

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["AI_SOC", "AIZ_SOC"])
def test_visible_example_matches_canonical_executable_project(name):
    source = ROOT / "systems" / name / "bringup"
    example = ROOT / "examples" / name

    def inventory(p):
        return {
            f.relative_to(p).as_posix(): digest(f)
            for f in p.rglob("*")
            if f.is_file() and f.name != "README.md"
        }

    assert inventory(example) == inventory(source)
    p = load_project(example)
    assert p.ir == load_project(source).ir
    assert len(p.ir.instances) == len(resolve(p)) == 9


@pytest.mark.parametrize("name", ["AI_SOC", "AIZ_SOC"])
def test_actual_visible_example_shared_guest_on_both_engines(tmp_path, name):
    if os.environ.get("USP_REQUIRE_UNIFIED") != "1":
        pytest.skip("actual generated firmware, source-IP models and both engines required")
    out = tmp_path / "run"
    result = accept_system(
        load_project(ROOT / "examples" / name),
        Path(os.environ["USP_DUAL_BUILD_ROOT"]) / name,
        Path(os.environ["USP_IP_MODELS_DIR"]),
        Path(os.environ["USP_UNIFIED_GEM5_BUILD"]),
        out,
    )
    assert result["status"] == verify_system(out)["status"] == "PASSED"
    assert all(result["checks"].values())
    assert result["full_architecture_complete"] is False
