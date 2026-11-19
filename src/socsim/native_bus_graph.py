"""Canonical facts for the qualified IEEE 1685-2022 static bus profile."""

from dataclasses import asdict, dataclass, replace

from .domain import ContractError, Instance
from .native_fabric import PROTOCOL, Bridge, Placement, Routing, Space, resolve
from .project import keys
from .soc_spec import catalog, name

BUS = "socsim.native:bus:mmio:1.0"
DECODER = "socsim.native:fabric:static_decoder:1.0"
ABSTRACTION = "socsim.native:abstraction:mmio:1.0"


@dataclass(frozen=True)
class BusDefinition:
    vlnv: str
    direct_connection: bool
    addressable: bool
    broadcast: bool
    max_initiators: int | None
    max_targets: int | None


@dataclass(frozen=True)
class BusNode:
    instance: Instance
    model: str


@dataclass(frozen=True)
class Segment:
    name: str
    offset: int
    size: int


@dataclass(frozen=True)
class BusSpace:
    instance: str
    name: str
    alias: str
    size: int
    width: int
    segments: tuple[Segment, ...]


@dataclass(frozen=True)
class Interface:
    instance: str
    name: str
    bus_type: str
    abstraction: str | None
    mode: str
    reference: str
    base: int
    bits_in_lau: int
    endianness: str


@dataclass(frozen=True)
class Subspace:
    instance: str
    memory_map: str
    name: str
    base: int
    initiator: str
    segment: str | None


@dataclass(frozen=True)
class Endpoint:
    instance: str
    interface: str


@dataclass(frozen=True)
class Connection:
    name: str
    endpoints: tuple[Endpoint, ...]


@dataclass(frozen=True)
class BusGraph:
    definition: BusDefinition
    nodes: tuple[BusNode, ...]
    spaces: tuple[BusSpace, ...]
    interfaces: tuple[Interface, ...]
    subspaces: tuple[Subspace, ...]
    connections: tuple[Connection, ...]

    def to_dict(self):
        import json

        return json.loads(json.dumps(asdict(self)))


def _unique(items, key, label):
    result = {key(x): x for x in items}
    if len(result) != len(items):
        raise ContractError("E_BUS_GRAPH duplicate " + label)
    return result


def _uint(value, label, positive=False):
    if type(value) is not int or not int(positive) <= value < 2**48 + int(positive):
        raise ContractError("E_BUS_GRAPH invalid " + label)
    return value


