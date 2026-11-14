import json
import os
from pathlib import Path

import pytest

from socsim.cpu_platform import load_platform, platform_plan, run_platform, verify_platform
from socsim.domain import ContractError
from socsim.project import load_project
from socsim.rtl_platform import validate_builds

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("model", ["chipyard-ibex", "custom-ibex-wb"])
@pytest.mark.parametrize("engine", ["qemu", "gem5"])
def test_unified_rtl_software_plan_keeps_actual_instruction_owner(model, engine):
    c = load_platform(ROOT / f"platforms/{model}-rtos-{engine}.yaml")
    p = platform_plan(load_project(ROOT / "examples/rv64-ips"), c)
    assert p["status"] == "AVAILABLE_ADAPTER"
    assert p["workloads"] == ["baremetal", "freertos", "ucos2", "ucos3", "zephyr"]
    assert p["ownership"]["instructions"] == "systemc-verilator-cpu"
    assert p["cpu"]["performance"] == "native-rtl-counters"


@pytest.mark.parametrize("case", ["missing", "extra", "linux", "clock", "elf", "iss"])
def test_unified_rtl_software_rejects_unverified_or_unsupported_contract(case):
    c = load_platform(ROOT / "platforms/chipyard-ibex-rtos-gem5.yaml")
    if case == "missing":
        del c["software_builds"]["zephyr"]
    if case == "extra":
        c["software_builds"]["linux"] = "/unused"
    if case == "linux":
        c["workloads"].append("linux")
    if case == "clock":
        c["cpu"]["clock_hz"] = 100000000
    if case == "elf":
        c["artifacts"] = {"zephyr": {}}
    if case == "iss":
        c["cpu"] = {"provider": "gem5", "model": "timing-simple"}
    with pytest.raises(ContractError):
        platform_plan(load_project(ROOT / "examples/rv64-ips"), c)


def configured(name, model, engine):
    c = load_platform(ROOT / f"platforms/{model}-rtos-{engine}.yaml")
    c["software_builds"] = {
        "freertos": str(Path(os.environ["USP_RTL_OS_ROOT"]) / name),
        "ucos2": str(Path(os.environ["USP_UCOS_ROOT"]) / "ucos2" / name),
        "ucos3": str(Path(os.environ["USP_UCOS_ROOT"]) / "ucos3" / name),
        "zephyr": str(Path(os.environ["USP_RTL_ZEPHYR_ROOT"]) / name),
    }
    return c


def test_actual_swapped_kernel_and_missing_tick_build_rejected_before_execution():
    if os.environ.get("USP_REQUIRE_RTL_PLATFORM") != "1":
        pytest.skip("actual source-built kernel provenance required")
    p = load_project(ROOT / "examples/rv64-ips")
    c = configured("rv64-ips", "chipyard-ibex", "gem5")
    c["software_builds"]["ucos2"] = c["software_builds"]["ucos3"]
    with pytest.raises(ContractError, match="selected µC/OS kernel"):
        validate_builds(p, c)
    c = configured("rv64-ips", "chipyard-ibex", "gem5")
    c["software_builds"]["zephyr"] = str(Path(os.environ["USP_RTL_ZEPHYR_ROOT"]) / "tick-disabled")
    with pytest.raises(ContractError, match="positive smoke"):
        validate_builds(p, c)


@pytest.mark.parametrize(
    "name,model,engine",
    [
        ("AI_SOC", "chipyard-ibex", "qemu"),
        ("AI_SOC", "custom-ibex-wb", "gem5"),
        ("AIZ_SOC", "chipyard-ibex", "gem5"),
        ("AIZ_SOC", "custom-ibex-wb", "qemu"),
    ],
)
def test_actual_single_platform_runs_all_five_software_layers(tmp_path, name, model, engine):
    if os.environ.get("USP_REQUIRE_RTL_PLATFORM") != "1":
        pytest.skip("actual RTL CPU and all source-built kernel profiles required")
    c = configured(name, model, engine)
    cpu = os.environ[
        "USP_RTL_CPU_IBEX_BUILD" if model == "chipyard-ibex" else "USP_RTL_CPU_CUSTOM_BUILD"
    ]
    out = tmp_path / "run"
    r = run_platform(
        load_project(ROOT / f"systems/{name}/bringup"),
        c,
        Path(os.environ["USP_DUAL_BUILD_ROOT"]) / name,
        Path(os.environ["USP_IP_MODELS_DIR"]),
        Path(os.environ["USP_RTL_MASTER_BUILD"]),
        None,
        out,
        Path(cpu),
    )
    assert r["status"] == verify_platform(out)["status"] == "PASSED"
    assert set(r["results"]) == set(c["workloads"])
    for layer in c["workloads"]:
        stage = out / layer / "baremetal" if layer == "baremetal" else out / layer
        n = json.loads((stage / "report.json").read_text())
        assert n["checks"]["no_iss_execution"]
        assert n["performance"]["kind"] == "native-rtl-counters"
    f = out / "report.json"
    original = f.read_text()
    data = json.loads(original)
    data["results"]["zephyr"]["performance"]["ipc"] = 999
    f.write_text(json.dumps(data))
    with pytest.raises(ContractError, match="invalid RTL software platform acceptance"):
        verify_platform(out)
    f.write_text(original)
    verify_platform(out)
