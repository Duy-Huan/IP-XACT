"""Offline XSD validation and a deliberately small fail-closed 2022 subset."""

import hashlib
import re
from functools import lru_cache
from pathlib import Path

from lxml import etree

from .domain import ContractError, Instance, SoCIR, Window
from .parameters import expression, resolve_parameters

NS = "http://www.accellera.org/XMLSchema/IPXACT/1685-2022"
PREFIX = f"{{{NS}}}"
SCHEMAS = Path(__file__).parent / "schemas" / "1685-2022"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def integer(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ContractError(f"{label}: boolean is not an integer")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)", value):
        result = int(value, 16 if value.lower().startswith("0x") else 10)
    else:
        raise ContractError(f"{label}: only decimal/hex integer literals supported: {value!r}")
    if not 0 <= result < 2**64:
        raise ContractError(f"{label}: outside unsigned 64-bit range")
    return result


def _parser() -> etree.XMLParser:
    return etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)


@lru_cache(maxsize=1)
def schema() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(SCHEMAS / "index.xsd"), _parser()))


def read_xml(path: Path) -> etree._Element:
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ContractError(f"{path.name}: XML exceeds 4 MiB intake limit")
        document = etree.parse(str(path), _parser())
        if document.docinfo.doctype:
            raise ContractError(f"{path.name}: DTD/entity declarations are forbidden")
        root = document.getroot()
        if etree.QName(root).namespace != NS:
            raise ContractError(f"{path.name}: expected IEEE 1685-2022 namespace")
        schema().assertValid(document)
        return root
    except (OSError, etree.Error) as exc:
        raise ContractError(f"{path.name}: {exc}") from exc


def text(node: etree._Element, name: str) -> str:
    result = node.findtext(PREFIX + name)
    if result is None or not result.strip():
        raise ContractError(f"line {node.sourceline}: missing {name}")
    return result.strip()


def identity(node: etree._Element) -> str:
    return ":".join(text(node, k) for k in ("vendor", "library", "name", "version"))


# Every accepted child/attribute is listed; no ignored functional semantics.
CHILDREN = {
    "component": {"vendor", "library", "name", "version", "memoryMaps", "parameters", "model"},
    "design": {"vendor", "library", "name", "version", "componentInstances"},
    "componentInstances": {"componentInstance"},
    "componentInstance": {"instanceName", "componentRef"},
    "memoryMaps": {"memoryMap"},
    "memoryMap": {"name", "addressBlock", "addressUnitBits"},
    "addressBlock": {"name", "baseAddress", "range", "width", "usage", "register"},
    "register": {"name", "addressOffset", "size", "volatile", "accessPolicies", "field"},
    "field": {"name", "bitOffset", "bitWidth", "volatile", "fieldAccessPolicies"},
    "accessPolicies": {"accessPolicy"},
    "accessPolicy": {"access"},
    "fieldAccessPolicies": {"fieldAccessPolicy"},
    "fieldAccessPolicy": {"access"},
    "parameters": {"parameter"},
    "parameter": {"name", "value"},
    "componentRef": {"configurableElementValues"},
    "configurableElementValues": {"configurableElementValue"},
    "model": {"views", "instantiations"},
    "views": {"view"},
    "view": {"name", "designInstantiationRef", "designConfigurationInstantiationRef"},
    "instantiations": {"designInstantiation", "designConfigurationInstantiation"},
    "designInstantiation": {"name", "designRef"},
    "designConfigurationInstantiation": {"name", "designConfigurationRef"},
    "designConfiguration": {
        "vendor",
        "library",
        "name",
        "version",
        "designRef",
        "viewConfiguration",
    },
    "viewConfiguration": {"instanceName", "view"},
}
LEAVES = {
    "vendor",
    "library",
    "name",
    "version",
    "instanceName",
    "componentRef",
    "baseAddress",
    "range",
    "width",
    "usage",
    "addressUnitBits",
    "value",
    "configurableElementValue",
    "addressOffset",
    "size",
    "volatile",
    "access",
    "bitOffset",
    "bitWidth",
    "designRef",
    "designConfigurationRef",
    "designInstantiationRef",
    "designConfigurationInstantiationRef",
}


def check_subset(root: etree._Element, source: str) -> None:
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        tag = etree.QName(node).localname
        if etree.QName(node).namespace != NS or tag not in CHILDREN.keys() | LEAVES:
            raise ContractError(f"{source}:{node.sourceline}: UNSUPPORTED element {tag}")
        allowed_attributes = (
            {"vendor", "library", "name", "version"}
            if tag in {"componentRef", "designRef", "designConfigurationRef"}
            else set()
        )
        if tag == "view" and node.getparent().tag == PREFIX + "viewConfiguration":
            allowed_attributes = {"viewRef"}
        if tag == "parameter":
            allowed_attributes = {"parameterId", "type", "sign", "resolve", "minimum", "maximum"}
        if tag == "configurableElementValue":
            allowed_attributes = {"referenceId"}
        for attribute in node.attrib:
            if attribute not in allowed_attributes:
                raise ContractError(
                    f"{source}:{node.sourceline}: UNSUPPORTED attribute {attribute}"
                )
        for child in node:
            if isinstance(child.tag, str) and etree.QName(child).localname not in CHILDREN.get(
                tag, set()
            ):
                raise ContractError(
                    f"{source}:{child.sourceline}: UNSUPPORTED {tag}/{etree.QName(child).localname}"
                )


