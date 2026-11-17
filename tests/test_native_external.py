"""External adapter qualification; no inferred behavior and actual guest MMIO/IRQ tests."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from socsim.domain import ContractError
from socsim.ipxact import digest
from socsim.soc_native import accept, generate, main, run, verify_generated
from socsim.soc_spec import load_spec

ROOT = Path(__file__).resolve().parents[1]


def fixture_spec(tmp_path):
    data = load_spec(ROOT / "specs/AI_SOC-native.yaml")
    binary = tmp_path / "models/local-model"
    binary.parent.mkdir()
    binary.write_bytes(b"configuration fixture; never executed")
    binary.chmod(0o755)
    data["external_models"] = {
        "vendor.new_ip": {
            "vlnv": "vendor:library:new_ip:1.0",
            "protocol": "usp-mmio32-v1",
            "executable": "models/local-model",
            "sha256": digest(binary),
            "license": "LicenseRef-Test",
            "compatible": "vendor,new-ip",
        }
    }
    data["devices"].append(
        {
            "name": "new_peripheral",
            "model": "vendor.new_ip",
            "base": 0x190000000,
            "size": 4096,
            "irq": 20,
        }
    )
    path = tmp_path / "spec.yaml"
    path.write_text(yaml.safe_dump(data))
    return path, data, binary


def test_arbitrary_vlnv_reaches_real_ipxact_ir_bindings_and_checked_executable(
    tmp_path, monkeypatch
):
    source, data, binary = fixture_spec(tmp_path)
    out = tmp_path / "generated"
    generate(source, out, "both")
    monkeypatch.chdir(tmp_path)
    assert verify_generated(Path("generated"))["name"] == data["name"]
    ir = json.loads((out / "soc-ir.json").read_text())
    instance = next(i for i in ir["instances"] if i["name"] == "new_peripheral")
    assert instance["vlnv"] == "vendor:library:new_ip:1.0"
    assert instance["windows"][0]["base"] == 0x190000000
    assert digest(out / "models/local-model") == digest(binary)
    bindings = json.loads((out / "engine-bindings.json").read_text())
    assert all(any(b["instance"] == "new_peripheral" for b in rows) for rows in bindings.values())
    with pytest.raises(ContractError, match="explicit user firmware"):
        run(out, None, tmp_path / "run")
    (out / "models/local-model").write_bytes(b"changed executable")
    with pytest.raises(ContractError, match="modified generated"):
        verify_generated(out)


@pytest.mark.parametrize(
    "case",
    [
        "hash",
        "missing",
        "escape",
        "namespace",
        "protocol",
        "vlnv",
        "aperture",
        "irq",
        "reserved",
        "design_id",
    ],
)
def test_external_binding_failure_precedes_output_creation(tmp_path, case):
    path, data, binary = fixture_spec(tmp_path)
    descriptor = data["external_models"]["vendor.new_ip"]
    if case == "hash":
        descriptor["sha256"] = "0" * 64
    elif case == "missing":
        binary.unlink()
    elif case == "escape":
        descriptor["executable"] = "models/../../outside"
    elif case == "namespace":
        binary.rename(tmp_path / "manifest.json")
        descriptor["executable"] = "models/../manifest.json"
    elif case == "protocol":
        descriptor["protocol"] = "axi-inferred"
    elif case == "vlnv":
        descriptor["vlnv"] = "unknown"
    elif case == "aperture":
        data["devices"][-1]["size"] = 8192
    elif case == "reserved":
        data["external_models"] = {"cpu": descriptor}
        data["devices"][-1]["model"] = "cpu"
    elif case == "design_id":
        data["external_models"] = {"design": descriptor}
        data["devices"][-1]["model"] = "design"
    else:
        data["devices"][-1]["irq"] = data["devices"][6]["irq"]
    path.write_text(yaml.safe_dump(data))
    out = tmp_path / "generated"
    with pytest.raises(ContractError):
        generate(path, out, "both")
    assert not out.exists()


def test_native_cli_registers_every_subcommand_without_conflicting_options():
    with pytest.raises(SystemExit) as status:
        main(["--help"])
    assert status.value.code == 0


def example():
    if os.environ.get("USP_REQUIRE_NATIVE_EXTERNAL") != "1":
        pytest.skip("requires compiled external SystemC model, custom machine and firmware")
    return Path(os.environ["USP_NATIVE_EXTERNAL_ROOT"])


def test_actual_new_systemc_ip_and_user_irq_driver_on_qemu_and_gem5(tmp_path):
    root = example()
    out = tmp_path / "run"
    report = accept(
        root / "generated",
        None,
        Path(os.environ["USP_NATIVE_GEM5_BUILD"]),
        out,
        60,
        firmware=root / "counter.elf",
    )
    assert report["status"] == "PASSED"
    for engine in ("qemu", "gem5"):
        path = out / engine
        assert "USP_EXTERNAL_IRQ_OK" in (path / "debug_console.uart.log").read_text()
        tx = [
            json.loads(line)
            for line in (path / "custom_counter.trace.jsonl").read_text().splitlines()
        ]
        assert any(t["op"] == 0 and t["offset"] == 0 and t["value"] == 41 for t in tx)
        assert any(t["irq"] == 1 for t in tx)
        assert tx[-1]["systemc_time_ps"] > 0
        assert json.loads((path / "report.json").read_text())["suite"] == "user-firmware-execution"


@pytest.mark.parametrize("engine", ["qemu", "gem5"])
def test_actual_external_bus_error_cannot_pass(tmp_path, engine):
    root = example()
    source = tmp_path / "negative.c"
    source.write_text(
        (ROOT / "models/peripherals/counter-firmware.c")
        .read_text()
        .replace(
            "R32(USP_INSTANCE_CUSTOM_COUNTER_BASE)=41;",
            "R32(USP_INSTANCE_CUSTOM_COUNTER_BASE+8)=41;",
        )
    )
    fw = root / "generated/firmware"
    elf = tmp_path / "negative.elf"
    subprocess.run(
        [
            os.environ.get("CROSS_COMPILE", "riscv64-unknown-elf-") + "gcc",
            "-march=rv64imac_zicsr_zifencei",
            "-mabi=lp64",
            "-mcmodel=medany",
            "-msmall-data-limit=0",
            "-nostdlib",
            "-O2",
            "-mno-relax",
            "-I",
            str(fw),
            "-T",
            str(fw / "linker.ld"),
            str(fw / "start.S"),
            str(source),
            "-o",
            str(elf),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    out = tmp_path / "run"
    report = run(
        root / "generated", None, out, 30, elf, engine, Path(os.environ["USP_NATIVE_GEM5_BUILD"])
    )
    assert report["status"] == "FAILED"
    assert report["checks"]["engine_exit"] is False
    tx = [
        json.loads(line) for line in (out / "custom_counter.trace.jsonl").read_text().splitlines()
    ]
    assert any(t["status"] != 0 for t in tx)
