"""Common-IR/direct-IP-XACT qualification, including real backend execution."""

import json
import os
import shutil
from pathlib import Path

import pytest
import yaml
from lxml import etree

from socsim.domain import ContractError
from socsim.native_ir import read_ir
from socsim.soc_native import accept, generate, verify, verify_generated

ROOT = Path(__file__).resolve().parents[1]


def test_direct_hierarchy_preserves_leaf_identity_and_explicit_aliases(tmp_path):
    out = tmp_path / "generated"
    generate(ROOT / "specs/ipxact/AI_SOC-hierarchy/native.yaml", out, "both", "ipxact")
    ir = read_ir(out / "native-ir.json")
    gcd = next(r for r in ir.regions if r.instance.name == "accelerator_b")
    assert gcd.instance.hierarchy_path == ("cluster", "accelerator_b")
    assert gcd.instance.windows[0].base == 0x113001000
    assert len(ir.cpus) == 4 and len(ir.regions) == 12


@pytest.mark.parametrize("name", ["AI_SOC", "AIZ_SOC"])
def test_yaml_and_direct_xml_converge_to_the_same_immutable_hardware(name, tmp_path, monkeypatch):
    a, b = tmp_path / "yaml", tmp_path / "xml"
    generate(ROOT / f"specs/{name}-smp.yaml", a, "both")
    generate(ROOT / f"specs/ipxact/{name}/native.yaml", b, "both", "ipxact")
    ia, ib = read_ir(a / "native-ir.json"), read_ir(b / "native-ir.json")
    da, db = ia.to_dict(), ib.to_dict()
    assert da.pop("sources") != db.pop("sources")
    assert da == db
    assert ia.to_spec() == ib.to_spec()
    assert (a / "soc.dts").read_bytes() == (b / "soc.dts").read_bytes()
    assert (a / "firmware/soc.h").read_bytes() == (b / "firmware/soc.h").read_bytes()
    import socsim.soc_native as native

    monkeypatch.setattr(native, "load_spec", lambda *_: pytest.fail("backend re-read source YAML"))
    assert verify_generated(b) == ib.to_spec()


@pytest.mark.parametrize("case", ["binding", "width", "irq", "cpu", "unknown", "bus"])
def test_direct_intake_rejects_incompatible_contract_before_output(tmp_path, case):
    project = tmp_path / "input"
    shutil.copytree(ROOT / "specs/ipxact/AI_SOC", project)
    path = project / "native.yaml"
    data = yaml.safe_load(path.read_text())
    if case == "binding":
        data["bindings"].pop()
    elif case == "irq":
        next(b for b in data["bindings"] if b["model"] == "uart")["irq"] = 0
    elif case == "cpu":
        data["cpu"]["model"] = "chipyard.rocket"
    elif case == "unknown":
        data["translations"] = []
    else:
        p = project / "ipxact/uart.xml"
        tree = etree.parse(str(p))
        if case == "width":
            tree.find(".//{*}width").text = "16"
        else:
            ns = "{http://www.accellera.org/XMLSchema/IPXACT/1685-2022}"
            etree.SubElement(tree.getroot(), ns + "busInterfaces")
        tree.write(str(p))
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    out = tmp_path / "generated"
    with pytest.raises(ContractError):
        generate(path, out, "both", "ipxact")
    assert not out.exists()


@pytest.mark.parametrize("case", ["clock", "reset", "irq", "cpu_contract", "hart_id", "owner"])
def test_canonical_ir_rejects_inconsistent_graph_not_only_its_projection(tmp_path, case):
    out = tmp_path / "generated"
    generate(ROOT / "specs/AI_SOC-smp.yaml", out, "both")
    p = out / "native-ir.json"
    data = json.loads(p.read_text())
    if case == "clock":
        data["cpus"][1]["clock"] = "unknown_clock"
    elif case == "reset":
        data["resets"][0]["targets"].pop()
    elif case == "irq":
        data["interrupts"][0]["line"] = 0
    elif case == "cpu_contract":
        data["cpus"][1]["contract"]["mmu"] = "none"
    elif case == "hart_id":
        data["cpus"][1]["hart_id"] = 0
    else:
        data["address_spaces"][0]["initiator"] = data["regions"][0]["instance"]["name"]
    p.write_text(json.dumps(data))
    with pytest.raises(ContractError, match="E_NATIVE_IR"):
        read_ir(p)
    with pytest.raises(ContractError, match="modified generated native artifact"):
        verify_generated(out)


@pytest.mark.parametrize("name", ["AI_SOC", "AIZ_SOC"])
@pytest.mark.parametrize("software", ["baremetal", "linux"])
def test_actual_direct_ipxact_same_ir_and_elf_on_both_engines(
    tmp_path, name, software, monkeypatch
):
    if os.environ.get("USP_REQUIRE_NATIVE_IPXACT") != "1":
        pytest.skip("requires direct IP-XACT source-built machine and native Linux/gem5")
    generated = Path(os.environ[f"USP_NATIVE_{name}_IPXACT_BUILD"])
    assert json.loads((generated / "manifest.json").read_text())["input_format"] == "ipxact"
    import socsim.soc_native as native

    monkeypatch.setattr(native, "load_spec", lambda *_: pytest.fail("runtime re-read source YAML"))
    out = tmp_path / "run"
    r = accept(
        generated,
        Path(os.environ["USP_IP_MODELS_DIR"]),
        Path(os.environ["USP_NATIVE_GEM5_BUILD"]),
        out,
        180,
        software,
    )
    assert r["status"] == verify(out)["status"] == "PASSED"
    assert r["checks"]["same_canonical_ir"] and r["checks"]["same_elf"]
    harts = len(read_ir(generated / "native-ir.json").cpus)
    if software == "linux":
        for engine in ("qemu", "gem5"):
            text = "\n".join(p.read_text() for p in (out / engine).glob("*.uart.log"))
            assert f"smp: Brought up 1 node, {harts} CPUs" in text


def test_actual_direct_hierarchy_mmio_irqs_and_harts(tmp_path):
    if os.environ.get("USP_REQUIRE_NATIVE_IPXACT") != "1":
        pytest.skip("requires direct configured hierarchy native build")
    generated = Path(os.environ["USP_NATIVE_HIERARCHY_IPXACT_BUILD"])
    ir = read_ir(generated / "native-ir.json")
    assert any(r.instance.hierarchy_path == ("cluster", "accelerator_b") for r in ir.regions)
    out = tmp_path / "run"
    report = accept(
        generated,
        Path(os.environ["USP_IP_MODELS_DIR"]),
        Path(os.environ["USP_NATIVE_GEM5_BUILD"]),
        out,
        60,
    )
    assert report["status"] == verify(out)["status"] == "PASSED"
    assert report["checks"]["accelerator_b.same_commands_results"]
