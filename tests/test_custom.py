import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from socsim.cli import main
from socsim.domain import ContractError
from socsim.pipeline import generate, identity, resolve, run_zephyr, verify_generated
from socsim.project import load_project

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def custom_project(tmp_path):
    root = tmp_path / "custom"
    shutil.copytree(ROOT / "examples/rv64-minimal", root)
    return root


def edit_hardware(root, change):
    path = root / "hardware.yaml"
    value = yaml.safe_load(path.read_text())
    change(value)
    path.write_text(yaml.safe_dump(value))


def test_custom_typed_contract(custom_project):
    p = load_project(custom_project)
    assert p.ir.schema_version == "2"
    assert p.ir.cpu.entry == 0x80000000
    assert p.ir.cpu.reset_vector == 0x1000
    assert len(p.ir.interrupts) == 5
    assert p.ir.address_spaces[0].initiator == "rv64"
    assert len(resolve(p)) == 7
    assert dict(p.ir.sources)["hardware.yaml"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("isa", "rv32i"),
        ("privilege", "U"),
        ("mmu", "sv48"),
        ("reset_vector", 0x2000),
        ("entry", 0x80001000),
        ("timebase_hz", 1),
    ],
)
def test_incompatible_cpu_rejected(custom_project, field, value):
    edit_hardware(custom_project, lambda d: d["cpu"].update({field: value}))
    with pytest.raises(ContractError):
        load_project(custom_project)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["interrupts"].pop(),
        lambda d: d["interrupts"][0].update(line=12),
        lambda d: d["interrupts"][-1].update(trigger="edge-low"),
        lambda d: d["interrupts"][-1].update(source="unknown"),
        lambda d: d["address_space"].update(initiator="uart"),
        lambda d: d["address_space"]["targets"].remove("rom"),
    ],
)
def test_invalid_graph_rejected(custom_project, change):
    edit_hardware(custom_project, change)
    with pytest.raises(ContractError):
        load_project(custom_project)


def test_deterministic_generation_and_relocated_uart(custom_project, tmp_path):
    p = load_project(custom_project)
    first = generate(p, tmp_path / "one")
    second = generate(p, tmp_path / "two")
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()
    assert "0x11000000" in (first / "machine.c").read_text()
    assert "uart@11000000" in (first / "zephyr/boards/usp/usp_rv64/usp_rv64.dts").read_text()
    assert 'DEFINE_MACHINE("usp-rv64"' in (first / "machine.c").read_text()
    assert verify_generated(p, first)["ir_sha256"] == identity(p)
    assert len(json.loads((first / "models.lock.json").read_text())["models"]) == 7
    with pytest.raises(FileExistsError):
        generate(p, first)


def test_changed_generated_source_rejected(custom_project, tmp_path):
    p = load_project(custom_project)
    out = generate(p, tmp_path / "generated")
    (out / "machine.c").write_text("modified")
    with pytest.raises(ContractError, match="changed"):
        verify_generated(p, out)


def test_changed_input_rejected(custom_project, tmp_path):
    p = load_project(custom_project)
    out = generate(p, tmp_path / "generated")
    edit_hardware(custom_project, lambda d: d["interrupts"][-1].update(line=5))
    changed = load_project(custom_project)
    with pytest.raises(ContractError, match="stale"):
        verify_generated(changed, out)


def test_wrong_model_rejected(custom_project):
    path = custom_project / "models.yaml"
    data = yaml.safe_load(path.read_text())
    data["bindings"][0]["implementation"] = "generic.fake"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ContractError, match="unbound"):
        resolve(load_project(custom_project))


