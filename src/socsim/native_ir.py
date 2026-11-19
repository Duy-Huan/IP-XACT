"""Immutable native design IR. Both input formats lower through this contract."""

import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from jsonschema import ValidationError, validate

from .domain import (
    AddressSpace,
    ContractError,
    CpuContract,
    Instance,
    InterruptRoute,
    Register,
    RegisterField,
    SoCIR,
    Window,
)
from .native_bus_graph import BusGraph
from .native_cpu_binding import NativeCpuBinding, binding
from .native_fabric import Routing
from .project import keys
from .registers import verify_model_registers
from .soc_spec import (
    ROLE,
    WIDTH,
    cpu_descriptor,
    cpu_instances,
    cpu_reset,
    model_descriptor,
    validate_spec,
)

SCHEMA = Path(__file__).parent / "schemas/native-ir-v3.json"


@dataclass(frozen=True)
class Cpu:
    instance: Instance
    hart_id: int
    contract: CpuContract
    qemu_model: str
    gem5_model: str | None
    clock: str
    reset: str


@dataclass(frozen=True)
class Region:
    instance: Instance
    model: str
    irq: int | None


@dataclass(frozen=True)
class Clock:
    name: str
    frequency_hz: int


@dataclass(frozen=True)
class Reset:
    name: str
    kind: str
    targets: tuple[str, ...]


@dataclass(frozen=True)
class Boot:
    ram: str
    rom: str
    console: str
    entry: int


@dataclass(frozen=True)
class ExternalModel:
    model: str
    vlnv: str
    executable: str
    sha256: str
    license: str
    protocol: str
    compatible: str


@dataclass(frozen=True)
class NativeIR:
    schema_version: str
    name: str
    vlnv: str
    cpus: tuple[Cpu, ...]
    regions: tuple[Region, ...]
    clocks: tuple[Clock, ...]
    resets: tuple[Reset, ...]
    boot: Boot
    fabric: str
    address_spaces: tuple[AddressSpace, ...]
    interrupts: tuple[InterruptRoute, ...]
    memory_latency_ns: int
    external_models: tuple[ExternalModel, ...]
    sources: tuple[tuple[str, str], ...]
    routing: Routing | None = None
    bus_graph: BusGraph | None = None
    cpu_binding: NativeCpuBinding | None = None

    def to_dict(self):
        data = asdict(self)
        if self.routing is None:
            data.pop("routing")
        if self.bus_graph is None:
            data.pop("bus_graph")
        if self.cpu_binding is None:
            data.pop("cpu_binding")
        if self.schema_version == "6":
            for cpu in data["cpus"]:
                cpu["model"] = cpu.pop("qemu_model")
                cpu.pop("gem5_model")
        return json.loads(json.dumps(data))

    def to_spec(self):
        """Projection for existing native adapters; never re-read source YAML."""
        c = self.cpus[0]
        cpu = {
            "name": c.instance.name,
            "model": c.qemu_model,
            "harts": len(self.cpus),
            "clock_hz": next(x.frequency_hz for x in self.clocks if x.name == "cpu_clock"),
            "timebase_hz": c.contract.timebase_hz,
            "memory_latency_ns": self.memory_latency_ns,
        }
        if c.gem5_model is not None:
            cpu["gem5_model"] = c.gem5_model
        devices = []
        for r in self.regions:
            w = r.instance.windows[0]
            d = {"name": r.instance.name, "model": r.model, "base": w.base, "size": w.size}
            if r.irq is not None:
                d["irq"] = r.irq
            devices.append(d)
        external = {}
        for m in self.external_models:
            data = asdict(m)
            external[data.pop("model")] = data
        return validate_spec(
            {
                "schema_version": "1",
                "name": self.name,
                "cpu": cpu,
                "boot": asdict(self.boot),
                "devices": devices,
                "external_models": external,
            }
        )

    def structural_ir(self):
        return SoCIR(
            "1",
            self.vlnv,
            tuple(
                sorted(
                    (*(c.instance for c in self.cpus), *(r.instance for r in self.regions)),
                    key=lambda x: x.name,
                )
            ),
            self.sources,
            self.cpus[0].contract,
            self.interrupts,
            self.address_spaces,
        )


