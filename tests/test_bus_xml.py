"""Standard XML bus facts, lossless IR and actual dual-engine acceptance."""

import json
import os
import shutil
from pathlib import Path

import pytest
import yaml
from lxml import etree

from socsim.domain import ContractError
from socsim.ipxact import PREFIX, read_xml
from socsim.native_bus_export import export_bus
from socsim.native_flow import execute_flow, verify_build, verify_flow
from socsim.native_ir import read_ir
from socsim.soc_native import generate, main, verify_generated

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["AI_SOC", "AIZ_SOC"])
def test_xml_and_yaml_converge_to_identical_executable_hardware(tmp_path, name):
    a, b = tmp_path / "fabric", tmp_path / "xml"
    generate(ROOT / f"specs/fabric/{name}.yaml", a, "both", "fabric")
    generate(ROOT / f"specs/bus/{name}/native.yaml", b, "both", "ipxact")
    ia, ib = read_ir(a / "native-ir.json"), read_ir(b / "native-ir.json")
    assert ia.schema_version == "4" and ib.schema_version == "5"
    da, db = ia.to_dict(), ib.to_dict()
    for d in (da, db):
        d.pop("schema_version")
        d.pop("sources")
        d.pop("bus_graph", None)
    assert da == db and ia.to_spec() == ib.to_spec() == verify_generated(b)
    assert ib.bus_graph.definition.addressable
    assert len([n for n in ib.bus_graph.nodes if n.model == "decoder"]) == 3
    assert all(i.bits_in_lau == 8 for i in ib.bus_graph.interfaces)
    for file in ("soc.dts", "firmware/soc.h", "firmware/main.c", "firmware/linker.ld"):
        assert (a / file).read_bytes() == (b / file).read_bytes()


def test_export_round_trip_uses_xsd_valid_bus_xml_and_no_overlay_placements(tmp_path):
    out = tmp_path / "export"
    assert export_bus(ROOT / "specs/fabric/AI_SOC.yaml", out)["status"] == "EXPORTED"
    for p in (out / "ipxact").glob("*.xml"):
        read_xml(p)
    overlay = yaml.safe_load((out / "native.yaml").read_text())
    assert "placements" not in overlay and "routing" not in overlay
    assert overlay["space_aliases"]
    assert main(["validate", str(out / "native.yaml"), "--input-format", "ipxact"]) == 0
    assert (
        main(["validate", str(ROOT / "specs/fabric/AI_SOC.yaml"), "--input-format", "fabric"]) == 0
    )


@pytest.mark.parametrize(
    "case",
    [
        "unknown-bus",
        "definition",
        "abstraction",
        "endian",
        "units",
        "steering",
        "disconnect",
        "segment",
        "map-ref",
        "alias",
        "parameter",
        "cpu",
        "extra-overlay",
        "extra-xml",
        "width",
        "fanout",
        "initiator-base",
        "duplicate-endpoint",
        "missing-binding",
        "namespace",
    ],
)
def test_unsupported_or_inconsistent_xml_fails_before_output(tmp_path, case):
    src = tmp_path / "input"
    shutil.copytree(ROOT / "specs/bus/AI_SOC", src)
    overlay = yaml.safe_load((src / "native.yaml").read_text())
    filename = "decoder.xml"
    if case == "definition":
        filename = "bus.xml"
    elif case == "abstraction":
        filename = "abstraction.xml"
    elif case in {"disconnect", "duplicate-endpoint"}:
        filename = "design.xml"
    p = src / "ipxact" / filename
    tree = etree.parse(str(p))
    r = tree.getroot()
    if case == "unknown-bus":
        r.find(f".//{PREFIX}busType").set("name", "AXI4")
    elif case == "definition":
        r.find(PREFIX + "directConnection").text = "false"
    elif case == "abstraction":
        r.find(f".//{PREFIX}logicalName").text = "unknown"
    elif case == "endian":
        r.find(f".//{PREFIX}endianness").text = "big"
    elif case == "units":
        r.find(f".//{PREFIX}bitsInLau").text = "16"
    elif case == "steering":
        r.find(f".//{PREFIX}bitSteering").text = "1"
    elif case == "disconnect":
        endpoint = r.find(f".//{PREFIX}activeInterface")
        endpoint.getparent().remove(endpoint)
    elif case == "segment":
        r.find(f".//{PREFIX}subspaceMap").set("segmentRef", "unknown")
    elif case == "map-ref":
        r.find(f".//{PREFIX}memoryMapRef").set("memoryMapRef", "unknown")
    elif case == "alias":
        values = list(overlay["space_aliases"])
        overlay["space_aliases"][values[1]] = overlay["space_aliases"][values[0]]
    elif case == "parameter":
        r.find(f".//{PREFIX}parameter").set("parameterId", "UNQUALIFIED")
    elif case == "cpu":
        overlay["cpu"]["harts"] = 2
    elif case == "extra-overlay":
        overlay["placements"] = {}
    elif case == "extra-xml":
        etree.SubElement(r, PREFIX + "vendorExtensions")
    elif case == "width":
        r.find(f".//{PREFIX}addressSpace/{PREFIX}width").text = "32"
    elif case == "fanout":
        p = src / "ipxact/bus.xml"
        tree = etree.parse(str(p))
        tree.getroot().find(PREFIX + "maxTargets").text = "1"
    elif case == "initiator-base":
        reference = r.find(f".//{PREFIX}addressSpaceRef")
        etree.SubElement(reference, PREFIX + "baseAddress").text = "4096"
    elif case == "duplicate-endpoint":
        all_nodes = r.findall(f".//{PREFIX}activeInterface")
        all_nodes[1].attrib.update(all_nodes[0].attrib)
    elif case == "missing-binding":
        overlay["bindings"].pop()
    elif case == "namespace":
        r.tag = "{http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009}component"
    tree.write(str(p))
    (src / "native.yaml").write_text(yaml.safe_dump(overlay))
    out = tmp_path / "generated"
    with pytest.raises(ContractError):
        generate(src / "native.yaml", out, "both", "ipxact")
    assert not out.exists()


