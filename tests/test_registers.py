import json
import shutil
from pathlib import Path

import pytest
from lxml import etree as E

from socsim.domain import ContractError
from socsim.ipxact import PREFIX as N
from socsim.pipeline import generate, resolve
from socsim.project import load_project

ROOT = Path(__file__).resolve().parents[1]


def test_legacy_backend_does_not_ignore_register_contracts(project_dir):
    from socsim.backends import qemu_plan

    path = project_dir / "ipxact/uart.xml"
    tree = E.parse(str(path))
    block = tree.find(".//" + N + "addressBlock")
    source = E.parse(str(ROOT / "examples/rv64-ips/ipxact/cygcd.xml"))
    block.append(E.fromstring(E.tostring(source.find(".//" + N + "register"))))
    tree.write(str(path))
    with pytest.raises(ContractError, match="UNSUPPORTED register declarations"):
        qemu_plan(load_project(project_dir), "qemu")


def test_register_ir_schema_and_generated_driver_header(tmp_path):
    from jsonschema import validate

    project = load_project(ROOT / "examples/rv64-ips")
    gcd = next(i for i in project.ir.instances if i.name == "cygcd")
    assert [(r.name, r.offset, r.access) for r in gcd.windows[0].registers] == [
        ("status", 0, "read-only"),
        ("x", 4, "write-only"),
        ("y", 8, "write-only"),
        ("result", 12, "read-only"),
    ]
    schema = json.loads((ROOT / "src/socsim/schemas/ir-v2.json").read_text())
    validate(json.loads(json.dumps(project.ir.to_dict())), schema)
    out = generate(project, tmp_path / "generated")
    header = (out / "soc_registers.h").read_text()
    assert "#define USP_CYGCD_RESULT_ADDRESS 0x1300000cUL" in header
    assert "#define USP_CYGCD_STATUS_INPUT_READY_MASK 0x2UL" in header
    assert "USP_CYGCD_RESULT_OFFSET" in (out / "app/main.c").read_text()
    assert (out / "app/soc_registers.h").read_bytes() == (out / "soc_registers.h").read_bytes()


@pytest.mark.parametrize(
    "case",
    [
        "overlap",
        "misaligned",
        "outside",
        "field-outside",
        "field-overlap",
        "field-access",
        "wrong-model-offset",
        "wrong-model-access",
        "multiple-policies",
    ],
)
def test_bad_scalar_register_semantics_or_model_binding_rejected(tmp_path, case):
    root = tmp_path / "project"
    shutil.copytree(ROOT / "examples/rv64-ips", root)
    path = root / "ipxact/cygcd.xml"
    tree = E.parse(str(path))
    regs = tree.findall(".//" + N + "register")
    if case == "overlap":
        regs[1].find(N + "addressOffset").text = "0"
    if case == "misaligned":
        regs[1].find(N + "addressOffset").text = "3"
    if case == "outside":
        regs[1].find(N + "addressOffset").text = "4096"
    if case == "field-outside":
        regs[0].find(N + "field/" + N + "bitWidth").text = "33"
    if case == "field-overlap":
        regs[0].findall(N + "field")[1].find(N + "bitOffset").text = "0"
    if case == "field-access":
        regs[0].find(
            ".//"
            + N
            + "field/"
            + N
            + "fieldAccessPolicies/"
            + N
            + "fieldAccessPolicy/"
            + N
            + "access"
        ).text = "write-only"
    if case == "wrong-model-offset":
        regs[-1].find(N + "addressOffset").text = "16"
    if case == "wrong-model-access":
        regs[-1].find(
            N + "accessPolicies/" + N + "accessPolicy/" + N + "access"
        ).text = "read-write"
    if case == "multiple-policies":
        policies = regs[0].find(N + "accessPolicies")
        policies.append(E.fromstring(E.tostring(policies[0])))
    tree.write(str(path))
    with pytest.raises(ContractError):
        resolve(load_project(root))
