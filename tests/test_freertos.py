import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from socsim.cli import main
from socsim.domain import ContractError
from socsim.freertos import (
    MARKERS,
    acceptance,
    build_freertos,
    generate_bsp,
    run_freertos,
    verify_freertos,
)
from socsim.pipeline import generate
from socsim.project import load_project

ROOT = Path(__file__).resolve().parents[1]


def test_bsp_uses_ir_timer_placement_and_irq(tmp_path):
    root = tmp_path / "project"
    shutil.copytree(ROOT / "examples/rv64-minimal", root)
    manifest = root / "simulation.yaml"
    data = yaml.safe_load(manifest.read_text())
    data["placements"]["clint"] = 0x3000000
    manifest.write_text(yaml.safe_dump(data))
    hardware = root / "hardware.yaml"
    data = yaml.safe_load(hardware.read_text())
    next(r for r in data["interrupts"] if r["source"] == "uart")["line"] = 17
    hardware.write_text(yaml.safe_dump(data))
    project = load_project(root)
    out = generate(project, tmp_path / "generated")
    before = (out / "manifest.json").read_bytes()
    bsp = generate_bsp(project, out)
    config = (bsp / "FreeRTOSConfig.h").read_text()
    assert "configMTIME_BASE_ADDRESS 0x300bff8" in config
    assert "configMTIMECMP_BASE_ADDRESS 0x3004000" in config
    assert "#define UART_SOURCE 17" in (bsp / "app.c").read_text()
    assert (bsp / "soc.h").read_bytes() == (out / "soc.h").read_bytes()
    assert (bsp / "memory.ld").read_bytes() == (out / "memory.ld").read_bytes()
    assert (out / "manifest.json").read_bytes() == before


def test_hybrid_is_explicitly_unsupported(tmp_path):
    project = load_project(ROOT / "examples/rv64-hybrid")
    out = generate(project, tmp_path / "hybrid")
    with pytest.raises(ContractError, match="non-hybrid"):
        generate_bsp(project, out)


def test_wrong_kernel_revision_is_rejected(tmp_path):
    project = load_project(ROOT / "examples/rv64-minimal")
    out = generate(project, tmp_path / "generated")
    with pytest.raises(ContractError, match="revision mismatch"):
        build_freertos(project, out, ROOT, "riscv64-unknown-elf-")


@pytest.mark.parametrize(
    "serial",
    [
        "\n".join(MARKERS),
        "\n".join(MARKERS) + "\nUSP_FREERTOS_METRICS ticks=30 timer_irqs=0 switches=32 "
        "worker0=400 worker1=400 uart_irqs=1\n",
        "\n".join(MARKERS) + "\nUSP_FREERTOS_METRICS ticks=30 timer_irqs=30 switches=32 "
        "worker0=400 worker1=0 uart_irqs=1\n",
        "garbage USP_TEST_PASS trailing text",
    ],
)
def test_markers_cannot_replace_guest_counter_evidence(serial):
    checks, _ = acceptance(serial)
    assert not all(checks.values())


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), 301])
def test_invalid_timeout_before_starting_guest(tmp_path, timeout):
    with pytest.raises(ContractError, match="timeout"):
        run_freertos(None, tmp_path, tmp_path / "output", timeout)
    assert not (tmp_path / "output").exists()


def test_freertos_cli_requires_source(tmp_path, capsys):
    rc = main(
        [
            "software",
            "build",
            str(ROOT / "examples/rv64-minimal"),
            "--os",
            "freertos",
            "--generated",
            str(tmp_path),
            "--cross-compile",
            "riscv64-unknown-elf-",
        ]
    )
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["message"] == "--source-freertos is required"


@pytest.fixture
def native_profile():
    generated = os.environ.get("USP_GENERATED_DIR")
    if not generated or not (Path(generated) / "freertos-build.json").is_file():
        if os.environ.get("USP_REQUIRE_FREERTOS") == "1":
            pytest.fail("FreeRTOS build required for this acceptance gate")
        pytest.skip("FreeRTOS source-build acceptance is an optional developer gate")
    return load_project(ROOT / "examples/rv64-minimal"), Path(generated).resolve()