def project(graph):
    """Derive executable regions from graph facts, without re-reading XML."""
    from .native_cpu_binding import ROCKET_VLNV

    definition = graph.definition
    if (
        definition.vlnv != BUS
        or definition.direct_connection is not True
        or definition.addressable is not True
        or definition.broadcast is not False
    ):
        raise ContractError("E_BUS_CAPABILITY bus definition needs another adapter")
    for value in (definition.max_initiators, definition.max_targets):
        if value is not None and (type(value) is not int or not 1 <= value <= 128):
            raise ContractError("E_BUS_GRAPH invalid bus fanout bound")
    if (
        not 1 <= len(graph.nodes) <= 96
        or len(graph.interfaces) > 192
        or len(graph.connections) > 64
    ):
        raise ContractError("E_BUS_GRAPH graph exceeds native limits")
    nodes = _unique(graph.nodes, lambda n: n.instance.name, "instance")
    spaces = _unique(graph.spaces, lambda s: (s.instance, s.name), "address space")
    interfaces = _unique(graph.interfaces, lambda i: (i.instance, i.name), "interface")
    subspaces = _unique(graph.subspaces, lambda s: (s.instance, s.memory_map), "decoder map")
    _unique(graph.connections, lambda c: c.name, "connection")
    aliases = set()
    for s in graph.spaces:
        name(s.name)
        name(s.alias)
        if s.instance not in nodes or s.alias in aliases:
            raise ContractError("E_BUS_GRAPH unresolved/ambiguous address space alias")
        aliases.add(s.alias)
        if type(s.size) is not int or not 4096 <= s.size <= 2**48 or s.size & (s.size - 1):
            raise ContractError("E_BUS_CAPABILITY address-space range must be a power of two")
        if s.width != 64 or type(s.width) is not int:
            raise ContractError(
                "E_BUS_CAPABILITY only the qualified native data width is supported"
            )
        _unique(s.segments, lambda x: x.name, "segment")
        intervals = []
        for segment in s.segments:
            name(segment.name)
            _uint(segment.offset, "segment offset")
            _uint(segment.size, "segment size", True)
            if segment.offset + segment.size > s.size:
                raise ContractError("E_BUS_GRAPH segment exceeds address space")
            intervals.append((segment.offset, segment.offset + segment.size))
        intervals.sort()
        if any(a[1] > b[0] for a, b in zip(intervals, intervals[1:], strict=False)):
            raise ContractError("E_BUS_GRAPH overlapping segments")
    for i in graph.interfaces:
        name(i.name)
        if i.instance not in nodes or i.bus_type != BUS or i.abstraction not in {None, ABSTRACTION}:
            raise ContractError(
                "E_BUS_CAPABILITY unresolved interface or unsupported bus/abstraction"
            )
        if (
            i.base != 0
            or type(i.base) is not int
            or i.bits_in_lau != 8
            or type(i.bits_in_lau) is not int
            or i.endianness != "little"
        ):
            raise ContractError(
                "E_BUS_CAPABILITY interface offset/unit/endian adaptation unsupported"
            )
        if i.mode not in {"initiator", "target"} or not isinstance(i.reference, str):
            raise ContractError("E_BUS_GRAPH unsupported interface mode/reference")
        if i.mode == "initiator" and (i.instance, i.reference) not in spaces:
            raise ContractError("E_BUS_GRAPH unresolved initiator addressSpaceRef")
    memberships, parents = {}, {}
    for c in graph.connections:
        name(c.name)
        if not 2 <= len(c.endpoints) <= 65:
            raise ContractError("E_BUS_GRAPH connection requires an initiator and targets")
        attached = []
        for e in c.endpoints:
            key = (e.instance, e.interface)
            if key not in interfaces or key in memberships:
                raise ContractError("E_BUS_GRAPH missing or multiply connected endpoint")
            memberships[key] = c.name
            attached.append(interfaces[key])
        initiators = [i for i in attached if i.mode == "initiator"]
        targets = [i for i in attached if i.mode == "target"]
        if len(initiators) != 1 or not targets:
            raise ContractError("E_BUS_CAPABILITY native lowering needs one initiator per bus")
        if definition.max_targets is not None and len(targets) > definition.max_targets:
            raise ContractError("E_BUS_GRAPH target count exceeds bus definition")
        source = spaces[(initiators[0].instance, initiators[0].reference)]
        for i in targets:
            parents[(i.instance, i.name)] = source
    if set(memberships) != set(interfaces):
        raise ContractError("E_BUS_GRAPH disconnected interface")
    cpu_nodes = [n for n in graph.nodes if n.model == "cpu"]
    if len(cpu_nodes) != 1:
        raise ContractError("E_BUS_CAPABILITY source bus profile requires one native CPU")
    routes, placements, physical, used_spaces, used_maps = [], [], {}, set(), set()
    root = None
    for node in graph.nodes:
        instance = node.instance
        name(instance.name)
        own = [i for i in graph.interfaces if i.instance == instance.name]
        initiators = [i for i in own if i.mode == "initiator"]
        targets = [i for i in own if i.mode == "target"]
        if node.model == "cpu":
            if (
                instance.vlnv not in {catalog()["rv64"]["vlnv"], ROCKET_VLNV}
                or instance.windows
                or instance.parameters
                or len(own) != 1
                or len(initiators) != 1
            ):
                raise ContractError("E_BUS_CAPABILITY invalid CPU/interface declaration")
            root = spaces[(instance.name, initiators[0].reference)].alias
            used_spaces.add((instance.name, initiators[0].reference))
            physical[instance.name] = instance
            continue
        if len(targets) != 1:
            raise ContractError("E_BUS_GRAPH each component requires one target interface")
        target = targets[0]
        parent = parents[(instance.name, target.name)]
        if node.model == "decoder":
            if (
                instance.vlnv != DECODER
                or instance.windows
                or len(own) != 2
                or len(initiators) != 1
            ):
                raise ContractError(
                    "E_BUS_CAPABILITY decoder requires a qualified stateless binding"
                )
            key = (instance.name, target.reference)
            sub = subspaces.get(key)
            if sub is None or sub.initiator != initiators[0].name:
                raise ContractError("E_BUS_GRAPH unresolved decoder subspace/initiator")
            name(sub.name)
            _uint(sub.base, "subspace base")
            child = spaces[(instance.name, initiators[0].reference)]
            used_spaces.add((child.instance, child.name))
            used_maps.add(key)
            if sub.segment is None:
                offset, size = 0, child.size
            else:
                segments = [s for s in child.segments if s.name == sub.segment]
                if len(segments) != 1:
                    raise ContractError("E_BUS_GRAPH unresolved segmentRef")
                offset, size = segments[0].offset, segments[0].size
            params = dict(instance.parameters)
            if params and (
                len(params) != len(instance.parameters)
                or params
                != {
                    "SOURCE_BASE": sub.base,
                    "TARGET_BASE": offset,
                    "SIZE": size,
                    "CAPACITY": child.size,
                }
            ):
                raise ContractError(
                    "E_BUS_CAPABILITY decoder parameters differ from structural map"
                )
            routes.append(
                Bridge(instance.name, parent.alias, child.alias, sub.base, offset, size, PROTOCOL)
            )
        else:
            if len(own) != 1 or len(instance.windows) != 1:
                raise ContractError("E_BUS_CAPABILITY native leaf requires one memory-map block")
            w = instance.windows[0]
            _uint(w.base, "local block base")
            _uint(w.size, "local block size", True)
            if type(w.width) is not int or w.width not in {8, 16, 32, 64}:
                raise ContractError("E_BUS_GRAPH unsupported memory block width")
            if w.name.split(".")[0] != target.reference:
                raise ContractError("E_BUS_GRAPH unresolved target memoryMapRef")
            params = dict(instance.parameters)
            if (
                len(params) != len(instance.parameters)
                or set(params) - {"BASE", "SIZE"}
                or params.get("BASE", w.base) != w.base
                or params.get("SIZE", w.size) != w.size
            ):
                raise ContractError("E_BUS_CAPABILITY unqualified model parameters")
            placements.append(Placement(instance.name, parent.alias, w.base))
    if set(spaces) != used_spaces or set(subspaces) != used_maps or root is None:
        raise ContractError("E_BUS_GRAPH unused or unresolved address-space/map declarations")
    routing = Routing(
        root,
        tuple(
            sorted(
                (Space(s.alias, s.size.bit_length() - 1) for s in graph.spaces),
                key=lambda s: s.name,
            )
        ),
        tuple(sorted(routes, key=lambda b: b.name)),
        tuple(sorted(placements, key=lambda p: p.instance)),
    )
    sizes = {
        n.instance.name: n.instance.windows[0].size
        for n in graph.nodes
        if n.model not in {"cpu", "decoder"}
    }
    addresses = resolve(routing, sizes)
    for node in graph.nodes:
        if node.model in {"cpu", "decoder"}:
            continue
        i = node.instance
        physical[i.name] = replace(
            i,
            windows=(replace(i.windows[0], base=addresses[i.name][0]),),
            parameters=tuple(p for p in i.parameters if p[0] != "BASE"),
        )
    return routing, physical


