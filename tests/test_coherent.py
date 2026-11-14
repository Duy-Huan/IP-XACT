import json
import os
from pathlib import Path

import pytest
import yaml

from socsim.coherent import contract, run, verify
from socsim.domain import ContractError
from socsim.project import load_project

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["rv64-ips", "AI_SOC", "AIZ_SOC"])
def test_coherent_lowering_preserves_base_and_declares_all_new_owners(name):
    path = "examples/rv64-ips" if name == "rv64-ips" else f"systems/{name}/bringup"
    project = load_project(ROOT / path)
    original = project.ir.to_dict()
    board, ir = contract(project, ROOT / f"platforms/{name}-coherent.yaml")
    assert project.ir.to_dict() == original
    assert len(ir.instances) == len(project.ir.instances) + 5
    assert {s.initiator for s in ir.address_spaces} >= {"rv64", "rv64_1", "dma"}
    for s in ir.address_spaces:
        if s.initiator in {"rv64", "rv64_1"}:
            assert "dma" in s.targets
        if s.initiator == "dma":
            assert s.targets == ("ram",)
    assert board["harts"] == 2
    assert next(i for i in ir.interrupts if i.source == "dma").line == 18


@pytest.mark.parametrize(
    "change",
    [
        {"harts": 1},
        {"harts": True},
        {"schema_version": "future"},
        {"ignored": 1},
        {"dma": {"base": 0x18000001, "irq": 18}},
        {"dma": {"base": 0x80000000, "irq": 18}},
        {"dma": {"base": 0x18000000, "irq": 12}},
        {"dma": {"base": 0x18000000, "irq": 32}},
        {"dma": {"base": 0x18000000, "irq": True}},
        {"dma": {"base": 1 << 64, "irq": 18}},
        {"dma": {"base": -4096, "irq": 18}},
    ],
)
def test_coherent_invalid_hardware_contract_rejected_before_execution(tmp_path, change):
    data = yaml.safe_load((ROOT / "platforms/rv64-ips-coherent.yaml").read_text())
    data.update(change)
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ContractError):
        contract(load_project(ROOT / "examples/rv64-ips"), path)


def actual(tmp_path, name, negative=False):
    if os.environ.get("USP_REQUIRE_COHERENT") != "1":
        pytest.skip("actual native coherent gem5 build and source-IP models required")
    path = "examples/rv64-ips" if name == "rv64-ips" else f"systems/{name}/bringup"
    generated = (
        Path(os.environ["USP_IP_GENERATED_DIR"])
        if name == "rv64-ips"
        else Path(os.environ["USP_DUAL_BUILD_ROOT"]) / name
    )
    out = tmp_path / "run"
    result = run(
        load_project(ROOT / path),
        generated,
        Path(os.environ["USP_IP_MODELS_DIR"]),
        Path(os.environ["USP_COHERENT_BUILD"]),
        Path(os.environ["USP_GEM5_SOURCE"]),
        ROOT / f"platforms/{name}-coherent.yaml",
        out,
        negative=negative,
    )
    return out, result


@pytest.mark.parametrize("name", ["rv64-ips", "AI_SOC", "AIZ_SOC"])
def test_actual_multihart_dirty_cache_dma_irq_and_source_ips(tmp_path, name):
    out, r = actual(tmp_path, name)
    assert r["status"] == verify(out)["status"] == "PASSED"
    assert verify(Path(os.path.relpath(out)))["status"] == "PASSED"
    assert r["dma_transfers"] == 4
    assert r["rejected_descriptors"] == 2
    assert r["network"]["system.ruby.network.average_hops"] > 0
    for cpu in r["cpus"]:
        assert cpu["instructions"] > 1000 and cpu["cycles"] > cpu["instructions"]
    # The actual successful guest cannot conceal a memory path changed to bypass Ruby.
    path = out / "config.json"
    original = path.read_text()
    c = json.loads(original)
    c["system"]["dma"]["dma"]["peer"] = "system.bus.cpu_side_ports[0]"
    path.write_text(json.dumps(c))
    with pytest.raises(ContractError, match="modified coherent SoC artifact"):
        verify(out)
    path.write_text(original)
    path = out / "report.json"
    original = path.read_text()
    c = json.loads(original)
    del c["files"]["firmware.elf"]
    path.write_text(json.dumps(c))
    with pytest.raises(ContractError, match="incomplete coherent"):
        verify(out)
    path.write_text(original)
    assert verify(out)["status"] == "PASSED"


def test_actual_corrupted_dma_destination_is_guest_failure(tmp_path):
    out, r = actual(tmp_path, "rv64-ips", negative=True)
    assert r["status"] == verify(out)["status"] == "FAILED"
    assert r["checks"]["dma_through_ruby"] and r["checks"]["dma_sequences"]
    assert not r["checks"]["guest_exit"]
    assert not r["checks"]["no_guest_failure"]
    assert "USP_COHERENT_FAIL" in (out / "uart.log").read_text()
    assert not json.loads((out / "process.json").read_text())["timed_out"]