def component_windows(root: etree._Element, parameters=None) -> tuple[Window, ...]:
    from .registers import parse_registers

    parameters = resolve_parameters(root) if parameters is None else parameters

    def number(value, label):
        def lookup(key):
            if key not in parameters:
                raise ContractError(f"{label}: E_PARAMETER_UNRESOLVED {key}")
            return parameters[key]

        return expression(value, lookup, label)

    windows = []
    names = set()
    for memory_map in root.findall(f"{PREFIX}memoryMaps/{PREFIX}memoryMap"):
        units = memory_map.findtext(PREFIX + "addressUnitBits", "8")
        if number(units, "addressUnitBits") != 8:
            raise ContractError("UNSUPPORTED non-byte addressUnitBits")
        for block in memory_map.findall(PREFIX + "addressBlock"):
            name = f"{text(memory_map, 'name')}.{text(block, 'name')}"
            if name in names:
                raise ContractError(f"duplicate address block {name}")
            names.add(name)
            usage = block.findtext(PREFIX + "usage", "memory")
            if usage not in {"memory", "register"}:
                raise ContractError(f"UNSUPPORTED address block usage: {usage}")
            base = number(text(block, "baseAddress"), name)
            size = number(text(block, "range"), name)
            width = number(text(block, "width"), name)
            if size == 0 or width not in {8, 16, 32, 64} or base + size > 2**64:
                raise ContractError(f"{name}: invalid range/width")
            registers = parse_registers(block, number, size)
            if registers and usage != "register":
                raise ContractError("E_REGISTER_USAGE register declarations require usage=register")
            windows.append(Window(name, base, size, width, registers))
    return tuple(sorted(windows, key=lambda w: (w.base, w.name)))


def elaborate(project: Path, placements: dict, design_path: Path, aliases=None) -> SoCIR:
    from .hierarchy import leaves

    catalog = {}
    sources = []
    for path in sorted((project / "ipxact").rglob("*.xml")):
        if not path.resolve().is_relative_to(project):
            raise ContractError("XML source escapes project root")
        root = read_xml(path)
        check_subset(root, str(path.relative_to(project)))
        vlnv = identity(root)
        if vlnv in catalog:
            raise ContractError(f"duplicate VLNV {vlnv}")
        catalog[vlnv] = root
        sources.append((path.relative_to(project).as_posix(), digest(path)))
    design = read_xml(design_path)
    if design.tag not in {PREFIX + "design", PREFIX + "designConfiguration"}:
        raise ContractError("selected input is not a design/configuration")
    aliases = {} if aliases is None else aliases
    if not isinstance(aliases, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in aliases.items()
    ):
        raise ContractError("instance_aliases must map source paths to logical names")
    instances = []
    seen = set()
    source_names = set()
    for path, component, ref in leaves(design, catalog):
        source_name = ".".join(path)
        source_names.add(source_name)
        name = aliases.get(source_name, source_name)
        if (
            source_name in aliases and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
        ) or name in seen:
            raise ContractError(f"duplicate/unsafe instance alias {name}")
        seen.add(name)
        vlnv = identity(component)
        offset = integer(placements.get(source_name, 0), f"placement {source_name}")
        parameters = resolve_parameters(component, ref)
        windows = tuple(
            Window(w.name, offset + w.base, w.size, w.width, w.registers)
            for w in component_windows(component, parameters)
        )
        instances.append(Instance(name, vlnv, windows, tuple(sorted(parameters.items())), path))
    if not instances:
        raise ContractError("design has no component instances")
    if set(placements) != source_names:
        raise ContractError("placements must explicitly name exactly all design instances")
    if not set(aliases).issubset(source_names):
        raise ContractError("instance_aliases contains unknown source paths")
    address_windows = sorted(
        (w.base, w.base + w.size, i.name) for i in instances for w in i.windows
    )
    previous_end = 0
    for start, end, name in address_windows:
        if end > 2**64:
            raise ContractError(f"{name}: address overflow")
        if start < previous_end:
            raise ContractError(f"{name}: overlapping address windows")
        previous_end = end
    return SoCIR(
        "1", identity(design), tuple(sorted(instances, key=lambda i: i.name)), tuple(sources)
    )
