"""Typed, bounded address-decoder trees with lossless native IR provenance.

Static decoding can be lowered to one CPU-visible map. It does not create an
AXI/TileLink transaction model, asynchronous bridge or timing/coherence adapter.
"""

import copy
from dataclasses import asdict, dataclass

from .domain import ContractError
from .ipxact import integer
from .project import keys, read_yaml
from .soc_spec import name, validate_spec

PROTOCOL = "static-address-decoder-v1"


@dataclass(frozen=True)
class Space:
    name: str
    address_bits: int


@dataclass(frozen=True)
class Bridge:
    name: str
    source: str
    target: str
    source_base: int
    target_base: int
    size: int
    protocol: str


@dataclass(frozen=True)
class Placement:
    instance: str
    space: str
    address: int


@dataclass(frozen=True)
class Routing:
    root: str
    spaces: tuple[Space, ...]
    bridges: tuple[Bridge, ...]
    placements: tuple[Placement, ...]

    def to_dict(self):
        import json

        return json.loads(json.dumps(asdict(self)))


def parse_routing(data):
    """Normalize source integers, retaining canonical deterministic node order."""
    keys(data, {"root", "spaces", "bridges", "placements"}, "native routing")
    name(data["root"])
    for field, limit in (("spaces", 32), ("bridges", 31), ("placements", 63)):
        if not isinstance(data[field], list) or len(data[field]) > limit:
            raise ContractError("E_FABRIC bounded routing arrays required")
    spaces, bridges, placements = [], [], []
    for item in data["spaces"]:
        keys(item, {"name", "address_bits"}, "address space")
        bits = integer(item["address_bits"], "address_bits")
        if not 12 <= bits <= 48:
            raise ContractError("E_FABRIC native address spaces require 12..48 bits")
        spaces.append(Space(name(item["name"]), bits))
    for item in data["bridges"]:
        keys(item, set(Bridge.__dataclass_fields__), "decoder bridge")
        if item["protocol"] != PROTOCOL:
            raise ContractError("E_FABRIC protocol needs an executable adapter")
        bridges.append(
            Bridge(
                name(item["name"]),
                name(item["source"]),
                name(item["target"]),
                integer(item["source_base"], "source_base"),
                integer(item["target_base"], "target_base"),
                integer(item["size"], "size"),
                PROTOCOL,
            )
        )
    for item in data["placements"]:
        keys(item, set(Placement.__dataclass_fields__), "local placement")
        placements.append(
            Placement(
                name(item["instance"]), name(item["space"]), integer(item["address"], "address")
            )
        )
    return Routing(
        data["root"],
        tuple(sorted(spaces, key=lambda s: s.name)),
        tuple(sorted(bridges, key=lambda b: b.name)),
        tuple(sorted(placements, key=lambda p: p.instance)),
    )