def compile_design(spec, structural):
    """Attach explicit executable bindings to XSD-elaborated structural data."""
    by_name = {i.name: i for i in structural.instances}
    names = cpu_instances(spec)
    if set(by_name) != set(names) | {d["name"] for d in spec["devices"]}:
        raise ContractError("E_NATIVE_BINDING instance inventory differs from IP-XACT")
    contract = CpuContract(
        "rv64imac_zicsr_zifencei",
        "MSU",
        "sv39",
        cpu_reset(spec),
        spec["boot"]["entry"],
        spec["cpu"]["timebase_hz"],
    )
    cpus = []
    for h, n in enumerate(names):
        i = by_name[n]
        if i.vlnv != cpu_descriptor(spec)["vlnv"] or i.windows or i.parameters:
            raise ContractError(f"E_NATIVE_CPU unsupported CPU declaration: {n}")
        cpus.append(
            Cpu(
                i,
                h,
                contract,
                spec["cpu"]["model"],
                spec["cpu"].get("gem5_model"),
                "cpu_clock",
                "cold_reset",
            )
        )
    regions = []
    for d in spec["devices"]:
        i = by_name[d["name"]]
        if i.vlnv != model_descriptor(spec, d["model"])["vlnv"] or len(i.windows) != 1:
            raise ContractError(f"E_NATIVE_BINDING exact VLNV/single window required: {i.name}")
        w = i.windows[0]
        if (w.base, w.size, w.width) != (d["base"], d["size"], WIDTH.get(d["model"], 32)):
            raise ContractError(f"E_NATIVE_WINDOW model address/size/width mismatch: {i.name}")
        verify_model_registers(replace(i, name=ROLE.get(d["model"], "external")))
        regions.append(Region(i, d["model"], d.get("irq")))
    controller = next(d["name"] for d in spec["devices"] if d["model"] == "plic")
    clint = next(d["name"] for d in spec["devices"] if d["model"] == "clint")
    irq = [
        InterruptRoute(d["name"], controller, d["irq"], "level-high")
        for d in spec["devices"]
        if "irq" in d
    ]
    for n in names:
        irq += [InterruptRoute(clint, n, line, "level-high") for line in (3, 7)]
        irq += [InterruptRoute(controller, n, line, "level-high") for line in (9, 11)]
    return NativeIR(
        "6" if binding(spec["cpu"]) else "3",
        spec["name"],
        structural.vlnv,
        tuple(cpus),
        tuple(regions),
        (
            Clock("cpu_clock", spec["cpu"]["clock_hz"]),
            Clock("rtc_clock", spec["cpu"]["timebase_hz"]),
        ),
        (Reset("cold_reset", "native-cold-boot", tuple(names)),),
        Boot(**spec["boot"]),
        "flat-functional-v1",
        tuple(
            AddressSpace(
                "physical" if h == 0 else f"physical_hart{h}",
                n,
                tuple(d["name"] for d in spec["devices"]),
            )
            for h, n in enumerate(names)
        ),
        tuple(irq),
        spec["cpu"]["memory_latency_ns"],
        tuple(ExternalModel(model=k, **v) for k, v in sorted(spec["external_models"].items())),
        tuple(structural.sources),
        cpu_binding=binding(spec["cpu"]),
    )


def instance_from_dict(d):
    keys(d, set(Instance.__dataclass_fields__), "canonical instance")
    windows = []
    for w in d["windows"]:
        keys(w, set(Window.__dataclass_fields__), "canonical window")
        for r in w["registers"]:
            keys(r, set(Register.__dataclass_fields__), "canonical register")
            for f in r["fields"]:
                keys(f, set(RegisterField.__dataclass_fields__), "canonical register field")
        regs = tuple(
            Register(
                r["name"],
                r["offset"],
                r["size"],
                r["access"],
                r["volatile"],
                tuple(RegisterField(**f) for f in r["fields"]),
            )
            for r in w["registers"]
        )
        windows.append(Window(w["name"], w["base"], w["size"], w["width"], regs))
    return Instance(
        d["name"],
        d["vlnv"],
        tuple(windows),
        tuple(tuple(p) for p in d["parameters"]),
        tuple(d["hierarchy_path"]),
    )


