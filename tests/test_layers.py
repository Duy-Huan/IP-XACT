import json
import os
from pathlib import Path

import pytest

from socsim.domain import ContractError
from socsim.layers import LAYERS, run_matrix, verify_matrix
from socsim.performance import gem5_metrics, qemu_metrics
from socsim.project import load_project

ROOT = Path(__file__).resolve().parents[1]


def test_qemu_costs_are_explicit_estimates_not_native_cycles():
    r = qemu_metrics({"executed_instructions": 100, "data_accesses": 20})
    assert r["estimated_cycles"] == 120
    assert r["estimated_cpi"] == 1.2
    assert r["estimated_ipc"] == pytest.approx(100 / 120)
    assert r["native_cpi"] is None and r["native_ipc"] is None


@pytest.mark.parametrize("bad", [0, -1, True, float("nan"), float("inf"), "1"])
def test_invalid_qemu_base_cost_rejected(bad):
    with pytest.raises(ContractError):
        qemu_metrics(
            {"executed_instructions": 100, "data_accesses": 0},
            {"base_cpi": bad, "memory_extra_cycles": 1},
        )


@pytest.mark.parametrize(
    "counters",
    [
        {},
        {"executed_instructions": 0, "data_accesses": 0},
        {"executed_instructions": 10, "data_accesses": 1, "mmio_accesses": 2},
        {"executed_instructions": True, "data_accesses": 0},
    ],
)
def test_invalid_measured_qemu_counters_rejected(counters):
    with pytest.raises(ContractError):
        qemu_metrics(counters)


def test_cost_overflow_rejected():
    with pytest.raises(ContractError, match="overflow"):
        qemu_metrics(
            {"executed_instructions": 100, "data_accesses": 0},
            {"base_cpi": 1e308, "memory_extra_cycles": 0},
        )


def test_gem5_uses_committed_cpu_stat_and_last_dump(tmp_path):
    raw = tmp_path / "stats.txt"
    raw.write_text(
        "simInsts 100\nsystem.cpu.commitStats0.numInsts 100\n"
        "system.cpu.numCycles 200\nsimTicks 200000\nsimSeconds 0.0000002\n"
        "simInsts 119\nsystem.cpu.commitStats0.numInsts 120\n"
        "system.cpu.numCycles 300\nsimTicks 300000\nsimSeconds 0.0000003\n"
    )
    r = gem5_metrics(raw)
    assert r["native_cpi"] == 2.5 and r["native_ipc"] == 0.4
    assert r["global_sim_instructions"] == 119
    raw.write_text("simInsts 120\n")
    with pytest.raises(ContractError):
        gem5_metrics(raw)


@pytest.mark.parametrize("name", ["rv64-ips", "AI_SOC", "AIZ_SOC"])
def test_actual_all_software_layers_and_performance_on_both_engines(tmp_path, name):
    if os.environ.get("USP_REQUIRE_SW_LAYERS") != "1":
        pytest.skip("source-built software matrix and performance engines explicitly required")
    target = "examples/rv64-ips" if name == "rv64-ips" else f"systems/{name}/bringup"
    generated = Path(os.environ.get("USP_DUAL_BUILD_ROOT", str(ROOT / "build"))) / name
    models = Path(os.environ.get("USP_IP_MODELS_DIR", str(ROOT / "build/source-ip-models")))
    bridge = Path(os.environ.get("USP_UNIFIED_GEM5_BUILD", str(ROOT / "build/unified-gem5")))
    counter = Path(os.environ.get("USP_COUNTER_BUILD_DIR", str(ROOT / "build/performance-counter")))
    r = run_matrix(
        load_project(ROOT / target), generated, models, bridge, counter, tmp_path / "matrix"
    )
    assert r["status"] == "PASSED", r["checks"]
    verify_matrix(tmp_path / "matrix")
    for layer in LAYERS:
        for engine in ("qemu", "gem5"):
            p = json.loads((tmp_path / "matrix" / layer / engine / "performance.json").read_text())
            if engine == "qemu":
                assert p["native_cpi"] is None and p["estimated_cpi"] > 0
                assert p["counters"]["executed_instructions"] > 0
            else:
                assert p["native_cpi"] > 0 and p["native_ipc"] > 0
    p = tmp_path / "matrix/baremetal/qemu/qemu-counters.json"
    p.write_text('{"executed_instructions":0}')
    with pytest.raises(ContractError, match="modified software matrix"):
        verify_matrix(tmp_path / "matrix")
