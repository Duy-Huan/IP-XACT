"""XSD-checked IEEE 1685-2022 buses, spaces and subspace-map intake.

The supported bus/abstraction/decoder VLNVs name explicit native functional
adapters. Other protocols are not silently treated as static MMIO.
"""

from dataclasses import replace

from lxml import etree

from .domain import ContractError, Instance, SoCIR
from .ipxact import (
    CHILDREN,
    LEAVES,
    NS,
    PREFIX,
    component_windows,
    digest,
    identity,
    integer,
    read_xml,
    text,
)
from .native_bus_graph import (
    ABSTRACTION,
    BUS,
    BusDefinition,
    BusGraph,
    BusNode,
    BusSpace,
    Connection,
    Endpoint,
    Interface,
    Segment,
    Subspace,
    project,
)
from .parameters import expression, resolve_parameters
from .project import inside, keys, read_yaml
from .soc_spec import cpu_instances, name, validate_spec

EXTRA = {
    "component": CHILDREN["component"] | {"busInterfaces", "addressSpaces"},
    "design": CHILDREN["design"] | {"interconnections"},
    "memoryMap": CHILDREN["memoryMap"] | {"subspaceMap"},
    "busDefinition": {
        "vendor",
        "library",
        "name",
        "version",
        "directConnection",
        "isAddressable",
        "broadcast",
        "maxInitiators",
        "maxTargets",
    },
    "abstractionDefinition": {"vendor", "library", "name", "version", "busType", "ports"},
    "ports": {"port"},
    "port": {"logicalName", "match", "transactional"},
    "transactional": {"onInitiator", "onTarget"},
    "onInitiator": {"presence", "initiative"},
    "onTarget": {"presence", "initiative"},
    "busInterfaces": {"busInterface"},
    "busInterface": {
        "name",
        "busType",
        "abstractionTypes",
        "initiator",
        "target",
        "connectionRequired",
        "bitsInLau",
        "bitSteering",
        "endianness",
    },
    "abstractionTypes": {"abstractionType"},
    "abstractionType": {"abstractionRef"},
    "busType": set(),
    "abstractionRef": set(),
    "initiator": {"addressSpaceRef"},
    "addressSpaceRef": {"baseAddress"},
    "target": {"memoryMapRef"},
    "memoryMapRef": set(),
    "addressSpaces": {"addressSpace"},
    "addressSpace": {"name", "range", "width", "segments", "addressUnitBits"},
    "segments": {"segment"},
    "segment": {"name", "addressOffset", "range"},
    "subspaceMap": {"name", "baseAddress"},
    "interconnections": {"interconnection"},
    "interconnection": {"name", "activeInterface"},
    "activeInterface": set(),
}
NEW_LEAVES = {
    "directConnection",
    "isAddressable",
    "broadcast",
    "maxInitiators",
    "maxTargets",
    "connectionRequired",
    "bitsInLau",
    "bitSteering",
    "endianness",
    "logicalName",
    "match",
    "presence",
    "initiative",
}


def check_subset(root, source):
    children = {**CHILDREN, **EXTRA}
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        tag = etree.QName(node).localname
        if etree.QName(node).namespace != NS or tag not in children.keys() | LEAVES | NEW_LEAVES:
            raise ContractError(f"E_BUS_UNSUPPORTED {source}:{node.sourceline} element {tag}")
        attrs = set()
        if tag in {"componentRef", "busType", "abstractionRef"}:
            attrs = {"vendor", "library", "name", "version"}
        if tag == "parameter":
            attrs = {"parameterId", "type", "sign", "resolve", "minimum", "maximum"}
        if tag == "configurableElementValue":
            attrs = {"referenceId"}
        if tag == "addressSpaceRef":
            attrs = {"addressSpaceRef"}
        if tag == "memoryMapRef":
            attrs = {"memoryMapRef"}
        if tag == "subspaceMap":
            attrs = {"initiatorRef", "segmentRef"}
        if tag == "activeInterface":
            attrs = {"componentInstanceRef", "busRef"}
        if set(node.attrib) - attrs:
            raise ContractError(f"E_BUS_UNSUPPORTED {source}:{node.sourceline} attributes on {tag}")
        for child in node:
            if isinstance(child.tag, str) and etree.QName(child).localname not in children.get(
                tag, set()
            ):
                raise ContractError(f"E_BUS_UNSUPPORTED {source}:{child.sourceline} child of {tag}")