def read_graph(data):
    from .native_ir import instance_from_dict

    keys(data, set(BusGraph.__dataclass_fields__), "canonical bus graph")
    for key in ("nodes", "spaces", "interfaces", "subspaces", "connections"):
        if not isinstance(data[key], list) or len(data[key]) > 192:
            raise ContractError("E_BUS_GRAPH bounded canonical arrays required")

    def construct(cls, value):
        keys(value, set(cls.__dataclass_fields__), cls.__name__)
        return cls(**value)

    nodes = []
    for n in data["nodes"]:
        keys(n, {"instance", "model"}, "bus node")
        nodes.append(BusNode(instance_from_dict(n["instance"]), n["model"]))
    spaces = []
    for s in data["spaces"]:
        keys(s, set(BusSpace.__dataclass_fields__), "bus space")
        if not isinstance(s["segments"], list) or len(s["segments"]) > 64:
            raise ContractError("E_BUS_GRAPH bounded segment inventory required")
        spaces.append(
            BusSpace(**dict(s, segments=tuple(construct(Segment, x) for x in s["segments"])))
        )
    connections = []
    for c in data["connections"]:
        keys(c, {"name", "endpoints"}, "bus connection")
        if not isinstance(c["endpoints"], list) or len(c["endpoints"]) > 65:
            raise ContractError("E_BUS_GRAPH invalid endpoint list")
        connections.append(
            Connection(c["name"], tuple(construct(Endpoint, e) for e in c["endpoints"]))
        )
    graph = BusGraph(
        construct(BusDefinition, data["definition"]),
        tuple(nodes),
        tuple(spaces),
        tuple(construct(Interface, i) for i in data["interfaces"]),
        tuple(construct(Subspace, s) for s in data["subspaces"]),
        tuple(connections),
    )
    project(graph)
    return graph


def verify_projection(graph, ir):
    routing, physical = project(graph)
    runtime = {c.instance.name: c.instance for c in ir.cpus}
    runtime.update({r.instance.name: r.instance for r in ir.regions})
    models = {c.instance.name: "cpu" for c in ir.cpus}
    models.update({r.instance.name: r.model for r in ir.regions})
    if (
        routing != ir.routing
        or physical != runtime
        or any(models.get(n.instance.name) != n.model for n in graph.nodes if n.model != "decoder")
    ):
        raise ContractError("E_BUS_GRAPH canonical bus graph differs from executable hardware")
