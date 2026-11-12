from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from socsim.cpu_platform import cpu_selection, load_platform
from socsim.domain import ContractError
from socsim.execution_plan import compile_execution_plan, validate_bridge_trace
from socsim.project import load_project
from socsim.unified import binding_plan

ROOT = Path(__file__).resolve().parents[1]


def plan_for(name):
    project = load_project(ROOT / "examples/rv64-ips")
    config = load_platform(ROOT / "platforms" / (name + ".yaml"))
    selected = cpu_selection(config, config["engine"], require=False)
    return compile_execution_plan(
        project, config, selected, binding_plan(project, config["engine"])["bindings"]
    )


@pytest.mark.parametrize("name", ["qemu-rv64", "gem5-timing", "gem5-atomic"])
def test_execution_plan_is_immutable_and_preserves_single_memory_owner(name):
    plan = plan_for(name)
    plan.require_executable()
    assert len(plan.bindings) == 9
    assert {r.owner for r in plan.address_routes if r.target in {"ram", "rom"}} == {plan.engine}
    assert {b.instance for b in plan.bindings if b.domain == "systemc-verilator"} == {
        "cygcd",
        "otgpio",
    }
    assert not plan.cpu_rtl_cycle_correlation and not plan.dma_supported
    with pytest.raises(FrozenInstanceError):
        plan.engine = "another-engine"


def test_unresolved_rtl_cpu_plan_cannot_execute():
    with pytest.raises(ContractError, match="unresolved CPU"):
        plan_for("chipyard-rocket-gem5").require_executable()


@pytest.mark.parametrize(
    "field,value,check",
    [
        ("seq", 2, "bridge_order"),
        ("time_ps", -1, "bridge_annotated_time"),
        ("offset", 2, "bridge_routes"),
        ("irq", 4, "bridge_irq_levels"),
    ],
)
def test_invalid_bridge_contract_evidence_is_detected(field, value, check):
    row = {"seq": 1, "op": 0, "offset": 0, "irq": 0, "status": 0, "time_ps": 20000, "cycles": 2}
    assert all(validate_bridge_trace([row]).values())
    row[field] = value
    assert not validate_bridge_trace([row])[check]
