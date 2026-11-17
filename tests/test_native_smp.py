"""Configurable native topology, real multi-hart execution and Linux acceptance."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from socsim.domain import ContractError
from socsim.soc_native import accept, generate, verify
from socsim.soc_spec import cpu_instances, load_spec

ROOT = Path(__file__).resolve().parents[1]


def test_native_multihart_ir_and_64_bit_dts_are_design_data(tmp_path):
    out = tmp_path / "machine"
    result = generate(ROOT / "specs/AI_SOC-smp.yaml", out, "both")
    spec = load_spec(out / "spec.yaml")
    ir = json.loads((out / "soc-ir.json").read_text())
    assert result["instances"] == 16
    assert len(ir["address_spaces"]) == 4
    assert {a["initiator"] for a in ir["address_spaces"]} == set(cpu_instances(spec))
    assert len({a["name"] for a in ir["address_spaces"]}) == 4
    bindings = json.loads((out / "engine-bindings.json").read_text())
    for engine in ("qemu", "gem5"):
        assert {b["instance"] for b in bindings[engine][:4]} == set(cpu_instances(spec))
    dts = (out / "soc.dts").read_text()
    assert "#address-cells = <2>" in dts and "0x1 0x80000000" in dts
    assert all(f"cpu@{h}" in dts and f"&__usp_hlic{h} 7" in dts for h in range(4))
    graph = json.loads((out / "hardware-graph.json").read_text())
    assert graph["cpu_clock_hz"] == 800000000 and graph["memory_latency_ns"] == 25


@pytest.mark.parametrize(
    "field,value",
    [
        ("harts", 0),
        ("harts", 9),
        ("harts", True),
        ("harts", 1.5),
        ("clock_hz", 0),
        ("memory_latency_ns", 0),
    ],
)
def test_invalid_topology_rejected_before_generation(tmp_path, field, value):
    spec = yaml.safe_load((ROOT / "specs/AI_SOC-smp.yaml").read_text())
    spec["cpu"][field] = value
    source = tmp_path / "spec.yaml"
    source.write_text(yaml.safe_dump(spec))
    out = tmp_path / "generated"
    with pytest.raises(ContractError):
        generate(source, out, "both")
    assert not out.exists()


def test_hart_identity_cannot_collide_with_device_identity(tmp_path):
    spec = yaml.safe_load((ROOT / "specs/AI_SOC-smp.yaml").read_text())
    spec["devices"][-1]["name"] = spec["cpu"]["name"] + "_hart1"
    source = tmp_path / "spec.yaml"
    source.write_text(yaml.safe_dump(spec))
    with pytest.raises(ContractError, match="duplicate"):
        load_spec(source)


@pytest.mark.parametrize("soc,harts", [("AI_SOC", 4), ("AIZ_SOC", 2)])
@pytest.mark.parametrize("software", ["baremetal", "linux"])
def test_actual_native_smp_same_ir_elf_and_boot_on_both_engines(tmp_path, soc, harts, software):
    if os.environ.get("USP_REQUIRE_NATIVE_SMP") != "1":
        pytest.skip("requires generated multi-hart machines, native gem5 and pinned Linux build")
    generated = Path(os.environ[f"USP_NATIVE_{soc}_SMP_BUILD"])
    assert load_spec(generated / "spec.yaml") == load_spec(ROOT / f"specs/{soc}-smp.yaml")
    out = tmp_path / "accept"
    report = accept(
        generated,
        Path(os.environ["USP_IP_MODELS_DIR"]),
        Path(os.environ["USP_NATIVE_GEM5_BUILD"]),
        out,
        180,
        software,
    )
    assert report["status"] == verify(out)["status"] == "PASSED"
    gem5 = json.loads((out / "gem5/report.json").read_text())
    assert len(gem5["performance"]["per_hart"]) == harts
    assert all(v["instructions"] > 0 for v in gem5["performance"]["per_hart"])
    for engine in ("qemu", "gem5"):
        child = json.loads((out / engine / "report.json").read_text())
        assert child["suite"] == (
            "native-linux-boot" if software == "linux" else "generated-conformance"
        )
        if software == "baremetal":
            assert all(child["checks"][f"USP_NATIVE_HART_OK:{h}"] for h in range(harts))
        else:
            assert child["checks"]["USP_LINUX_USERSPACE_OK"]
    if software == "linux":
        path = out / "gem5" / (load_spec(generated / "spec.yaml")["boot"]["console"] + ".uart.log")
        path.write_text("truncated boot output")
        with pytest.raises(ContractError):
            verify(out)


@pytest.mark.parametrize("cpus", [1, 8])
def test_actual_qemu_rejects_a_different_hart_topology(tmp_path, cpus):
    if os.environ.get("USP_REQUIRE_NATIVE_SMP") != "1":
        pytest.skip("requires source-built SMP machine")
    generated = Path(os.environ["USP_NATIVE_AI_SOC_SMP_BUILD"])
    result = subprocess.run(
        [
            str(generated / "qemu-build/qemu-system-riscv64"),
            "-M",
            "usp-native",
            "-smp",
            str(cpus),
            "-m",
            "65536K",
            "-display",
            "none",
            "-monitor",
            "none",
            "-kernel",
            str(generated / "firmware.elf"),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    (tmp_path / "wrong-topology.log").write_text(result.stdout + result.stderr)
    assert result.returncode != 0
    if cpus > 4:
        assert f"Invalid SMP CPUs {cpus}." in result.stderr
        assert "max CPUs supported" in result.stderr
    else:
        assert "4 harts" in result.stderr


@pytest.mark.parametrize("artifact", ["linux/soc.dtb", "linux/kernel/.config", "linux/workload"])
def test_actual_linux_input_corruption_is_rejected(tmp_path, artifact):
    if os.environ.get("USP_REQUIRE_NATIVE_SMP") != "1":
        pytest.skip("requires source-built native Linux")
    from socsim.soc_native_linux import verify as verify_linux

    source = Path(os.environ["USP_NATIVE_AI_SOC_SMP_BUILD"])
    record = json.loads((source / "linux-build.json").read_text())
    for filename in ["manifest.json", "linux-build.json", *record["files"]]:
        target = tmp_path / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / filename, target)
    (tmp_path / artifact).write_bytes(b"corrupted")
    with pytest.raises(ContractError, match="modified native Linux"):
        verify_linux(tmp_path)


@pytest.mark.parametrize("case", ["manifest", "missing_files"])
def test_actual_linux_cache_mutation_is_rejected(tmp_path, case):
    if os.environ.get("USP_REQUIRE_NATIVE_SMP") != "1":
        pytest.skip("requires a real native Linux build")
    from socsim.soc_native_linux import verify as verify_linux

    source = Path(os.environ["USP_NATIVE_AI_SOC_SMP_BUILD"])
    record = json.loads((source / "linux-build.json").read_text())
    for filename in [*record["files"], "manifest.json", "linux-build.json"]:
        destination = tmp_path / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / filename, destination)
    verify_linux(tmp_path)
    if case == "manifest":
        manifest = tmp_path / "manifest.json"
        manifest.write_text(manifest.read_text() + "\n")
    else:
        record["files"] = {}
        (tmp_path / "linux-build.json").write_text(json.dumps(record))
    with pytest.raises(ContractError):
        verify_linux(tmp_path)


def test_actual_maximum_eight_harts_and_shared_ip_transactions(tmp_path):
    if os.environ.get("USP_REQUIRE_NATIVE_SMP") != "1":
        pytest.skip("requires native maximum-hart boundary build")
    generated = Path(os.environ["USP_NATIVE_MAX_HART_BUILD"])
    assert load_spec(generated / "spec.yaml")["cpu"]["harts"] == 8
    out = tmp_path / "eight-hart"
    result = accept(
        generated,
        Path(os.environ["USP_IP_MODELS_DIR"]),
        Path(os.environ["USP_NATIVE_GEM5_BUILD"]),
        out,
        60,
    )
    assert result["status"] == verify(out)["status"] == "PASSED"
    gem5 = json.loads((out / "gem5/report.json").read_text())
    assert len(gem5["performance"]["per_hart"]) == 8
    assert all(v["instructions"] > 0 for v in gem5["performance"]["per_hart"])
    for engine in ["qemu", "gem5"]:
        report = json.loads((out / engine / "report.json").read_text())
        assert all(report["checks"][f"USP_NATIVE_HART_OK:{hart}"] for hart in range(8))
