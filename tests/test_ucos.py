import json
import os
from pathlib import Path

import pytest

from socsim.domain import ContractError
from socsim.project import load_project
from socsim.ucos import run, verify

ROOT = Path(__file__).resolve().parents[1]


def inputs(name, kernel, case=None):
    if os.environ.get("USP_REQUIRE_UCOS") != "1":
        pytest.skip("actual µC/OS-II/III kernels and RTL executables explicitly required")
    project = ROOT / ("examples/rv64-ips" if name == "rv64-ips" else f"systems/{name}/bringup")
    generated = (
        Path(os.environ["USP_IP_GENERATED_DIR"])
        if name == "rv64-ips"
        else Path(os.environ["USP_DUAL_BUILD_ROOT"]) / name
    )
    firmware = Path(os.environ["USP_UCOS_ROOT"]) / kernel / (case or name)
    return load_project(project), generated, firmware


@pytest.mark.parametrize("kernel", ["ucos2", "ucos3"])
@pytest.mark.parametrize("name", ["rv64-ips", "AI_SOC", "AIZ_SOC"])
@pytest.mark.parametrize("model", ["chipyard-ibex", "custom-ibex-wb"])
@pytest.mark.parametrize("engine", ["qemu", "gem5"])
def test_actual_ucos_preemption_context_and_source_ip_irqs(tmp_path, kernel, name, model, engine):
    project, generated, firmware = inputs(name, kernel)
    cpu = Path(
        os.environ[
            "USP_RTL_CPU_IBEX_BUILD" if model == "chipyard-ibex" else "USP_RTL_CPU_CUSTOM_BUILD"
        ]
    )
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
    assert all(r["checks"].values())
    assert r["metrics"]["gcd_irqs"] == 4 and r["metrics"]["gpio_irqs"] == 1
    verify(tmp_path / "run")
    native = json.loads((tmp_path / "run/report.json").read_text())
    assert native["checks"]["no_iss_execution"]
    assert native["performance"]["kind"] == "native-rtl-counters"
    assert r["os_build"]["kernel"] == kernel
    # Acceptance must bind the compiled port and its preserved license as well.
    port = tmp_path / "run/os-firmware/os_cpu_a.S"
    original = port.read_text()
    port.write_text(original + "\n# changed adapter\n")
    with pytest.raises(ContractError, match="modified µC/OS software artifact"):
        verify(tmp_path / "run")
    port.write_text(original)
    verify(tmp_path / "run")


@pytest.mark.parametrize("kernel", ["ucos2", "ucos3"])
@pytest.mark.parametrize("case", ["assert-failure", "tick-disabled"])
def test_actual_ucos_failure_and_disabled_tick(tmp_path, kernel, case):
    project, generated, firmware = inputs("rv64-ips", kernel, case)
    r = run(
        project,
        generated,
        firmware,
        ROOT / "models/cpus/chipyard-ibex.yaml",
        Path(os.environ["USP_RTL_CPU_IBEX_BUILD"]),
        Path(os.environ["USP_IP_MODELS_DIR"]),
        "qemu",
        tmp_path / "run",
        max_cycles=50000 if case == "tick-disabled" else 1500000,
    )
    assert r["status"] == "FAILED"
    if case == "assert-failure":
        assert "USP_UCOS_FAIL" in (tmp_path / "run/uart.log").read_text()
    verify(tmp_path / "run")
