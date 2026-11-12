import json
import os
from pathlib import Path

import pytest
import yaml

from socsim.cpu_platform import (
    cpu_selection,
    load_platform,
    platform_plan,
    run_platform,
    verify_platform,
)
from socsim.domain import ContractError
from socsim.ipxact import digest
from socsim.layers import software
from socsim.project import load_project

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["qemu-rv64", "qemu-max", "gem5-timing", "gem5-atomic"])
def test_registered_cpu_engine_adapters_are_explicit(name):
    config = load_platform(ROOT / "platforms" / (name + ".yaml"))
    plan = platform_plan(load_project(ROOT / "examples/rv64-ips"), config)
    assert plan["status"] == "AVAILABLE_ADAPTER"
    assert plan["cpu"]["provider"] == config["cpu"]["provider"]
    assert plan["cpu"]["model"] == config["cpu"]["model"]
    assert len(plan["peripheral_bindings"]) == 8


@pytest.mark.parametrize(
    "provider,model,engine",
    [
        ("chipyard", "Rocket", "gem5"),
        ("chipyard", "BOOM", "qemu"),
        ("custom-rtl", "MyCore", "qemu"),
        ("qemu", "rv64", "gem5"),
        ("gem5", "timing-simple", "qemu"),
    ],
)
def test_unimplemented_cpu_combinations_fail_without_substitution(provider, model, engine):
    config = {"engine": engine, "cpu": {"provider": provider, "model": model}}
    assert cpu_selection(config, engine, require=False)["status"] == "NEEDS_ADAPTER"
    with pytest.raises(ContractError, match="substitution"):
        cpu_selection(config, engine)


@pytest.mark.parametrize("case", ["extra", "clock", "command", "cost", "duplicate"])
def test_invalid_platform_configuration_rejected(tmp_path, case):
    config = load_platform(ROOT / "platforms/gem5-timing.yaml")
    if case == "extra":
        config["exec"] = "shell"
    if case == "clock":
        config["cpu"]["clock_hz"] = 0
    if case == "command":
        config["cpu"]["model"] = "rv64; shell"
    if case == "cost":
        config["qemu_cost_model"] = {"base_cpi": 1, "memory_extra_cycles": 1}
    if case == "duplicate":
        config["workloads"] = ["linux", "linux"]
    p = tmp_path / "platform.yaml"
    p.write_text(yaml.safe_dump(config))
    with pytest.raises(ContractError):
        load_platform(p)


def test_selected_workloads_are_configuration_data(tmp_path):
    config = load_platform(ROOT / "platforms/qemu-rv64.yaml")
    config["workloads"] = ["baremetal"]
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(config))
    assert load_platform(p)["workloads"] == ["baremetal"]


def test_external_artifact_must_be_a_valid_matching_elf(tmp_path):
    # Validation happens before an engine/model starts; arbitrary blobs are not firmware.
    from socsim.pipeline import generate

    project = load_project(ROOT / "examples/rv64-ips")
    generated = generate(project, tmp_path / "generated")
    payload = tmp_path / "payload"
    payload.write_bytes(b"not an ELF")
    with pytest.raises(ContractError, match="external ELF"):
        software(
            project,
            generated,
            "baremetal",
            {"elf": str(payload), "sha256": digest(payload), "expected_markers": []},
        )


def test_actual_external_elf_manifest_uses_real_cpu_execution(tmp_path):
    if os.environ.get("USP_REQUIRE_SW_LAYERS") != "1":
        pytest.skip("external native firmware execution explicitly required")
    generated = Path(os.environ.get("USP_IP_GENERATED_DIR", str(ROOT / "build/rv64-ips")))
    config = load_platform(ROOT / "platforms/qemu-rv64.yaml")
    config["workloads"] = ["baremetal"]
    image = generated / "baremetal/firmware.elf"
    config["artifacts"] = {
        "baremetal": {
            "elf": str(image),
            "sha256": digest(image),
            "expected_markers": ["USP_BAREMETAL_BOOT_OK", "USP_TEST_PASS"],
        }
    }
    r = run_platform(
        load_project(ROOT / "examples/rv64-ips"),
        config,
        generated,
        Path(os.environ["USP_IP_MODELS_DIR"]),
        Path(os.environ["USP_UNIFIED_GEM5_BUILD"]),
        Path(os.environ["USP_COUNTER_BUILD_DIR"]),
        tmp_path / "run",
    )
    assert r["status"] == "PASSED"
    verify_platform(tmp_path / "run")
    row = json.loads((tmp_path / "run/baremetal/report.json").read_text())
    assert row["software_build"]["kind"] == "external-artifact"
    assert row["performance"]["counters"]["executed_instructions"] > 0


@pytest.mark.parametrize("name", ["qemu-max", "gem5-atomic"])
def test_actual_alternative_cpu_models_execute_all_selected_software_layers(tmp_path, name):
    if os.environ.get("USP_REQUIRE_SW_LAYERS") != "1":
        pytest.skip("actual configurable CPU/software matrix explicitly required")
    config = load_platform(ROOT / "platforms" / (name + ".yaml"))
    generated = Path(os.environ.get("USP_IP_GENERATED_DIR", str(ROOT / "build/rv64-ips")))
    models = Path(os.environ.get("USP_IP_MODELS_DIR", str(ROOT / "build/source-ip-models")))
    bridge = Path(os.environ.get("USP_UNIFIED_GEM5_BUILD", str(ROOT / "build/unified-gem5")))
    counter = Path(os.environ.get("USP_COUNTER_BUILD_DIR", str(ROOT / "build/performance-counter")))
    r = run_platform(
        load_project(ROOT / "examples/rv64-ips"),
        config,
        generated,
        models,
        bridge,
        counter,
        tmp_path / "run",
    )
    assert r["status"] == "PASSED", r
    verify_platform(tmp_path / "run")
    assert set(r["results"]) == set(config["workloads"])
    for layer in config["workloads"]:
        actual = json.loads((tmp_path / "run" / layer / "report.json").read_text())
        assert actual["cpu_selection"]["model"] == config["cpu"]["model"]
