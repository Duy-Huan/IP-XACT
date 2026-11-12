import os
import shutil
from pathlib import Path

import pytest
import yaml
from lxml import etree as E

from socsim.domain import ContractError
from socsim.ipxact import PREFIX as N
from socsim.pipeline import run_zephyr
from socsim.project import load_project

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def hierarchy(tmp_path):
    root = tmp_path / "project"
    shutil.copytree(ROOT / "examples/rv64-hierarchy", root)
    return root


def test_configuration_aliases_and_parameterized_leaf(hierarchy):
    project = load_project(hierarchy)
    uart = next(i for i in project.ir.instances if i.name == "uart")
    assert uart.hierarchy_path == ("serial", "uart")
    assert uart.windows[0].base == 0x11000000
    assert uart.windows[0].size == 256
    assert dict(uart.parameters)["SPAN"] == 2
    other = hierarchy.parent / "relocated"
    shutil.copytree(hierarchy, other)
    assert project.ir.to_dict() == load_project(other).ir.to_dict()


@pytest.mark.parametrize(
    "case",
    ["cycle", "unknown-view", "ambiguous", "alias-collision", "unknown-alias", "missing-placement"],
)
def test_invalid_hierarchy_fails_closed(hierarchy, case):
    manifest = hierarchy / "simulation.yaml"
    data = yaml.safe_load(manifest.read_text())
    if case == "cycle":
        path = hierarchy / "ipxact/serial-design.xml"
        tree = E.parse(str(path))
        ref = tree.find(".//" + N + "componentRef")
        ref.set("library", "hierarchy")
        ref.set("name", "serial_wrapper")
        for child in list(ref):
            ref.remove(child)
        tree.write(str(path))
    elif case == "unknown-view":
        path = hierarchy / "ipxact/config.xml"
        path.write_text(path.read_text().replace('viewRef="rtl"', 'viewRef="missing"'))
    elif case == "ambiguous":
        path = hierarchy / "ipxact/serial-wrapper.xml"
        tree = E.parse(str(path))
        views = tree.find(".//" + N + "views")
        view = E.fromstring(E.tostring(views[0]))
        view.find(N + "name").text = "alternate"
        views.append(view)
        tree.write(str(path))
        data["design"] = "ipxact/design.xml"
    elif case == "alias-collision":
        data["instance_aliases"]["serial.uart"] = "ram"
    elif case == "unknown-alias":
        data["instance_aliases"]["missing.uart"] = "missing"
    else:
        data["placements"].pop("serial.uart")
    manifest.write_text(yaml.safe_dump(data))
    with pytest.raises(ContractError):
        load_project(hierarchy)


def test_recursive_design_configuration_instantiation(hierarchy):
    # A second structural wrapper selects the complete configured design.
    wrapper = E.parse(str(hierarchy / "ipxact/serial-wrapper.xml"))
    wrapper.getroot().find(N + "name").text = "outer"
    v = wrapper.find(".//" + N + "designInstantiationRef")
    v.tag = N + "designConfigurationInstantiationRef"
    inst = wrapper.find(".//" + N + "designInstantiation")
    inst.tag = N + "designConfigurationInstantiation"
    ref = inst.find(N + "designRef")
    ref.tag = N + "designConfigurationRef"
    ref.set("name", "reference_config")
    wrapper.write(str(hierarchy / "ipxact/outer.xml"))
    tree = E.parse(str(hierarchy / "ipxact/design.xml"))
    tree.getroot().find(N + "name").text = "outer_design"
    items = tree.find(N + "componentInstances")
    for child in list(items):
        items.remove(child)
    item = E.SubElement(items, N + "componentInstance")
    E.SubElement(item, N + "instanceName").text = "shell"
    E.SubElement(
        item,
        N + "componentRef",
        vendor="socsim.example",
        library="hierarchy",
        name="outer",
        version="1.0",
    )
    tree.write(str(hierarchy / "ipxact/outer-design.xml"))
    path = hierarchy / "simulation.yaml"
    data = yaml.safe_load(path.read_text())
    data["design"] = "ipxact/outer-design.xml"
    data["instance_aliases"] = {
        "shell." + k: data["instance_aliases"].get(k, k) for k in data["placements"]
    }
    data["placements"] = {"shell." + k: v for k, v in data["placements"].items()}
    path.write_text(yaml.safe_dump(data))
    p = load_project(hierarchy)
    assert next(i for i in p.ir.instances if i.name == "uart").hierarchy_path == (
        "shell",
        "serial",
        "uart",
    )


@pytest.mark.integration
def test_real_configured_hierarchy_boot_and_irq(tmp_path):
    if os.environ.get("USP_REQUIRE_WORKSPACE") != "1":
        pytest.skip("configured hierarchy native build is an explicit acceptance gate")
    p = load_project(ROOT / "examples/rv64-hierarchy")
    report = run_zephyr(p, ROOT / "build/rv64-hierarchy", tmp_path / "run")
    assert report["status"] == "PASSED"
    assert report["assertions"]["USP_TIMER_IRQ_OK"]
    assert report["assertions"]["USP_UART_PLIC_IRQ_OK"]
