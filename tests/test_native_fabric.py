"""Decoder graph semantics and real translated HW/SW execution."""

import copy
import json
import os
from pathlib import Path

import pytest
import yaml

from socsim.domain import ContractError
from socsim.native_fabric import read_source, resolve
from socsim.native_flow import execute_flow, plan, verify_build, verify_flow
from socsim.native_ir import read_ir
from socsim.soc_native import generate, verify_generated

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name,base", [("AI_SOC", 0x40000000000), ("AIZ_SOC", 0x50000000000)])
def test_decoder_tree_retains_local_contract_and_both_lowerers_consume_it(tmp_path, name, base):
    source = ROOT / f"specs/fabric/{name}.yaml"
    spec, routing = read_source(source)
    out = tmp_path / "generated"
    generate(source, out, "both", "fabric")
    ir = read_ir(out / "native-ir.json")
    assert ir.schema_version == "4" and ir.routing == routing
    assert ir.to_spec() == spec == verify_generated(out)
    rows = resolve(routing, {d["name"]: d["size"] for d in spec["devices"]})
    math = [p for p in routing.placements if p.space == "math"]
    secure = [p for p in routing.placements if p.space == "security"]
    assert math[0].address == secure[0].address == 0x10000
    assert rows[math[0].instance] == (base + 0x1000, ("io_decode", "math_decode"))
    assert rows[secure[0].instance] == (base + 0x20000, ("io_decode", "security_decode"))
    assert plan(ROOT / f"specs/fabric/{name}-flow.yaml")["engine"] == "both"


@pytest.mark.parametrize(
    "case",
    [
        "duplicate-space",
        "missing-root",
        "cycle",
        "alias",
        "disconnected",
        "unknown-space",
        "protocol",
        "unaligned",
        "overflow",
        "zero-size",
        "bad-bits",
        "clipped-target",
        "shadow",
        "missing-target",
        "duplicate-target",
        "duplicate-bridge",
        "nested-outside",
        "overlapping-local",
        "unknown-field",
    ],
)
def test_incompatible_graph_fails_before_any_output(tmp_path, case):
    data = yaml.safe_load((ROOT / "specs/fabric/AI_SOC.yaml").read_text())
    r = data["routing"]
    b = r["bridges"][0]
    p = next(p for p in r["placements"] if p["space"] == "math")
    if case == "duplicate-space":
        r["spaces"].append(copy.deepcopy(r["spaces"][0]))
    elif case == "missing-root":
        r["root"] = "missing"
    elif case == "cycle":
        b["source"] = "math"
    elif case == "alias":
        r["bridges"].append(dict(b, name="second"))
    elif case == "disconnected":
        r["spaces"].append({"name": "unused", "address_bits": 32})
    elif case == "unknown-space":
        p["space"] = "missing"
    elif case == "protocol":
        b["protocol"] = "axi4"
    elif case == "unaligned":
        b["source_base"] += 1
    elif case == "overflow":
        b["source_base"] = 2**48
    elif case == "zero-size":
        b["size"] = 0
    elif case == "bad-bits":
        r["spaces"][0]["address_bits"] = 64
    elif case == "clipped-target":
        p["address"] = 0x20000
    elif case == "shadow":
        b["source_base"] = next(
            x["address"] for x in r["placements"] if x["instance"] == data["boot"]["ram"]
        )
    elif case == "missing-target":
        r["placements"].pop()
    elif case == "duplicate-target":
        r["placements"].append(copy.deepcopy(p))
    elif case == "duplicate-bridge":
        r["bridges"][1]["name"] = b["name"]
    elif case == "nested-outside":
        r["bridges"][1]["source_base"] = 0x50000000
    elif case == "overlapping-local":
        second = next(x for x in r["placements"] if x["space"] == "math" and x is not p)
        second["address"] = p["address"]
    else:
        r["clock_crossing"] = "invented"
    source = tmp_path / "source.yaml"
    source.write_text(yaml.safe_dump(data))
    out = tmp_path / "generated"
    with pytest.raises(ContractError):
        generate(source, out, "both", "fabric")
    assert not out.exists()


@pytest.mark.parametrize("case", ["target", "local", "physical", "extra", "order"])
def test_canonical_fabric_cannot_disagree_with_its_runtime_projection(tmp_path, case):
    out = tmp_path / "generated"
    generate(ROOT / "specs/fabric/AI_SOC.yaml", out, "both", "fabric")
    path = out / "native-ir.json"
    d = json.loads(path.read_text())
    if case == "target":
        d["routing"]["bridges"][0]["target_base"] += 4096
    elif case == "local":
        d["routing"]["placements"][0]["address"] += 4096
    elif case == "physical":
        d["regions"][0]["instance"]["windows"][0]["base"] += 4096
    elif case == "order":
        d["routing"]["spaces"].reverse()
    else:
        d["routing"]["unimplemented"] = True
    path.write_text(json.dumps(d))
    with pytest.raises(ContractError):
        read_ir(path)


@pytest.mark.parametrize("name", ["AI_SOC", "AIZ_SOC"])
def test_actual_translated_ips_and_generated_kernels_on_both_engines(tmp_path, name):
    if os.environ.get("USP_REQUIRE_NATIVE_FABRIC") != "1":
        pytest.skip("requires source-built hierarchical-decoder native HW/SW flow")
    built = Path(os.environ[f"USP_NATIVE_FABRIC_{name}"])
    record, _ = verify_build(built)
    ir = read_ir(built / "generated/native-ir.json")
    assert ir.schema_version == "4" and len(ir.routing.bridges) == 3
    out = tmp_path / "run"
    assert execute_flow(built, out)["status"] == "PASSED"
    assert verify_flow(built, out)["status"] == "PASSED"
    assert set(record["software"]) == {"baremetal", "freertos", "zephyr", "ucos2", "ucos3"}
    for software in record["software"]:
        q = json.loads((out / software / "qemu/report.json").read_text())
        g = json.loads((out / software / "gem5/report.json").read_text())
        assert q["native_ir_sha256"] == g["native_ir_sha256"] == record["native_ir_sha256"]
        assert q["firmware_sha256"] == g["firmware_sha256"]
        assert q["status"] == g["status"] == "PASSED"