def copy_hardware(project, native, destination):
    """Copy only verified generated artifacts and the actual custom QEMU binary."""
    from socsim.pipeline import verify_generated

    manifest = verify_generated(project, native)
    for name in (
        *manifest["files"],
        "manifest.json",
        "qemu-build.json",
        "qemu-build/qemu-system-riscv64",
    ):
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(native / name, path)
    return destination


@pytest.mark.integration
def test_real_freertos_preemption_registers_timer_and_uart_irq(native_profile, tmp_path):
    project, native = native_profile
    report = run_freertos(project, native, tmp_path / "run")
    assert report["status"] == "PASSED"
    assert report["returncode"] == 0
    assert all(report["assertions"].values())
    metrics = report["guest_metrics"]
    assert metrics["timer_irqs"] >= 30 and metrics["switches"] >= 10
    assert metrics["worker0"] and metrics["worker1"] and metrics["uart_irqs"] == 1
    assert report["simulated_time_seconds"] is None
    assert (tmp_path / "run/FreeRTOS-LICENSE.md").is_file()
    with pytest.raises(FileExistsError):
        run_freertos(project, native, tmp_path / "run")


@pytest.mark.integration
@pytest.mark.parametrize("case", ["assert-failure", "tick-disabled"])
def test_real_guest_failures_are_failed_results(native_profile, tmp_path, case):
    project, native = native_profile
    source = os.environ.get("USP_FREERTOS_SOURCE")
    compiler = os.environ.get("CROSS_COMPILE", "riscv64-unknown-elf-")
    if not source or not shutil.which(compiler + "gcc"):
        if os.environ.get("USP_REQUIRE_FREERTOS") == "1":
            pytest.fail("pinned FreeRTOS source/compiler required for negative guest tests")
        pytest.skip("pinned source/compiler needed to build actual negative guests")
    out = copy_hardware(project, native, tmp_path / "generated")
    build_freertos(project, out, Path(source), compiler, case)
    report = run_freertos(
        project, out, tmp_path / "run", timeout=2 if case == "tick-disabled" else 10
    )
    assert report["status"] == "FAILED"
    serial = (tmp_path / "run/stdout.log").read_text()
    assert "USP_FREERTOS_BOOT_OK" in serial
    if case == "assert-failure":
        assert report["returncode"] == 1
        assert "USP_FREERTOS_FAIL" in serial
        assert not report["timed_out"]
        assert report["assertions"]["USP_FREERTOS_PREEMPTION_OK"]
    else:
        assert report["timed_out"] and report["returncode"] < 0
        assert not report["assertions"]["USP_FREERTOS_TIMER_IRQ_OK"]


@pytest.mark.integration
@pytest.mark.parametrize("changed", ["freertos/firmware.elf", "freertos/bsp/FreeRTOSConfig.h"])
def test_modified_software_is_rejected_before_execution(native_profile, tmp_path, changed):
    project, native = native_profile
    out = copy_hardware(project, native, tmp_path / "generated")
    shutil.copytree(native / "freertos", out / "freertos")
    shutil.copyfile(native / "freertos-build.json", out / "freertos-build.json")
    with (out / changed).open("ab") as stream:
        stream.write(b"modified")
    with pytest.raises(ContractError, match="modified FreeRTOS"):
        verify_freertos(project, out)


@pytest.mark.integration
@pytest.mark.parametrize(
    "field,value",
    [
        ("software_adapter", {}),
        ("bsp_files", {"../soc.h": "invalid"}),
        ("test_case", []),
        ("kernel_source_commit", "unverified-revision"),
    ],
)
def test_invalid_software_record_is_rejected(native_profile, tmp_path, field, value):
    project, native = native_profile
    out = copy_hardware(project, native, tmp_path / "generated")
    record = json.loads((native / "freertos-build.json").read_text())
    record[field] = value
    (out / "freertos-build.json").write_text(json.dumps(record))
    with pytest.raises(ContractError, match="stale FreeRTOS|incomplete FreeRTOS"):
        verify_freertos(project, out)
