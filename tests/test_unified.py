import json
import os
from pathlib import Path

import pytest

from socsim.domain import ContractError
from socsim.project import load_project
from socsim.unified import accept_system, binding_plan, board_contract, run_system, verify_system

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "target", ["examples/rv64-ips", "systems/AI_SOC/bringup", "systems/AIZ_SOC/bringup"]
)
def test_unified_board_uses_actual_ipxact_windows_and_plic_sources(target):
    project = load_project(ROOT / target)
    board = board_contract(project)
    for instance in project.ir.instances:
        for window in instance.windows:
            assert board[instance.name + "_base"] == window.base
            assert board[instance.name + "_size"] == window.size
    for irq in project.ir.interrupts:
        if irq.target == "plic":
            assert board[irq.source + "_irq"] == irq.line
    assert board["plic_sources"] > max(board["cygcd_irq"], board["otgpio_irq"])
    for engine in ("qemu", "gem5"):
        plan = binding_plan(project, engine)
        assert len(plan["bindings"]) == 9
        assert {b["instance"] for b in plan["bindings"] if b["owner"] == "systemc-verilator"} == {
            "cygcd",
            "otgpio",
        }


@pytest.mark.parametrize("bound", [0, -1, True, 10000000000001])
def test_unified_rejects_invalid_simulation_bounds(bound):
    with pytest.raises(ContractError, match="bound"):
        board_contract(load_project(ROOT / "examples/rv64-ips"), bound)


def test_unified_rejects_unbound_source_ip_profile():
    with pytest.raises(ContractError, match="usp-rv64-ips"):
        board_contract(load_project(ROOT / "examples/rv64-minimal"))


@pytest.mark.parametrize("name", ["rv64-ips", "AI_SOC", "AIZ_SOC"])
def test_actual_same_soc_zephyr_and_rtl_ips_on_qemu_and_gem5(tmp_path, name):
    if os.environ.get("USP_REQUIRE_UNIFIED") != "1":
        pytest.skip("full-system gem5/SystemC source build explicitly required")
    target = "examples/rv64-ips" if name == "rv64-ips" else f"systems/{name}/bringup"
    project = load_project(ROOT / target)
    generated = Path(os.environ.get("USP_DUAL_BUILD_ROOT", str(ROOT / "build"))) / name
    if name == "rv64-ips":
        generated = Path(os.environ.get("USP_IP_GENERATED_DIR", str(generated)))
    models = Path(os.environ.get("USP_IP_MODELS_DIR", str(ROOT / "build/source-ip-models")))
    bridge = Path(os.environ.get("USP_UNIFIED_GEM5_BUILD", str(ROOT / "build/unified-gem5")))
    result = accept_system(project, generated, models, bridge, tmp_path / "accepted")
    assert result["status"] == "PASSED", result
    assert all(result["checks"].values()) and not result["full_architecture_complete"]
    verify_system(tmp_path / "accepted")
    mapped = json.loads((tmp_path / "accepted/gem5/report.json").read_text())
    assert mapped["engine"] == "gem5-full-system"
    assert mapped["checks"]["actual_cpu_execution"]
    assert mapped["model_process"]["returncode"] == 0
    trace = tmp_path / "accepted/gem5/bridge-trace.jsonl"
    trace.write_text(trace.read_text() + "modified")
    with pytest.raises(ContractError, match="modified unified"):
        verify_system(tmp_path / "accepted")
    if name == "rv64-ips":
        limited = run_system(project, generated, models, bridge, tmp_path / "short", max_ticks=1000)
        assert limited["status"] == "FAILED"
        assert limited["exit"]["cause"] == "simulate() limit reached"
        assert not limited["checks"]["gcd_results"]
