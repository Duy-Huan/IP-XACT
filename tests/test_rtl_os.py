import json
import os
from pathlib import Path

import pytest

from socsim.domain import ContractError
from socsim.project import load_project
from socsim.rtl_os import run, verify

ROOT = Path(__file__).resolve().parents[1]


def paths(name):
    if os.environ.get("USP_REQUIRE_RTL_OS") != "1":
        pytest.skip("actual RV32 RTOS RTL binaries explicitly required")
    project = ROOT / ("examples/rv64-ips" if name == "rv64-ips" else f"systems/{name}/bringup")
    generated = (
        Path(os.environ["USP_IP_GENERATED_DIR"])
        if name == "rv64-ips"
        else Path(os.environ["USP_DUAL_BUILD_ROOT"]) / name
    )
    return load_project(project), generated


@pytest.mark.parametrize("name", ["rv64-ips", "AI_SOC", "AIZ_SOC"])
@pytest.mark.parametrize("model", ["chipyard-ibex", "custom-ibex-wb"])
@pytest.mark.parametrize("engine", ["qemu", "gem5"])
def test_real_rv32_freertos_preemption_registers_and_irqs(tmp_path, name, model, engine):
    project, generated = paths(name)
    cpu = Path(
        os.environ[
            "USP_RTL_CPU_IBEX_BUILD" if model == "chipyard-ibex" else "USP_RTL_CPU_CUSTOM_BUILD"
        ]
    )
    firmware = Path(os.environ["USP_RTL_OS_ROOT"]) / name
    r = run(
        project,
        generated,
        firmware,
        ROOT / f"models/cpus/{model}.yaml",
        cpu,
        Path(os.environ["USP_IP_MODELS_DIR"]),
        engine,
        tmp_path / "run",
        Path(os.environ["USP_RTL_MASTER_BUILD"]),
    )
    assert r["status"] == "PASSED", r
    assert all(r["checks"].values()) and r["metrics"]["timer_irqs"] >= 30
    verify(tmp_path / "run")
    native = json.loads((tmp_path / "run/report.json").read_text())
    assert native["checks"]["no_iss_execution"]
    assert native["performance"]["kind"] == "native-rtl-counters"
    # A correct UART log cannot hide a changed software source or provenance.
    source = tmp_path / "run/os-firmware/start.S"
    original = source.read_text()
    source.write_text(original + "\n# changed\n")
    with pytest.raises(ContractError, match="modified RTL RTOS software artifact"):
        verify(tmp_path / "run")
    source.write_text(original)
    verify(tmp_path / "run")


@pytest.mark.parametrize("case", ["assert-failure", "tick-disabled"])
def test_real_rtos_failures_cannot_pass(tmp_path, case):
    project, generated = paths("rv64-ips")
    r = run(
        project,
        generated,
        Path(os.environ["USP_RTL_OS_ROOT"]) / case,
        ROOT / "models/cpus/chipyard-ibex.yaml",
        Path(os.environ["USP_RTL_CPU_IBEX_BUILD"]),
        Path(os.environ["USP_IP_MODELS_DIR"]),
        "qemu",
        tmp_path / "run",
        max_cycles=50000 if case == "tick-disabled" else 1000000,
    )
    assert r["status"] == "FAILED"
    if case == "assert-failure":
        assert "USP_FREERTOS_FAIL" in (tmp_path / "run/uart.log").read_text()
    verify(tmp_path / "run")
