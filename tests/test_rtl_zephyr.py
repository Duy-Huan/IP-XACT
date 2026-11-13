import json
import os
from pathlib import Path

import pytest

from socsim.domain import ContractError
from socsim.project import load_project
from socsim.rtl_zephyr import run, verify

ROOT = Path(__file__).resolve().parents[1]


def inputs(name, case=None):
    if os.environ.get("USP_REQUIRE_RTL_ZEPHYR") != "1":
        pytest.skip("actual RV32 Zephyr firmware and RTL masters explicitly required")
    project = ROOT / ("examples/rv64-ips" if name == "rv64-ips" else f"systems/{name}/bringup")
    generated = (
        Path(os.environ["USP_IP_GENERATED_DIR"])
        if name == "rv64-ips"
        else Path(os.environ["USP_DUAL_BUILD_ROOT"]) / name
    )
    return (
        load_project(project),
        generated,
        Path(os.environ["USP_RTL_ZEPHYR_ROOT"]) / (case or name),
    )


@pytest.mark.parametrize("name", ["rv64-ips", "AI_SOC", "AIZ_SOC"])
@pytest.mark.parametrize("model", ["chipyard-ibex", "custom-ibex-wb"])
@pytest.mark.parametrize("engine", ["qemu", "gem5"])
def test_actual_rv32_zephyr_kernel_timer_and_source_ip_drivers(tmp_path, name, model, engine):
    project, generated, firmware = inputs(name)
    cpu = Path(
        os.environ[
            "USP_RTL_CPU_IBEX_BUILD" if model == "chipyard-ibex" else "USP_RTL_CPU_CUSTOM_BUILD"
        ]
    )
    out = tmp_path / "run"
    result = run(
        project,
        generated,
        firmware,
        ROOT / f"models/cpus/{model}.yaml",
        cpu,
        Path(os.environ["USP_IP_MODELS_DIR"]),
        engine,
        out,
        Path(os.environ["USP_RTL_MASTER_BUILD"]),
    )
    assert result["status"] == "PASSED"
    verify(out)
    native = json.loads((out / "report.json").read_text())
    assert native["checks"]["no_iss_execution"]
    assert native["performance"]["kind"] == "native-rtl-counters"
    # The reset shim and selected ISA must remain bound to the running ELF.
    reset = out / "os-firmware/board-root/soc/usp/usp_rv32/reset.S"
    original = reset.read_text()
    reset.write_text(original + "\n# changed\n")
    with pytest.raises(ContractError, match="modified RTL Zephyr software artifact"):
        verify(out)
    reset.write_text(original)
    verify(out)


@pytest.mark.parametrize("engine", ["qemu", "gem5"])
def test_actual_rv32_zephyr_missing_timer_cannot_pass(tmp_path, engine):
    project, generated, firmware = inputs("rv64-ips", "tick-disabled")
    out = tmp_path / "run"
    result = run(
        project,
        generated,
        firmware,
        ROOT / "models/cpus/chipyard-ibex.yaml",
        Path(os.environ["USP_RTL_CPU_IBEX_BUILD"]),
        Path(os.environ["USP_IP_MODELS_DIR"]),
        engine,
        out,
        Path(os.environ["USP_RTL_MASTER_BUILD"]),
        max_cycles=300000,
    )
    assert result["status"] == "FAILED"
    serial = (out / "uart.log").read_text()
    assert "USP_ZEPHYR_BOOT_OK" in serial and "USP_TIMER_IRQ_OK" not in serial
    verify(out)