def reference(node):
    if node is None:
        raise ContractError("E_BUS_REFERENCE missing VLNV reference")
    value = ":".join(node.get(k, "") for k in ("vendor", "library", "name", "version"))
    if any(not part for part in value.split(":")) or len(value.split(":")) != 4:
        raise ContractError("E_BUS_REFERENCE incomplete VLNV")
    return value


def boolean(value):
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise ContractError("E_BUS_VALUE invalid boolean")


def validate_abstraction(root):
    if identity(root) != ABSTRACTION or reference(root.find(PREFIX + "busType")) != BUS:
        raise ContractError("E_BUS_CAPABILITY unknown abstraction adapter")
    ports = root.findall(f"{PREFIX}ports/{PREFIX}port")
    if (
        len(ports) != 1
        or text(ports[0], "logicalName") != "mmio"
        or boolean(ports[0].findtext(PREFIX + "match", "false"))
    ):
        raise ContractError("E_BUS_CAPABILITY abstraction needs the qualified logical MMIO port")
    for mode, initiative in [("onInitiator", "requires"), ("onTarget", "provides")]:
        node = ports[0].find(f"{PREFIX}transactional/{PREFIX}{mode}")
        if (
            node is None
            or node.findtext(PREFIX + "presence") != "required"
            or node.findtext(PREFIX + "initiative") != initiative
        ):
            raise ContractError(
                "E_BUS_CAPABILITY abstraction port directions differ from native adapter"
            )