def resolve(routing, sizes):
    """Return (physical base, ordered bridge path) for every declared instance."""
    spaces = {s.name: s for s in routing.spaces}
    if len(spaces) != len(routing.spaces) or routing.root not in spaces:
        raise ContractError("E_FABRIC duplicate space or unresolved root")
    names = [b.name for b in routing.bridges]
    if len(names) != len(set(names)) or set(names) & set(spaces):
        raise ContractError("E_FABRIC ambiguous bridge identity")
    parents = {}
    occupied: dict[str, list[tuple[int, int, str]]] = {n: [] for n in spaces}
    for b in routing.bridges:
        if b.source not in spaces or b.target not in spaces or b.source == b.target:
            raise ContractError("E_FABRIC unresolved/self bridge")
        if b.target == routing.root or b.target in parents:
            raise ContractError("E_FABRIC root bridge or multiple parents/aliases unsupported")
        if b.protocol != PROTOCOL or min(b.source_base, b.target_base) < 0 or b.size <= 0:
            raise ContractError("E_FABRIC invalid decoder interval/protocol")
        if any(v % 4096 for v in (b.source_base, b.target_base, b.size)):
            raise ContractError("E_FABRIC decoder apertures must be 4 KiB aligned")
        if (
            b.source_base + b.size > 1 << spaces[b.source].address_bits
            or b.target_base + b.size > 1 << spaces[b.target].address_bits
        ):
            raise ContractError("E_FABRIC bridge interval exceeds address-space width")
        parents[b.target] = b
        occupied[b.source].append((b.source_base, b.source_base + b.size, b.name))
    # Check every space, including disconnected/cyclic branches without devices.
    for s in spaces:
        visited = set()
        while s != routing.root:
            if s in visited or s not in parents:
                raise ContractError("E_FABRIC cycle or disconnected address space")
            visited.add(s)
            if len(visited) > 16:
                raise ContractError("E_FABRIC decoder hierarchy exceeds 16 levels")
            s = parents[s].source
    placements = {p.instance: p for p in routing.placements}
    if len(placements) != len(routing.placements) or set(placements) != set(sizes):
        raise ContractError("E_FABRIC placement inventory differs from actual regions")
    result = {}
    for p in routing.placements:
        size = sizes[p.instance]
        if p.space not in spaces or p.address < 0 or type(size) is not int or size <= 0:
            raise ContractError("E_FABRIC invalid local target")
        if p.address + size > 1 << spaces[p.space].address_bits:
            raise ContractError("E_FABRIC local target exceeds address-space width")
        occupied[p.space].append((p.address, p.address + size, p.instance))
        s, address, path = p.space, p.address, []
        while s != routing.root:
            b = parents[s]
            if not b.target_base <= address or address + size > b.target_base + b.size:
                raise ContractError("E_FABRIC target is outside a bridge aperture")
            address = b.source_base + address - b.target_base
            path.append(b.name)
            s = b.source
        result[p.instance] = (address, tuple(reversed(path)))
    for s, intervals in occupied.items():
        intervals.sort()
        if any(left[1] > right[0] for left, right in zip(intervals, intervals[1:], strict=False)):
            raise ContractError("E_FABRIC overlapping local decode windows")
        # Equal bases must also fail, including a bridge shadowed by a target.
        if len({x[0] for x in intervals}) != len(intervals):
            raise ContractError("E_FABRIC overlapping local decode windows")
        if s in parents:
            b = parents[s]
            if any(lo < b.target_base or hi > b.target_base + b.size for lo, hi, _ in intervals):
                raise ContractError("E_FABRIC nested aperture lies outside parent bridge")
    return result


def read_source(path):
    data = read_yaml(path)
    keys(
        data,
        {"schema_version", "name", "cpu", "boot", "devices", "routing"}
        | ({"external_models"} if "external_models" in data else set()),
        "native fabric source",
    )
    if data["schema_version"] != "native-fabric-1":
        raise ContractError("E_FABRIC unsupported source version")
    routing = parse_routing(data["routing"])
    if not isinstance(data["devices"], list) or not 5 <= len(data["devices"]) <= 63:
        raise ContractError("E_FABRIC bounded device inventory required")
    sizes = {}
    for d in data["devices"]:
        keys(d, {"name", "model", "size"} | ({"irq"} if "irq" in d else set()), "fabric device")
        n = name(d["name"])
        if n in sizes:
            raise ContractError("E_FABRIC duplicate device identity")
        sizes[n] = integer(d["size"], "region size")
    addresses = resolve(routing, sizes)
    spec = copy.deepcopy({k: v for k, v in data.items() if k != "routing"})
    spec["schema_version"] = "1"
    for d in spec["devices"]:
        d["size"] = sizes[d["name"]]
        d["base"] = addresses[d["name"]][0]
    return validate_spec(spec), routing


def verify_projection(routing, spec):
    resolved = resolve(routing, {d["name"]: d["size"] for d in spec["devices"]})
    if any(resolved[d["name"]][0] != d["base"] for d in spec["devices"]):
        raise ContractError("E_FABRIC canonical routing and native physical map differ")