@pytest.mark.parametrize(
    "case", ["definition", "local-window", "segment", "interface", "connection", "extra-field"]
)
def test_bus_facts_cannot_disagree_with_canonical_runtime(tmp_path, case):
    out = tmp_path / "generated"
    generate(ROOT / "specs/bus/AI_SOC/native.yaml", out, "both", "ipxact")
    p = out / "native-ir.json"
    d = json.loads(p.read_text())
    g = d["bus_graph"]
    if case == "definition":
        g["definition"]["max_targets"] = 0
    elif case == "local-window":
        next(n for n in g["nodes"] if n["instance"]["windows"])["instance"]["windows"][0][
            "base"
        ] += 4096
    elif case == "segment":
        next(s for s in g["spaces"] if s["segments"])["segments"][0]["offset"] += 4096
    elif case == "interface":
        g["interfaces"][0]["bus_type"] = "vendor:bus:AXI4:1.0"
    elif case == "connection":
        g["connections"][0]["endpoints"][0]["interface"] = "missing"
    else:
        g["nodes"][0]["instance"]["ignored"] = True
    p.write_text(json.dumps(d))
    with pytest.raises(ContractError):
        read_ir(p)


@pytest.mark.parametrize("name", ["AI_SOC", "AIZ_SOC"])
def test_actual_standard_xml_bus_graph_drives_both_engines_and_all_native_kernels(tmp_path, name):
    if os.environ.get("USP_REQUIRE_BUS_XML") != "1":
        pytest.skip("requires actual source-built bus XML machine and native software")
    built = Path(os.environ[f"USP_BUS_XML_{name}"])
    record, _ = verify_build(built)
    ir = read_ir(built / "generated/native-ir.json")
    assert ir.schema_version == "5" and ir.bus_graph is not None
    previous = Path(os.environ[f"USP_NATIVE_FABRIC_{name}"])
    assert (built / "generated/firmware.elf").read_bytes() == (
        previous / "generated/firmware.elf"
    ).read_bytes()
    out = tmp_path / "run"
    assert execute_flow(built, out)["status"] == "PASSED"
    assert verify_flow(built, out)["status"] == "PASSED"
    assert set(record["software"]) == {"baremetal", "freertos", "zephyr", "ucos2", "ucos3"}
    for workload in record["software"]:
        q = json.loads((out / workload / "qemu/report.json").read_text())
        g = json.loads((out / workload / "gem5/report.json").read_text())
        assert q["native_ir_sha256"] == g["native_ir_sha256"] == record["native_ir_sha256"]
        assert q["firmware_sha256"] == g["firmware_sha256"]
        assert q["status"] == g["status"] == "PASSED"