def import_bus_design(path):
    from .native_ir import compile_design

    path = path.resolve()
    root = path.parent
    data = read_yaml(path)
    keys(
        data,
        {"schema_version", "design", "name", "cpu", "boot", "bindings", "space_aliases"}
        | ({"external_models"} if "external_models" in data else set()),
        "native bus overlay",
    )
    if data["schema_version"] != "native-ipxact-bus-1":
        raise ContractError("E_BUS_VERSION unsupported overlay")
    design_path = inside(root, data["design"])
    if not design_path.is_relative_to(root / "ipxact") or design_path.suffix != ".xml":
        raise ContractError("E_BUS_REFERENCE design must be under ipxact/")
    files = sorted((root / "ipxact").rglob("*.xml"))
    if len(files) > 128 or sum(p.stat().st_size for p in files) > 16 * 1024**2:
        raise ContractError("E_BUS_LIMIT XML catalog exceeds intake bounds")
    catalog = {}
    sources = []
    for p in files:
        if not p.resolve().is_relative_to(root):
            raise ContractError("E_BUS_REFERENCE XML source escapes project")
        node = read_xml(p)
        check_subset(node, p.relative_to(root).as_posix())
        vlnv = identity(node)
        if vlnv in catalog:
            raise ContractError("E_BUS_REFERENCE duplicate catalog VLNV")
        catalog[vlnv] = node
        sources.append((p.relative_to(root).as_posix(), digest(p)))
    design = read_xml(design_path)
    if design.tag != PREFIX + "design":
        raise ContractError("E_BUS_UNSUPPORTED bus profile needs a flat explicit design")
    bindings = data["bindings"]
    aliases = data["space_aliases"]
    if not isinstance(bindings, list) or not isinstance(aliases, dict):
        raise ContractError("E_BUS_BINDING explicit bindings/space aliases required")
    by_binding = {}
    for b in bindings:
        keys(b, {"instance", "model"} | ({"irq"} if "irq" in b else set()), "bus instance binding")
        n = name(b["instance"])
        if n in by_binding or not isinstance(b["model"], str):
            raise ContractError("E_BUS_BINDING ambiguous model")
        if b["model"] in {"cpu", "decoder"} and "irq" in b:
            raise ContractError("E_BUS_BINDING CPU/decoder cannot supply a peripheral IRQ")
        by_binding[n] = b
    definition_node = catalog.get(BUS)
    if definition_node is None or definition_node.tag != PREFIX + "busDefinition":
        raise ContractError("E_BUS_CAPABILITY missing qualified bus definition")

    def optional(n):
        if definition_node.find(PREFIX + n) is None:
            return None
        return integer(definition_node.findtext(PREFIX + n), n)

    definition = BusDefinition(
        BUS,
        boolean(text(definition_node, "directConnection")),
        boolean(text(definition_node, "isAddressable")),
        boolean(definition_node.findtext(PREFIX + "broadcast", "false")),
        optional("maxInitiators"),
        optional("maxTargets"),
    )
    nodes = []
    spaces = []
    interfaces = []
    subspaces = []
    seen = set()
    used_aliases = set()
    for item in design.findall(f"{PREFIX}componentInstances/{PREFIX}componentInstance"):
        instance = name(text(item, "instanceName"))
        if instance in seen or instance not in by_binding:
            raise ContractError("E_BUS_BINDING unknown/duplicate instance")
        seen.add(instance)
        ref = item.find(PREFIX + "componentRef")
        vlnv = reference(ref)
        component = catalog.get(vlnv)
        if component is None or component.tag != PREFIX + "component":
            raise ContractError("E_BUS_REFERENCE unresolved component")
        if component.find(PREFIX + "model") is not None:
            raise ContractError("E_BUS_UNSUPPORTED structural views/HDL ports need another adapter")
        params = resolve_parameters(component, ref)

        def number(value, label, values=params):
            def lookup(parameter):
                if parameter not in values:
                    raise ContractError("E_BUS_PARAMETER unresolved " + parameter)
                return values[parameter]

            return expression(value, lookup, label)

        local = Instance(
            instance,
            vlnv,
            component_windows(component, params),
            tuple(sorted(params.items())),
            (instance,),
        )
        nodes.append(BusNode(local, by_binding[instance]["model"]))
        for space in component.findall(f"{PREFIX}addressSpaces/{PREFIX}addressSpace"):
            local_name = name(text(space, "name"))
            key = instance + "." + local_name
            if key not in aliases:
                raise ContractError("E_BUS_BINDING missing explicit space alias " + key)
            used_aliases.add(key)
            if number(space.findtext(PREFIX + "addressUnitBits", "8"), "addressUnitBits") != 8:
                raise ContractError("E_BUS_CAPABILITY non-byte address space")
            segments = tuple(
                Segment(
                    name(text(s, "name")),
                    number(text(s, "addressOffset"), "segment offset"),
                    number(text(s, "range"), "segment range"),
                )
                for s in space.findall(f"{PREFIX}segments/{PREFIX}segment")
            )
            spaces.append(
                BusSpace(
                    instance,
                    local_name,
                    name(aliases[key]),
                    number(text(space, "range"), "space range"),
                    number(text(space, "width"), "space width"),
                    segments,
                )
            )
        for interface in component.findall(f"{PREFIX}busInterfaces/{PREFIX}busInterface"):
            bus = reference(interface.find(PREFIX + "busType"))
            if bus != BUS:
                raise ContractError("E_BUS_CAPABILITY unsupported bus VLNV " + bus)
            abs_nodes = interface.findall(f"{PREFIX}abstractionTypes/{PREFIX}abstractionType")
            if len(abs_nodes) > 1:
                raise ContractError("E_BUS_CAPABILITY ambiguous abstraction selection")
            abstraction = None
            if abs_nodes:
                abstraction = reference(abs_nodes[0].find(PREFIX + "abstractionRef"))
                declaration = catalog.get(abstraction)
                if declaration is None:
                    raise ContractError("E_BUS_REFERENCE missing abstraction definition")
                validate_abstraction(declaration)
            boolean(interface.findtext(PREFIX + "connectionRequired", "false"))
            if number(interface.findtext(PREFIX + "bitSteering", "0"), "bitSteering") != 0:
                raise ContractError("E_BUS_CAPABILITY bit steering requires another adapter")
            initiator = interface.find(PREFIX + "initiator")
            mode = "initiator" if initiator is not None else "target"
            parent = initiator if initiator is not None else interface.find(PREFIX + "target")
            target = parent.find(
                PREFIX + ("addressSpaceRef" if mode == "initiator" else "memoryMapRef")
            )
            if target is None:
                raise ContractError("E_BUS_REFERENCE missing address-space/memory-map reference")
            reference_name = target.get(
                "addressSpaceRef" if mode == "initiator" else "memoryMapRef"
            )
            interfaces.append(
                Interface(
                    instance,
                    name(text(interface, "name")),
                    bus,
                    abstraction,
                    mode,
                    reference_name,
                    number(target.findtext(PREFIX + "baseAddress", "0"), "interface base"),
                    number(interface.findtext(PREFIX + "bitsInLau", "8"), "bitsInLau"),
                    interface.findtext(PREFIX + "endianness", "little"),
                )
            )
        for memory_map in component.findall(f"{PREFIX}memoryMaps/{PREFIX}memoryMap"):
            if number(memory_map.findtext(PREFIX + "addressUnitBits", "8"), "addressUnitBits") != 8:
                raise ContractError("E_BUS_CAPABILITY non-byte memory map")
            for sub in memory_map.findall(PREFIX + "subspaceMap"):
                subspaces.append(
                    Subspace(
                        instance,
                        text(memory_map, "name"),
                        text(sub, "name"),
                        number(text(sub, "baseAddress"), "subspace base"),
                        sub.get("initiatorRef"),
                        sub.get("segmentRef"),
                    )
                )
    if seen != set(by_binding) or used_aliases != set(aliases):
        raise ContractError("E_BUS_BINDING extra/missing binding/space alias")
    connections = []
    for connection in design.findall(f"{PREFIX}interconnections/{PREFIX}interconnection"):
        endpoints = tuple(
            Endpoint(n.get("componentInstanceRef"), n.get("busRef"))
            for n in connection.findall(PREFIX + "activeInterface")
        )
        connections.append(Connection(text(connection, "name"), endpoints))
    graph = BusGraph(
        definition,
        tuple(sorted(nodes, key=lambda n: n.instance.name)),
        tuple(sorted(spaces, key=lambda s: (s.instance, s.name))),
        tuple(sorted(interfaces, key=lambda i: (i.instance, i.name))),
        tuple(sorted(subspaces, key=lambda s: (s.instance, s.memory_map))),
        tuple(sorted(connections, key=lambda c: c.name)),
    )
    routing, physical = project(graph)
    devices = []
    cpus = []
    for b in bindings:
        if b["model"] == "decoder":
            continue
        if b["model"] == "cpu":
            cpus.append(b["instance"])
            continue
        w = physical[b["instance"]].windows[0]
        device = {"name": b["instance"], "model": b["model"], "base": w.base, "size": w.size}
        if "irq" in b:
            device["irq"] = b["irq"]
        devices.append(device)
    spec = validate_spec(
        {
            "schema_version": "1",
            "name": data["name"],
            "cpu": data["cpu"],
            "boot": data["boot"],
            "devices": devices,
            "external_models": data.get("external_models", {}),
        }
    )
    if cpus != cpu_instances(spec):
        raise ContractError("E_BUS_CAPABILITY CPU binding/hart inventory differs")
    sources.append((path.name, digest(path)))
    structural = SoCIR(
        "1",
        identity(design),
        tuple(sorted(physical.values(), key=lambda i: i.name)),
        tuple(sorted(sources)),
    )
    canonical = replace(
        compile_design(spec, structural),
        schema_version="5",
        fabric="hierarchical-static-decode-v1",
        routing=routing,
        bus_graph=graph,
    )
    return spec, canonical, root, data["design"], {}