def test_lifecycle_cli(custom_project, tmp_path, capsys):
    output = tmp_path / "generated"
    assert main(["generate", str(custom_project), "--out", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "GENERATED"
    assert main(["models", "explain", str(custom_project), "--instance", "uart"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["models"][0]["interrupts"]
    assert main(["models", "explain", str(custom_project), "--instance", "invalid"]) == 2
    assert main(["ipxact", "validate", str(custom_project)]) == 0


@pytest.mark.integration
def test_real_custom_zephyr_boot_timer_and_uart_irq(tmp_path):
    generated = os.environ.get("USP_GENERATED_DIR")
    if not generated:
        if os.environ.get("USP_REQUIRE_BRINGUP") == "1":
            pytest.fail("USP_GENERATED_DIR is required for the custom-machine acceptance gate")
        pytest.skip("custom QEMU/Zephyr build is optional in the quick core job")
    p = load_project(ROOT / "examples/rv64-minimal")
    report = run_zephyr(p, Path(generated).resolve(), tmp_path / "zephyr")
    assert report["status"] == "PASSED"
    assert report["returncode"] == 0
    assert report["assertions"]["USP_TIMER_IRQ_OK"]
    assert report["assertions"]["USP_UART_PLIC_IRQ_OK"]
    assert report["machine"] == "usp-rv64"
    assert report["simulated_time_seconds"] is None


def test_exported_ir_json_schema(custom_project):
    from jsonschema import ValidationError, validate

    from socsim.ipxact import SCHEMAS

    schema = json.loads((SCHEMAS.parent / "ir-v2.json").read_text())
    document = json.loads(json.dumps(load_project(custom_project).ir.to_dict()))
    validate(document, schema)
    document["backend_specific_type"] = "not canonical IR"
    with pytest.raises(ValidationError, match="Additional properties"):
        validate(document, schema)


@pytest.mark.parametrize("bad", [[], True, {"bad": "value"}])
def test_overlay_schema_rejects_wrong_types(custom_project, bad):
    edit_hardware(custom_project, lambda d: d["interrupts"][-1].update(source=bad))
    with pytest.raises(ContractError, match="E_HARDWARE_SCHEMA"):
        load_project(custom_project)


@pytest.mark.integration
def test_real_linux_userspace_and_poweroff(tmp_path):
    generated = os.environ.get("USP_GENERATED_DIR")
    if not generated or not (Path(generated) / "linux-build.json").exists():
        if os.environ.get("USP_REQUIRE_LINUX") == "1":
            pytest.fail("Linux build required for this acceptance gate")
        pytest.skip("Linux source-build integration is separate from quick core tests")
    from socsim.pipeline import run_custom

    report = run_custom(
        load_project(ROOT / "examples/rv64-minimal"),
        Path(generated).resolve(),
        tmp_path / "linux",
        osname="linux",
    )
    assert report["status"] == "PASSED"
    assert report["assertions"]["USP_LINUX_USERSPACE_OK"]
    assert report["returncode"] == 0


@pytest.mark.integration
def test_custom_runtime_timeout_is_reported(tmp_path):
    generated = os.environ.get("USP_GENERATED_DIR")
    if not generated:
        pytest.skip("custom source build required")
    report = run_zephyr(
        load_project(ROOT / "examples/rv64-minimal"),
        Path(generated).resolve(),
        tmp_path / "timeout",
        timeout=0.000001,
    )
    assert report["status"] == "FAILED"
    assert report["timed_out"]


@pytest.mark.parametrize(
    "field,value", [("schema_version", []), ("profile", []), ("profile", True)]
)
def test_bad_manifest_types_return_diagnostic(custom_project, field, value):
    path = custom_project / "simulation.yaml"
    data = yaml.safe_load(path.read_text())
    data[field] = value
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ContractError):
        load_project(custom_project)


def test_generated_software_contract_and_uart_clock(custom_project, tmp_path):
    out = generate(load_project(custom_project), tmp_path / "generated")
    contract = json.loads((out / "software-contract.json").read_text())
    assert contract["uart_clock_hz"] == 3686400
    assert "230400, serial_hd(0)" in (out / "machine.c").read_text()
    assert "#define USP_UART_BASE 0x11000000UL" in (out / "soc.h").read_text()
    assert "0x80000000" in (out / "memory.ld").read_text()
    assert (
        "clock-frequency = <3686400>"
        in (out / "zephyr/boards/usp/usp_rv64/usp_rv64.dts").read_text()
    )
    assert (out / "yocto/meta-usp/conf/machine/usp-rv64.conf").is_file()


def test_custom_toolchain_pin_cannot_silently_fall_back(custom_project):
    path = custom_project / "toolchain.lock.yaml"
    data = yaml.safe_load(path.read_text())
    data["qemu_version"] = "11.0.0"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ContractError, match="QEMU 10.0.0 exactly"):
        resolve(load_project(custom_project))
