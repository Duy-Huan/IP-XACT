"""Typed clock/reset domain graphs and per-initiator address translation views (M1).

Models explicit synchronous/CDC clock-reset domains and per-initiator address
translations without inferring asynchronous bridge or protocol behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .domain import ContractError
from .native_ir import NativeIR
from .project import keys
from .validation import (
    canonical_json_dict,
    require_bounded_int,
    require_identifier,
    require_unique,
)

SUPPORTED_RESET_POLARITIES = frozenset({"active-low", "active-high"})
SUPPORTED_RESET_KINDS = frozenset({"synchronous", "asynchronous", "native-cold-boot"})
SUPPORTED_CROSSING_POLICIES = frozenset(
    {"synchronous-ratio", "explicit-cdc-fifo", "async-reset-sync"}
)
SUPPORTED_VIEW_PROTOCOLS = frozenset(
    {"flat-functional-v1", "static-address-decoder-v1", "axi4-lite-32", "tilelink-ul-32"}
)
EXECUTABLE_NATIVE_PROTOCOLS = frozenset({"flat-functional-v1", "static-address-decoder-v1"})


@dataclass(frozen=True)
class ClockDomain:
    name: str
    frequency_hz: int
    parent: str | None = None


@dataclass(frozen=True)
class ResetDomain:
    name: str
    kind: str
    polarity: str
    clock: str
    release_order: int = 0


@dataclass(frozen=True)
class DomainBinding:
    instance: str
    clock: str
    reset: str
    hierarchy_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class DomainCrossing:
    source_instance: str
    target_instance: str
    policy: str


@dataclass(frozen=True)
class DomainGraph:
    clocks: tuple[ClockDomain, ...]
    resets: tuple[ResetDomain, ...]
    bindings: tuple[DomainBinding, ...]
    crossings: tuple[DomainCrossing, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return canonical_json_dict(self)


@dataclass(frozen=True)
class TranslationAperture:
    name: str
    local_base: int
    physical_base: int
    size: int
    target: str
    protocol: str = "static-address-decoder-v1"
    readable: bool = True
    writable: bool = True


@dataclass(frozen=True)
class InitiatorView:
    initiator: str
    address_bits: int
    apertures: tuple[TranslationAperture, ...]


@dataclass(frozen=True)
class AddressViewGraph:
    views: tuple[InitiatorView, ...]

    def to_dict(self) -> dict[str, Any]:
        return canonical_json_dict(self)


def validate_domain_graph(
    graph: DomainGraph,
    known_instances: set[str] | None = None,
    connections: tuple[tuple[str, str], ...] = (),
) -> DomainGraph:
    """Validate clock/reset domains, hierarchy bindings, and explicit CDC crossings."""
    if not graph.clocks or not graph.resets:
        raise ContractError("E_DOMAIN_GRAPH requires at least one clock and one reset domain")
    clocks = require_unique(graph.clocks, lambda c: c.name, "clock domain")
    for clk in graph.clocks:
        require_identifier(clk.name, "clock domain name")
        require_bounded_int(clk.frequency_hz, 1, 10**11, f"clock {clk.name} frequency_hz")
        if clk.parent is not None:
            if clk.parent not in clocks or clk.parent == clk.name:
                raise ContractError(f"E_DOMAIN_CLOCK unresolved/self parent clock: {clk.name}")

    # Detect clock parent cycles.
    for clk_name in clocks:
        visited: set[str] = set()
        current: str | None = clk_name
        while current is not None:
            if current in visited:
                raise ContractError(f"E_DOMAIN_CLOCK cycle in clock hierarchy: {clk_name}")
            visited.add(current)
            current = clocks[current].parent

    resets = require_unique(graph.resets, lambda r: r.name, "reset domain")
    for rst in graph.resets:
        require_identifier(rst.name, "reset domain name")
        if rst.kind not in SUPPORTED_RESET_KINDS:
            raise ContractError(f"E_DOMAIN_RESET unsupported reset kind: {rst.kind}")
        if rst.polarity not in SUPPORTED_RESET_POLARITIES:
            raise ContractError(f"E_DOMAIN_RESET unsupported reset polarity: {rst.polarity}")
        if rst.clock not in clocks:
            raise ContractError(f"E_DOMAIN_RESET reset {rst.name} references unknown clock")
        require_bounded_int(rst.release_order, 0, 64, f"reset {rst.name} release_order")

    bindings = require_unique(graph.bindings, lambda b: b.instance, "domain binding")
    for bind in graph.bindings:
        require_identifier(bind.instance, "bound instance")
        if bind.clock not in clocks:
            raise ContractError(f"E_DOMAIN_BINDING unknown clock {bind.clock} on {bind.instance}")
        if bind.reset not in resets:
            raise ContractError(f"E_DOMAIN_BINDING unknown reset {bind.reset} on {bind.instance}")
        if known_instances is not None and bind.instance not in known_instances:
            raise ContractError(f"E_DOMAIN_BINDING unknown instance: {bind.instance}")
        for segment in bind.hierarchy_path:
            require_identifier(segment, "hierarchy path segment")

    if known_instances is not None and set(bindings) != known_instances:
        missing = sorted(known_instances - set(bindings))
        raise ContractError(
            f"E_DOMAIN_BINDING missing clock/reset binding for: {', '.join(missing)}"
        )

    declared_crossings: dict[tuple[str, str], DomainCrossing] = {}
    for crossing in graph.crossings:
        if crossing.source_instance not in bindings or crossing.target_instance not in bindings:
            raise ContractError("E_DOMAIN_CROSSING references unbound instance")
        if crossing.policy not in SUPPORTED_CROSSING_POLICIES:
            raise ContractError(f"E_DOMAIN_CROSSING unsupported policy: {crossing.policy}")
        pair = (crossing.source_instance, crossing.target_instance)
        if pair in declared_crossings:
            raise ContractError(f"E_DOMAIN_CROSSING duplicate crossing: {pair}")
        src_clk = clocks[bindings[crossing.source_instance].clock]
        dst_clk = clocks[bindings[crossing.target_instance].clock]
        if crossing.policy == "synchronous-ratio" and (
            max(src_clk.frequency_hz, dst_clk.frequency_hz)
            % min(src_clk.frequency_hz, dst_clk.frequency_hz)
            != 0
        ):
            raise ContractError("E_DOMAIN_CROSSING synchronous-ratio requires integer clock ratio")
        declared_crossings[pair] = crossing

    for src, dst in connections:
        if src not in bindings or dst not in bindings:
            raise ContractError(f"E_DOMAIN_CROSSING unbound connected endpoint: {src}->{dst}")
        if (
            bindings[src].clock != bindings[dst].clock or bindings[src].reset != bindings[dst].reset
        ) and (
            src,
            dst,
        ) not in declared_crossings:
            raise ContractError(
                f"E_DOMAIN_CROSSING undeclared clock/reset crossing between {src} and {dst}"
            )

    return DomainGraph(
        tuple(sorted(graph.clocks, key=lambda c: c.name)),
        tuple(sorted(graph.resets, key=lambda r: (r.release_order, r.name))),
        tuple(sorted(graph.bindings, key=lambda b: b.instance)),
        tuple(sorted(graph.crossings, key=lambda x: (x.source_instance, x.target_instance))),
    )


def derive_domains_from_ir(ir: NativeIR) -> DomainGraph:
    """Derive a validated DomainGraph directly from a canonical NativeIR."""
    clocks = tuple(ClockDomain(c.name, c.frequency_hz) for c in ir.clocks)
    default_clock = ir.cpus[0].clock
    resets = tuple(
        ResetDomain(r.name, r.kind, "active-low", default_clock, idx)
        for idx, r in enumerate(ir.resets)
    )
    default_reset = ir.cpus[0].reset
    bindings = [
        DomainBinding(c.instance.name, c.clock, c.reset, c.instance.hierarchy_path) for c in ir.cpus
    ] + [
        DomainBinding(r.instance.name, default_clock, default_reset, r.instance.hierarchy_path)
        for r in ir.regions
    ]
    all_instances = {c.instance.name for c in ir.cpus} | {r.instance.name for r in ir.regions}
    return validate_domain_graph(DomainGraph(clocks, resets, tuple(bindings)), all_instances)


def parse_address_views(data: dict[str, Any]) -> AddressViewGraph:
    """Parse and validate a per-initiator address-space/translation view mapping."""
    keys(data, {"views"}, "address view graph")
    if not isinstance(data["views"], list) or not 1 <= len(data["views"]) <= 32:
        raise ContractError("E_VIEW_GRAPH requires 1..32 initiator views")
    views: list[InitiatorView] = []
    for raw_view in data["views"]:
        keys(raw_view, {"initiator", "address_bits", "apertures"}, "initiator view")
        initiator = require_identifier(raw_view["initiator"], "initiator")
        bits = require_bounded_int(raw_view["address_bits"], 12, 56, "address_bits")
        if (
            not isinstance(raw_view["apertures"], list)
            or not 1 <= len(raw_view["apertures"]) <= 128
        ):
            raise ContractError(f"E_VIEW_GRAPH initiator {initiator} requires 1..128 apertures")
        apertures: list[TranslationAperture] = []
        for raw_ap in raw_view["apertures"]:
            allowed = {"name", "local_base", "physical_base", "size", "target"} | (
                {"protocol", "readable", "writable"} & set(raw_ap)
            )
            keys(raw_ap, allowed, "translation aperture")
            protocol = raw_ap.get("protocol", "static-address-decoder-v1")
            if protocol not in SUPPORTED_VIEW_PROTOCOLS:
                raise ContractError(f"E_VIEW_PROTOCOL unsupported protocol: {protocol}")
            readable = raw_ap.get("readable", True)
            writable = raw_ap.get("writable", True)
            if (
                type(readable) is not bool
                or type(writable) is not bool
                or not (readable or writable)
            ):
                raise ContractError("E_VIEW_ACCESS aperture must allow read or write")
            apertures.append(
                TranslationAperture(
                    require_identifier(raw_ap["name"], "aperture name"),
                    require_bounded_int(raw_ap["local_base"], 0, (1 << bits) - 1, "local_base"),
                    require_bounded_int(raw_ap["physical_base"], 0, (1 << 56) - 1, "physical_base"),
                    require_bounded_int(raw_ap["size"], 1, 1 << bits, "aperture size"),
                    require_identifier(raw_ap["target"], "aperture target"),
                    protocol,
                    readable,
                    writable,
                )
            )
        views.append(
            InitiatorView(
                initiator,
                bits,
                tuple(sorted(apertures, key=lambda a: (a.local_base, a.name))),
            )
        )
    return resolve_initiator_views(
        AddressViewGraph(tuple(sorted(views, key=lambda v: v.initiator)))
    )


def resolve_initiator_views(
    graph: AddressViewGraph, target_sizes: dict[str, int] | None = None
) -> AddressViewGraph:
    """Validate overlap, alignment, address-width bounds and optional target bounds."""
    require_unique(graph.views, lambda v: v.initiator, "initiator view")
    for view in graph.views:
        limit = 1 << view.address_bits
        require_unique(view.apertures, lambda a: a.name, f"aperture in {view.initiator}")
        intervals: list[tuple[int, int, str]] = []
        for ap in view.apertures:
            if ap.local_base + ap.size > limit:
                raise ContractError(
                    f"E_VIEW_OVERFLOW aperture {ap.name} exceeds {view.address_bits}-bit space"
                )
            if target_sizes is not None:
                if ap.target not in target_sizes:
                    raise ContractError(f"E_VIEW_TARGET unresolved target: {ap.target}")
                if ap.size > target_sizes[ap.target]:
                    raise ContractError(f"E_VIEW_CLIPPING aperture {ap.name} exceeds target size")
            intervals.append((ap.local_base, ap.local_base + ap.size, ap.name))
        intervals.sort()
        for left, right in zip(intervals, intervals[1:], strict=False):
            if left[1] > right[0]:
                raise ContractError(
                    f"E_VIEW_OVERLAP overlapping apertures in {view.initiator}: "
                    f"{left[2]}, {right[2]}"
                )
    return graph


def check_lowering_capability(
    graph: AddressViewGraph,
    domains: DomainGraph | None = None,
    *,
    backend: str = "native-flat",
) -> dict[str, Any]:
    """Explicit capability check before lowering multi-initiator/domain graphs."""
    protocols = sorted({ap.protocol for v in graph.views for ap in v.apertures})
    unsupported_protocols = [p for p in protocols if p not in EXECUTABLE_NATIVE_PROTOCOLS]
    distinct_maps = {
        tuple((ap.target, ap.local_base, ap.physical_base, ap.size) for ap in v.apertures)
        for v in graph.views
    }
    async_crossings = (
        [c for c in domains.crossings if c.policy != "synchronous-ratio"] if domains else []
    )
    reasons: list[str] = []
    if unsupported_protocols:
        reasons.append(f"protocols require dedicated adapters: {', '.join(unsupported_protocols)}")
    if backend == "native-flat" and len(distinct_maps) > 1:
        reasons.append(
            "distinct per-initiator physical maps require per-initiator backend lowering"
        )
    if async_crossings:
        reasons.append("asynchronous clock/reset crossings require timed bridge adapters")
    return {
        "backend": backend,
        "status": "SUPPORTED" if not reasons else "UNSUPPORTED",
        "initiators": [v.initiator for v in graph.views],
        "protocols": protocols,
        "blocking_reasons": reasons,
    }
