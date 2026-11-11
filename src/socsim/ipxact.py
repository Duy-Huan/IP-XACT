"""Offline XSD validation and a deliberately small fail-closed 2022 subset."""

import hashlib
import re
from functools import lru_cache
from pathlib import Path

from lxml import etree

from .domain import ContractError, Instance, SoCIR, Window

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
    "component": {"vendor", "library", "name", "version", "memoryMaps"},
    "design": {"vendor", "library", "name", "version", "componentInstances"},
    "componentInstances": {"componentInstance"},
    "componentInstance": {"instanceName", "componentRef"},
    "memoryMaps": {"memoryMap"},
    "memoryMap": {"name", "addressBlock", "addressUnitBits"},
    "addressBlock": {"name", "baseAddress", "range", "width", "usage"},
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
}


def check_subset(root: etree._Element, source: str) -> None:
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        tag = etree.QName(node).localname
        if etree.QName(node).namespace != NS or tag not in CHILDREN.keys() | LEAVES:
            raise ContractError(f"{source}:{node.sourceline}: UNSUPPORTED element {tag}")
        allowed_attributes = (
            {"vendor", "library", "name", "version"} if tag == "componentRef" else set()
        )
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


def component_windows(root: etree._Element) -> tuple[Window, ...]:
    windows = []
    names = set()
    for memory_map in root.findall(f"{PREFIX}memoryMaps/{PREFIX}memoryMap"):
        units = memory_map.findtext(PREFIX + "addressUnitBits", "8")
        if integer(units, "addressUnitBits") != 8:
            raise ContractError("UNSUPPORTED non-byte addressUnitBits")
        for block in memory_map.findall(PREFIX + "addressBlock"):
            name = f"{text(memory_map, 'name')}.{text(block, 'name')}"
            if name in names:
                raise ContractError(f"duplicate address block {name}")
            names.add(name)
            usage = block.findtext(PREFIX + "usage", "memory")
            if usage not in {"memory", "register"}:
                raise ContractError(f"UNSUPPORTED address block usage: {usage}")
            base = integer(text(block, "baseAddress"), name)
            size = integer(text(block, "range"), name)
            width = integer(text(block, "width"), name)
            if size == 0 or width not in {8, 16, 32, 64} or base + size > 2**64:
                raise ContractError(f"{name}: invalid range/width")
            windows.append(Window(name, base, size, width))
    return tuple(sorted(windows, key=lambda w: (w.base, w.name)))


def elaborate(project: Path, placements: dict, design_path: Path) -> SoCIR:
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
    if design.tag != PREFIX + "design":
        raise ContractError("selected input is not a design")
    instances = []
    seen = set()
    for item in design.findall(f"{PREFIX}componentInstances/{PREFIX}componentInstance"):
        name = text(item, "instanceName")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name in seen:
            raise ContractError(f"duplicate/unsafe instance name {name}")
        seen.add(name)
        ref = item.find(PREFIX + "componentRef")
        vlnv = ":".join(ref.get(k, "") for k in ("vendor", "library", "name", "version"))
        component = catalog.get(vlnv)
        if component is None or component.tag != PREFIX + "component":
            raise ContractError(f"{name}: unresolved component VLNV {vlnv}")
        offset = integer(placements.get(name, 0), f"placement {name}")
        windows = tuple(
            Window(w.name, offset + w.base, w.size, w.width) for w in component_windows(component)
        )
        instances.append(Instance(name, vlnv, windows))
    if not instances:
        raise ContractError("design has no component instances")
    if set(placements) != seen:
        raise ContractError("placements must explicitly name exactly all design instances")
    windows = sorted((w.base, w.base + w.size, i.name) for i in instances for w in i.windows)
    previous_end = 0
    for start, end, name in windows:
        if end > 2**64:
            raise ContractError(f"{name}: address overflow")
        if start < previous_end:
            raise ContractError(f"{name}: overlapping address windows")
        previous_end = end
    return SoCIR(
        "1", identity(design), tuple(sorted(instances, key=lambda i: i.name)), tuple(sources)
    )
