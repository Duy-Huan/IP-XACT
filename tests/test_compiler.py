import hashlib
import json
from pathlib import Path

import pytest
import yaml

from socsim.backends import qemu_plan
from socsim.cli import main
from socsim.domain import ContractError
from socsim.ipxact import SCHEMAS, integer, read_xml
from socsim.project import load_project


def change_yaml(path, edit):
    value = yaml.safe_load(path.read_text())
    edit(value)
    path.write_text(yaml.safe_dump(value))


def test_elaboration_and_binding(project_dir):
    project = load_project(project_dir)
    assert len(project.ir.instances) == 4
    assert [i.name for i in project.ir.instances] == ["finisher", "ram", "rv64", "uart"]
    ram = next(i for i in project.ir.instances if i.name == "ram")
    assert (ram.windows[0].base, ram.windows[0].size) == (0x80000000, 0x8000000)
    plan = qemu_plan(project, "qemu")
    assert len(plan["bindings"]) == 4
    assert plan["simulated_time_seconds"] is None


def test_ir_deterministic_across_paths(project_dir, tmp_path):
    import shutil

    relocated = tmp_path / "other"
    shutil.copytree(project_dir, relocated)
    assert load_project(project_dir).ir.to_dict() == load_project(relocated).ir.to_dict()


def test_schema_checksums():
    provenance = json.loads((SCHEMAS.parent / "provenance.json").read_text())
    assert provenance["files"]
    for name, expected in provenance["files"].items():
        assert hashlib.sha256((SCHEMAS / name).read_bytes()).hexdigest() == expected


@pytest.mark.parametrize("value", [True, -1, 2**64, "1 << 4", "__import__('os')", None, "0xZZ"])
def test_integer_rejects_unsafe_expressions(value):
    with pytest.raises(ContractError):
        integer(value, "test")


@pytest.mark.parametrize("value,expected", [("0x1000", 4096), ("012", 12), (16, 16)])
def test_integer_literals(value, expected):
    assert integer(value, "test") == expected


@pytest.mark.parametrize(
    "old,new,match",
    [
        ("1685-2022", "1685-2014", "namespace"),
        ("<vendor>socsim.example</vendor>", "", "not expected"),
        ("<componentInstances>", "<nonsense>", "Opening and ending tag mismatch"),
        ('name="rv64" version="1.0"', 'name="missing" version="1.0"', "unresolved"),
    ],
)
def test_bad_design(project_dir, old, new, match):
    path = project_dir / "ipxact/design.xml"
    path.write_text(path.read_text().replace(old, new))
    with pytest.raises(ContractError, match=match):
        load_project(project_dir)


def test_duplicate_vlnv(project_dir):
    source = project_dir / "ipxact/ram.xml"
    (source.parent / "copy.xml").write_bytes(source.read_bytes())
    with pytest.raises(ContractError, match="duplicate VLNV"):
        load_project(project_dir)


def test_entities_forbidden(project_dir):
    path = project_dir / "ipxact/rv64.xml"
    content = path.read_text().replace(
        "?>", '?>\n<!DOCTYPE component [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
    )
    path.write_text(content)
    with pytest.raises(ContractError, match="DTD/entity"):
        read_xml(path)


def test_unsupported_semantics_not_ignored(project_dir):
    path = project_dir / "ipxact/rv64.xml"
    path.write_text(
        path.read_text().replace(
            "</component>", "<description>not in subset</description></component>"
        )
    )
    with pytest.raises(ContractError, match="UNSUPPORTED"):
        load_project(project_dir)


@pytest.mark.parametrize(
    "placement,match",
    [
        ({"ram": 0x10000000, "uart": 0x10000000, "rv64": 0, "finisher": 0x100000}, "overlapping"),
        ({"ram": 2**64 - 1, "uart": 0x10000000, "rv64": 0, "finisher": 0x100000}, "overflow"),
        ({"ram": 0}, "exactly all"),
    ],
)
def test_bad_placement(project_dir, placement, match):
    change_yaml(project_dir / "simulation.yaml", lambda d: d.update(placements=placement))
    with pytest.raises(ContractError, match=match):
        load_project(project_dir)


def test_non_byte_units_rejected(project_dir):
    path = project_dir / "ipxact/ram.xml"
    path.write_text(path.read_text().replace("<addressUnitBits>8", "<addressUnitBits>16"))
    with pytest.raises(ContractError, match="non-byte"):
        load_project(project_dir)


@pytest.mark.parametrize(
    "backend", ["gem5", "chipyard", "opentitan", "systemc", "hybrid", "unknown"]
)
def test_unimplemented_backend_rejected(project_dir, backend):
    with pytest.raises(ContractError, match="UNSUPPORTED backend"):
        qemu_plan(load_project(project_dir), backend)


def test_missing_binding(project_dir):
    change_yaml(project_dir / "models.yaml", lambda d: d["bindings"].pop())
    with pytest.raises(ContractError, match="UNBOUND"):
        qemu_plan(load_project(project_dir), "qemu")


def test_duplicate_binding(project_dir):
    change_yaml(
        project_dir / "models.yaml", lambda d: d["bindings"].append(d["bindings"][0].copy())
    )
    with pytest.raises(ContractError, match="ambiguous"):
        qemu_plan(load_project(project_dir), "qemu")


def test_wrong_map_cannot_silently_use_virt(project_dir):
    change_yaml(project_dir / "simulation.yaml", lambda d: d["placements"].update(uart=0x20000000))
    with pytest.raises(ContractError, match="does not match"):
        qemu_plan(load_project(project_dir), "qemu")


def test_yaml_duplicate_key(project_dir):
    path = project_dir / "simulation.yaml"
    path.write_text(path.read_text() + '\nschema_version: "1"\n')
    with pytest.raises(ContractError, match="unique"):
        load_project(project_dir)


@pytest.mark.parametrize("name", ["../outside.xml", "/etc/passwd"])
def test_path_traversal_rejected(project_dir, name):
    change_yaml(project_dir / "simulation.yaml", lambda d: d.update(design=name))
    with pytest.raises(ContractError):
        load_project(project_dir)


def test_xml_symlink_escape_rejected(project_dir, tmp_path):
    target = tmp_path / "external.xml"
    source = project_dir / "ipxact/ram.xml"
    target.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(target)
    with pytest.raises(ContractError, match="escapes"):
        load_project(project_dir)


def test_cli(project_dir, tmp_path, capsys):
    assert main(["validate", str(project_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PASSED"
    output = tmp_path / "soc.json"
    assert main(["elaborate", str(project_dir), "--out", str(output)]) == 0
    assert json.loads(output.read_text())["schema_version"] == "1"
    assert main(["elaborate", str(project_dir), "--out", str(output)]) == 2
    assert main(["plan", str(project_dir), "--backend", "gem5"]) == 2


def test_unknown_manifest_key(project_dir):
    change_yaml(project_dir / "simulation.yaml", lambda d: d.update(command="echo fake"))
    with pytest.raises(ContractError, match="exactly keys"):
        load_project(project_dir)


def test_schema_loaded_from_package():
    assert (Path(SCHEMAS) / "index.xsd").is_file()
