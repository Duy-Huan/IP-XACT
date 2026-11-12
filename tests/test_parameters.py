import shutil
from pathlib import Path

import pytest
from lxml import etree

from socsim.domain import ContractError
from socsim.ipxact import NS, PREFIX, component_windows, read_xml
from socsim.parameters import expression, resolve_parameters
from socsim.project import load_project

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("8 / 3", 2),
        ("(1 << 8) | 3", 259),
        ("012 + 0x10", 28),
        ("7 % 4", 3),
        ("15 & 7 ^ 1", 6),
        ("+2 >> 1", 1),
    ],
)
def test_unsigned_expression_subset(text, expected):
    assert expression(text, lambda key: 0, "test") == expected


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os').system('true')",
        "x[0]",
        "2 ** 1000000000",
        "1 // 2",
        "1 << 64",
        "0xffffffffffffffff + 1",
        "0 - 1",
        "1 / 0",
        "1 % 0",
        "True",
        "0b10",
        "1_000",
        "~0",
        "1.0",
        "1 if 1 else 0",
        "(" * 50 + "1" + ")" * 50 + " + " + "+" * 50 + "1",
    ],
)
def test_unsafe_or_unbounded_expressions_rejected(text):
    with pytest.raises(ContractError):
        expression(text, lambda key: 0, "test")


def component(values):
    node = etree.Element(PREFIX + "component", nsmap={None: NS})
    params = etree.SubElement(node, PREFIX + "parameters")
    for identifier, expr in values.items():
        p = etree.SubElement(
            params,
            PREFIX + "parameter",
            parameterId=identifier,
            type="longint",
            sign="unsigned",
            resolve="user",
        )
        etree.SubElement(p, PREFIX + "name").text = identifier
        etree.SubElement(p, PREFIX + "value").text = expr
    return node


def test_forward_dependencies_and_cycle_diagnostics():
    assert resolve_parameters(component({"A": "B + 2", "B": "C * 2", "C": "4"})) == {
        "A": 10,
        "B": 8,
        "C": 4,
    }
    with pytest.raises(ContractError, match="E_PARAMETER_CYCLE A -> B -> A"):
        resolve_parameters(component({"A": "B", "B": "A"}))
    with pytest.raises(ContractError, match="E_PARAMETER_UNRESOLVED"):
        resolve_parameters(component({"A": "MISSING"}))


def test_xsd_valid_instance_override_and_ir_provenance():
    project = load_project(ROOT / "examples/rv64-minimal")
    uart = next(i for i in project.ir.instances if i.name == "uart")
    assert dict(uart.parameters) == {"BITS": 8, "SPAN": 2, "UNIT": 128}
    assert uart.windows[0].size == 256
    raw = read_xml(ROOT / "examples/rv64-minimal/ipxact/uart.xml")
    assert component_windows(raw)[0].size == 128


@pytest.mark.parametrize("case", ["immutable", "unknown", "bounds", "type", "generated"])
def test_invalid_parameter_contract_in_real_project(tmp_path, case):
    root = tmp_path / "project"
    shutil.copytree(ROOT / "examples/rv64-minimal", root)
    if case in {"immutable", "unknown", "bounds"}:
        path = root / "ipxact/design.xml"
        text = path.read_text()
        if case == "immutable":
            text = text.replace('referenceId="SPAN"', 'referenceId="UNIT"')
        if case == "unknown":
            text = text.replace('referenceId="SPAN"', 'referenceId="MISSING"')
        if case == "bounds":
            text = text.replace('referenceId="SPAN">2<', 'referenceId="SPAN">9<')
    else:
        path = root / "ipxact/uart.xml"
        text = path.read_text()
        if case == "type":
            text = text.replace('type="longint"', 'type="string"')
        if case == "generated":
            text = text.replace('resolve="user"', 'resolve="generated"')
    path.write_text(text)
    with pytest.raises(ContractError, match="E_PARAMETER"):
        load_project(root)


def test_override_isolation_between_instances():
    raw = component({"SIZE": "2"})
    ref = etree.Element(PREFIX + "componentRef")
    cfg = etree.SubElement(ref, PREFIX + "configurableElementValues")
    etree.SubElement(cfg, PREFIX + "configurableElementValue", referenceId="SIZE").text = "8"
    assert resolve_parameters(raw, ref) == {"SIZE": 8}
    assert resolve_parameters(raw) == {"SIZE": 2}
