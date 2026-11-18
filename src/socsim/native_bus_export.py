"""Export the qualified fabric subset to real IEEE 1685-2022 bus XML."""

from pathlib import Path

import yaml
from lxml import etree

from .domain import ContractError
from .ipxact import NS, PREFIX, digest, read_xml
from .native_bus_graph import ABSTRACTION, BUS, DECODER
from .native_fabric import read_source
from .runner import write_json
from .soc_spec import FIXED_SIZE, WIDTH, catalog, model_descriptor


def export_bus(source, out):
    source, out = source.resolve(), out.resolve()
    spec, routing = read_source(source)
    if spec["cpu"]["harts"] != 1 or spec["external_models"]:
        raise ContractError(
            "E_BUS_EXPORT currently qualifies one hart and built-in public model bindings"
        )
    names = {spec["cpu"]["name"], *(d["name"] for d in spec["devices"])}
    if names & {b.name for b in routing.bridges}:
        raise ContractError("E_BUS_EXPORT decoder/instance namespace collision")
    if out.exists():
        raise ContractError("preserve an existing export; use a fresh output")
    for s in routing.spaces:
        if not any(p.space == s.name for p in routing.placements) and not any(
            b.source == s.name for b in routing.bridges
        ):
            raise ContractError("E_BUS_EXPORT source bus cannot be empty")
    out.mkdir(parents=True)
    directory = out / "ipxact"
    directory.mkdir()

    def element(parent, tag, value=None, **attrs):
        e = etree.SubElement(parent, PREFIX + tag, **attrs)
        if value is not None:
            e.text = str(value)
        return e

    def reference(parent, tag, vlnv, **attrs):
        return element(
            parent,
            tag,
            **dict(zip(("vendor", "library", "name", "version"), vlnv.split(":"), strict=True)),
            **attrs,
        )

    def document(kind, vlnv):
        node = etree.Element(PREFIX + kind, nsmap={None: NS})
        for key, value in zip(
            ("vendor", "library", "name", "version"), vlnv.split(":"), strict=True
        ):
            element(node, key, value)
        return node

    def save(node, name):
        path = directory / name
        etree.ElementTree(node).write(
            str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True
        )
        read_xml(path)

    def interface(component, n, mode, ref):
        container = component.find(PREFIX + "busInterfaces")
        if container is None:
            container = element(component, "busInterfaces")
        i = element(container, "busInterface")
        element(i, "name", n)
        reference(i, "busType", BUS)
        a = element(element(i, "abstractionTypes"), "abstractionType")
        reference(a, "abstractionRef", ABSTRACTION)
        m = element(i, mode)
        attr = "addressSpaceRef" if mode == "initiator" else "memoryMapRef"
        element(m, attr, **{attr: ref})
        element(i, "connectionRequired", "true")
        element(i, "bitsInLau", 8)
        element(i, "bitSteering", 0)
        element(i, "endianness", "little")

    def space(component, n, capacity, segment=False):
        s = element(element(component, "addressSpaces"), "addressSpace")
        element(s, "name", n)
        element(s, "range", capacity)
        element(s, "width", 64)
        if segment:
            seg = element(element(s, "segments"), "segment")
            element(seg, "name", "aperture")
            element(seg, "addressOffset", "TARGET_BASE")
            element(seg, "range", "SIZE")
        element(s, "addressUnitBits", 8)

    def parameters(component, values):
        ps = element(component, "parameters")
        for n, v in values.items():
            p = element(
                ps, "parameter", parameterId=n, type="longint", sign="unsigned", resolve="user"
            )
            element(p, "name", n)
            element(p, "value", v)

    bus = document("busDefinition", BUS)
    element(bus, "directConnection", "true")
    element(bus, "broadcast", "false")
    element(bus, "isAddressable", "true")
    element(bus, "maxInitiators", 1)
    element(bus, "maxTargets", 64)
    save(bus, "bus.xml")
    abstraction = document("abstractionDefinition", ABSTRACTION)
    reference(abstraction, "busType", BUS)
    port = element(element(abstraction, "ports"), "port")
    element(port, "logicalName", "mmio")
    t = element(port, "transactional")
    for tag, initiative in [("onInitiator", "requires"), ("onTarget", "provides")]:
        p = element(t, tag)
        element(p, "presence", "required")
        element(p, "initiative", initiative)
    save(abstraction, "abstraction.xml")
    by_space = {s.name: s for s in routing.spaces}
    cpu = document("component", catalog()["rv64"]["vlnv"])
    interface(cpu, "initiator", "initiator", "physical")
    space(cpu, "physical", 1 << by_space[routing.root].address_bits)
    save(cpu, "cpu.xml")
    decoder = document("component", DECODER)
    interface(decoder, "target", "target", "map")
    interface(decoder, "out", "initiator", "out")
    space(decoder, "out", "CAPACITY", True)
    mm = element(element(decoder, "memoryMaps"), "memoryMap")
    element(mm, "name", "map")
    sub = element(mm, "subspaceMap", initiatorRef="out", segmentRef="aperture")
    element(sub, "name", "forward")
    element(sub, "baseAddress", "SOURCE_BASE")
    element(mm, "addressUnitBits", 8)
    parameters(decoder, {"SOURCE_BASE": 0, "TARGET_BASE": 0, "SIZE": 4096, "CAPACITY": 2**32})
    save(decoder, "decoder.xml")
    for kind in sorted({d["model"] for d in spec["devices"]}):
        component = document("component", model_descriptor(spec, kind)["vlnv"])
        interface(component, "target", "target", "map")
        mm = element(element(component, "memoryMaps"), "memoryMap")
        element(mm, "name", "map")
        block = element(mm, "addressBlock")
        for tag, value in [
            ("name", "region"),
            ("baseAddress", "BASE"),
            ("range", "SIZE"),
            ("width", WIDTH.get(kind, 32)),
            ("usage", "memory" if kind in {"ram", "rom"} else "register"),
        ]:
            element(block, tag, value)
        element(mm, "addressUnitBits", 8)
        parameters(component, {"BASE": 0, "SIZE": FIXED_SIZE.get(kind, 4096)})
        save(component, kind.replace(".", "_") + ".xml")
    design = document("design", f"socsim.native:generated:{spec['name']}:1.0")
    instances = element(design, "componentInstances")

    def add(n, vlnv, values):
        item = element(instances, "componentInstance")
        element(item, "instanceName", n)
        ref = reference(item, "componentRef", vlnv)
        if values:
            overrides = element(ref, "configurableElementValues")
            for key, value in values.items():
                element(overrides, "configurableElementValue", value, referenceId=key)

    add(spec["cpu"]["name"], catalog()["rv64"]["vlnv"], {})
    local = {p.instance: p.address for p in routing.placements}
    for d in spec["devices"]:
        add(
            d["name"],
            model_descriptor(spec, d["model"])["vlnv"],
            {"BASE": local[d["name"]], "SIZE": d["size"]},
        )
    for b in routing.bridges:
        add(
            b.name,
            DECODER,
            {
                "SOURCE_BASE": b.source_base,
                "TARGET_BASE": b.target_base,
                "SIZE": b.size,
                "CAPACITY": 1 << by_space[b.target].address_bits,
            },
        )
    connections = element(design, "interconnections")
    for index, s in enumerate(routing.spaces):
        incoming = next((b for b in routing.bridges if b.target == s.name), None)
        c = element(connections, "interconnection")
        element(c, "name", f"net{index}")
        element(
            c,
            "activeInterface",
            componentInstanceRef=incoming.name if incoming else spec["cpu"]["name"],
            busRef="out" if incoming else "initiator",
        )
        targets = [p.instance for p in routing.placements if p.space == s.name] + [
            b.name for b in routing.bridges if b.source == s.name
        ]
        for target in targets:
            element(c, "activeInterface", componentInstanceRef=target, busRef="target")
    save(design, "design.xml")
    aliases = {spec["cpu"]["name"] + ".physical": routing.root}
    aliases.update({b.name + ".out": b.target for b in routing.bridges})
    bindings = [{"instance": spec["cpu"]["name"], "model": "cpu"}]
    for d in spec["devices"]:
        b = {"instance": d["name"], "model": d["model"]}
        if "irq" in d:
            b["irq"] = d["irq"]
        bindings.append(b)
    bindings.extend({"instance": b.name, "model": "decoder"} for b in routing.bridges)
    overlay = {
        "schema_version": "native-ipxact-bus-1",
        "design": "ipxact/design.xml",
        "name": spec["name"],
        "cpu": spec["cpu"],
        "boot": spec["boot"],
        "bindings": bindings,
        "space_aliases": aliases,
    }
    (out / "native.yaml").write_text(yaml.safe_dump(overlay, sort_keys=False))
    from .native_bus_xml import import_bus_design

    actual = import_bus_design(out / "native.yaml")[0]
    if actual != spec:
        raise ContractError("E_BUS_EXPORT round-trip hardware mismatch")
    write_json(
        out / "export.json",
        {
            "contract": "native-bus-export-1",
            "source_sha256": digest(source),
            "exporter_sha256": digest(Path(__file__)),
            "files": {
                p.relative_to(out).as_posix(): digest(p) for p in out.rglob("*") if p.is_file()
            },
        },
    )
    return {
        "status": "EXPORTED",
        "format": "IEEE 1685-2022 qualified native bus profile",
        "directory": str(out),
    }