def read_ir(path):
    """Validate syntax and the entire executable graph, not just a spec projection."""
    try:
        if path.stat().st_size > 8 * 1024**2:
            raise ContractError("E_NATIVE_IR input exceeds 8 MiB")
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ContractError("E_NATIVE_IR canonical graph must be a mapping")
        schema = json.loads(SCHEMA.read_text())
        if data.get("schema_version") == "6":
            schema["properties"]["schema_version"] = {"const": "6"}
            cpu_schema = schema["properties"]["cpus"]["items"]
            cpu_schema["properties"]["model"] = cpu_schema["properties"].pop("qemu_model")
            cpu_schema["properties"].pop("gem5_model")
            cpu_schema["required"] = [
                "model" if n == "qemu_model" else n
                for n in cpu_schema["required"]
                if n != "gem5_model"
            ]
            schema["properties"]["cpu_binding"] = json.loads(
                SCHEMA.with_name("native-cpu-binding-v1.json").read_text()
            )
            schema["required"].append("cpu_binding")
            schema["properties"]["fabric"] = {
                "enum": ["flat-functional-v1", "hierarchical-static-decode-v1"]
            }
            schema["properties"]["routing"] = {"type": "object"}
            schema["properties"]["bus_graph"] = {"type": "object"}
        if data.get("schema_version") in {"4", "5"}:
            schema["properties"]["schema_version"] = {"const": data["schema_version"]}
            schema["properties"]["fabric"] = {"const": "hierarchical-static-decode-v1"}
            schema["properties"]["routing"] = {"type": "object"}
            schema["required"].append("routing")
        if data.get("schema_version") == "5":
            schema["properties"]["bus_graph"] = {"type": "object"}
            schema["required"].append("bus_graph")
        validate(data, schema)

        cpus = tuple(
            Cpu(
                instance_from_dict(c["instance"]),
                c["hart_id"],
                CpuContract(**c["contract"]),
                c["model"] if data["schema_version"] == "6" else c["qemu_model"],
                None if data["schema_version"] == "6" else c["gem5_model"],
                c["clock"],
                c["reset"],
            )
            for c in data["cpus"]
        )
        regions = tuple(
            Region(instance_from_dict(r["instance"]), r["model"], r["irq"]) for r in data["regions"]
        )
        ir = NativeIR(
            data["schema_version"],
            data["name"],
            data["vlnv"],
            cpus,
            regions,
            tuple(Clock(**c) for c in data["clocks"]),
            tuple(Reset(r["name"], r["kind"], tuple(r["targets"])) for r in data["resets"]),
            Boot(**data["boot"]),
            data["fabric"],
            tuple(
                AddressSpace(a["name"], a["initiator"], tuple(a["targets"]))
                for a in data["address_spaces"]
            ),
            tuple(InterruptRoute(**i) for i in data["interrupts"]),
            data["memory_latency_ns"],
            tuple(ExternalModel(**m) for m in data["external_models"]),
            tuple(tuple(s) for s in data["sources"]),
        )
        if data["schema_version"] == "6":
            b = data["cpu_binding"]
            ir = replace(
                ir,
                cpu_binding=NativeCpuBinding(
                    b["provider"],
                    b["model"],
                    tuple(tuple(x) for x in b["source_pins"]),
                    b["protocol"],
                    b["instruction_owner"],
                    tuple(tuple(x) for x in b["memory_contexts"]),
                    b["external_dma_coherence"],
                    b["timing_correlated"],
                ),
            )
            if (data["fabric"] == "hierarchical-static-decode-v1") != ("routing" in data):
                raise ContractError("E_NATIVE_IR inconsistent native CPU/fabric capability")
        if data["schema_version"] in {"4", "5"} or "routing" in data:
            from .native_fabric import parse_routing, verify_projection

            ir = replace(ir, routing=parse_routing(data["routing"]))
            verify_projection(ir.routing, ir.to_spec())
        if data["schema_version"] == "5" or "bus_graph" in data:
            from .native_bus_graph import read_graph, verify_projection

            ir = replace(ir, bus_graph=read_graph(data["bus_graph"]))
            verify_projection(ir.bus_graph, ir)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(:[A-Za-z0-9_.-]+){3}", ir.vlnv):
            raise ContractError("E_NATIVE_IR invalid design VLNV")
        for n, sha in ir.sources:
            if (
                Path(n).is_absolute()
                or ".." in Path(n).parts
                or not re.fullmatch("[0-9a-f]{64}", sha)
            ):
                raise ContractError("E_NATIVE_IR invalid source provenance")
        expected = compile_design(ir.to_spec(), ir.structural_ir())
        if ir.routing is not None:
            expected = replace(
                expected,
                schema_version=ir.schema_version,
                fabric="hierarchical-static-decode-v1",
                routing=ir.routing,
                bus_graph=ir.bus_graph,
            )
        if expected.to_dict() != data:
            raise ContractError(
                "E_NATIVE_IR unsupported/inconsistent CPU/clock/reset/fabric/IRQ graph"
            )
        return ir
    except (KeyError, TypeError, ValueError, StopIteration, ValidationError) as exc:
        raise ContractError(f"E_NATIVE_IR invalid canonical graph: {exc}") from exc
