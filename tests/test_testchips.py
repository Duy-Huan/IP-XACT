import json
import os
import shutil
from pathlib import Path

import pytest
import yaml
from lxml import etree

from socsim.domain import ContractError
from socsim.testchip import accept_chip, load_chip, run_chip, verify_chip, verify_flow

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["AI_SOC", "AIZ_SOC"])
def test_reference_chip_has_exact_bound_inventory_and_explicit_scope(name):
    config, project = load_chip(ROOT / "systems" / name / "rtl-testchip")
    assert len(project.ir.instances) == 9
    assert config["scope"] == "functional-rtl-testchip"
    assert config["isa"] == "rv32imc_zicsr_zifencei"
    assert config["writeback_stage"] is (name == "AIZ_SOC")
    assert len(project.models["bindings"]) == 9


@pytest.mark.parametrize(
    "case", ["extra", "isa", "clock", "source", "name", "binding", "duplicate", "large-ram", "role"]
)
def test_invalid_complete_chip_contract_rejected(tmp_path, case):
    project = tmp_path / "chip"
    shutil.copytree(ROOT / "systems/AI_SOC/rtl-testchip", project)
    path = project / "chip.yaml"
    config = yaml.safe_load(path.read_text())
    if case == "extra":
        config["cpu_count"] = 4
    if case == "isa":
        config["isa"] = "rv64imac"
    if case == "clock":
        config["clock_hz"] = 100000000
    if case == "source":
        config["opentitan_commit"] = "0" * 40
    if case == "name":
        config["name"] = "AIZ_SOC"
    path.write_text(yaml.safe_dump(config))
    if case in {"binding", "duplicate"}:
        path = project / "models.yaml"
        models = yaml.safe_load(path.read_text())
        if case == "binding":
            models["bindings"].pop()
        else:
            models["bindings"].append(models["bindings"][0])
        path.write_text(yaml.safe_dump(models))
    if case == "large-ram":
        path = project / "ipxact/ram.xml"
        tree = etree.parse(str(path))
        tree.find(".//{*}range").text = "4096"
        tree.write(str(path))
    if case == "role":
        path = project / "ipxact/cpu.xml"
        tree = etree.parse(str(path))
        tree.getroot().find("{*}version").text = "different"
        tree.write(str(path))
    with pytest.raises(ContractError):
        load_chip(project)


@pytest.mark.parametrize("name", ["AI_SOC", "AIZ_SOC"])
def test_actual_complete_chip_rtl_route_gate_reset_and_evidence(tmp_path, name):
    if os.environ.get("USP_REQUIRE_TESTCHIP") != "1":
        pytest.skip("complete RTL chip/physical/mapped artifacts explicitly required")
    generated = Path(os.environ.get("USP_RTL_CHIP_ROOT", str(ROOT / "build/rtl-chips"))) / name
    physical = (
        Path(os.environ.get("USP_CHIP_PHYSICAL_ROOT", str(ROOT / "build/rtl-physical"))) / name
    )
    gate = Path(os.environ.get("USP_GATE_CHIP_ROOT", str(ROOT / "build/rtl-gates"))) / name
    record = verify_chip(generated)
    result = accept_chip(generated, physical, gate, tmp_path / "accepted")
    assert result["status"] == "PASSED", result
    assert result["complete_testchip_flow"] and not result["full_architecture_complete"]
    assert result["checks"]["identical_serial_trace"]
    verify_flow(tmp_path / "accepted")
    mapped = json.loads((tmp_path / "accepted/mapped/report.json").read_text())
    assert mapped["level"] == "post-route-functional"
    assert mapped["binary_sha256"] != record["binary_sha256"]
    assert mapped["boots"] == 2
    serial = tmp_path / "accepted/mapped/uart.log"
    serial.write_text(serial.read_text() + "modified")
    with pytest.raises(ContractError, match="modified test-chip"):
        verify_flow(tmp_path / "accepted")
    path = tmp_path / "accepted/report.json"
    r = json.loads(path.read_text())
    r["full_architecture_complete"] = True
    path.write_text(json.dumps(r))
    with pytest.raises(ContractError, match="full-architecture"):
        verify_flow(tmp_path / "accepted")
    limited = run_chip(generated, tmp_path / "short", max_cycles=20, boots=1)
    assert limited["status"] == "TIMED_OUT"
