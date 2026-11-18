"""Spec frontend for native SoC generation, independent of legacy board roles."""

import json
import re
from dataclasses import replace
from pathlib import Path

from lxml import etree

from .domain import AddressSpace, ContractError, CpuContract, InterruptRoute
from .ipxact import NS, PREFIX, digest, elaborate, integer
from .pipeline import QEMU_SHA
from .project import keys, read_yaml
from .runner import write_json

CATALOG = Path(__file__).parent / "schemas/model-catalog-v1.json"
ROLE = {
    "ram": "ram",
    "rom": "rom",
    "clint": "clint",
    "plic": "plic",
    "uart": "uart",
    "finisher": "finisher",
    "chipyard.gcd": "cygcd",
    "opentitan.gpio": "otgpio",
}
FIXED_SIZE = {
    "clint": 0x10000,
    "plic": 0x4000000,
    "uart": 0x100,
    "finisher": 0x1000,
    "chipyard.gcd": 0x1000,
    "opentitan.gpio": 0x1000,
}
IRQ_MODELS = {"uart", "chipyard.gcd", "opentitan.gpio"}
WIDTH = {"ram": 64, "uart": 8, "clint": 64}


def name(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,47}", value)
        or value.lower().startswith("__usp_")
    ):
        raise ContractError("native spec: names require 1..48 ASCII letters/digits/underscores")
    return value


def catalog():
    return {m["role"]: m for m in json.loads(CATALOG.read_text())["models"]}


def load_spec(path):
    return validate_spec(read_yaml(path))


def validate_cpu(data):
    if not isinstance(data, dict):
        raise ContractError("native CPU must be a mapping")
    cpu = keys(
        data,
        {"name", "model", "timebase_hz"}
        | (set(data) & {"gem5_model", "harts", "clock_hz", "memory_latency_ns"}),
        "native CPU",
    )
    name(cpu["name"])
    for field, default, low, high in (
        ("harts", 1, 1, 8),
        ("clock_hz", 1000000000, 1000000, 10000000000),
        ("memory_latency_ns", 10, 1, 100000),
    ):
        cpu[field] = integer(cpu.get(field, default), "cpu." + field)
        if not low <= cpu[field] <= high:
            raise ContractError(f"unsupported native cpu.{field}: expected {low}..{high}")
    if not isinstance(cpu["model"], str) or cpu["model"] not in {"qemu.rv64", "qemu.max"}:
        raise ContractError("native generator currently binds QEMU rv64/max; no CPU substitution")
    if "gem5_model" in cpu and (
        not isinstance(cpu["gem5_model"], str)
        or cpu["gem5_model"] not in {"gem5.RiscvTimingSimpleCPU", "gem5.RiscvAtomicSimpleCPU"}
    ):
        raise ContractError("native gem5 requires an explicitly supported CPU model")
    cpu["timebase_hz"] = integer(cpu["timebase_hz"], "timebase_hz")
    if not 1000 <= cpu["timebase_hz"] <= 100000000:
        raise ContractError("unsupported native timebase")
    return cpu


def validate_spec(data):
    """Validate and normalize an owned mapping, independent of its input format."""
    if not isinstance(data, dict):
        raise ContractError("native SoC spec must be a mapping")
    keys(
        data,
        {"schema_version", "name", "cpu", "boot", "devices"}
        | ({"external_models"} if "external_models" in data else set()),
        "native SoC spec",
    )
    if data["schema_version"] != "1":
        raise ContractError("unsupported native SoC spec schema")
    name(data["name"])
    from .native_external import validate

    data["external_models"] = validate(
        data.get("external_models", {}), ROLE, [v["vlnv"] for v in catalog().values()]
    )
    irq_models = IRQ_MODELS | set(data["external_models"])
    cpu = validate_cpu(data["cpu"])
    boot = keys(data["boot"], {"ram", "rom", "console", "entry"}, "native boot")
    for key in ("ram", "rom", "console"):
        name(boot[key])
    boot["entry"] = integer(boot["entry"], "entry")
    if not isinstance(data["devices"], list) or not 5 <= len(data["devices"]) <= 63:
        raise ContractError("native spec requires 5..63 devices")
    names = {name(n).upper() for n in cpu_instances(data)}
    if len(data["devices"]) + cpu["harts"] > 64:
        raise ContractError("native spec exceeds 64 total instances")
    irqs = set()
    for dev in data["devices"]:
        if not isinstance(dev, dict) or not isinstance(dev.get("model"), str):
            raise ContractError("invalid native device")
        kind = dev["model"]
        if kind not in ROLE and kind not in data["external_models"]:
            raise ContractError(f"no qualified native model: {kind}")
        keys(
            dev,
            {"name", "model", "base", "size"} | ({"irq"} if kind in irq_models else set()),
            "native device",
        )
        label = name(dev["name"])
        if label.upper() in names:
            raise ContractError("duplicate native instance/header name")
        names.add(label.upper())
        for key in ("base", "size"):
            dev[key] = integer(dev[key], label + "." + key)
        base, size = dev["base"], dev["size"]
        if base % 4096 or not size or base + size > 2**48:
            raise ContractError(
                f"{label}: native windows require aligned 48-bit physical addresses"
            )
        if (kind in FIXED_SIZE and size != FIXED_SIZE[kind]) or (
            kind in data["external_models"] and size != 0x1000
        ):
            raise ContractError(f"{label}: incompatible {kind} aperture")
        if kind in {"ram", "rom"} and (
            size % 4096 or not 4096 <= size <= (512 * 1024**2 if kind == "ram" else 65536)
        ):
            raise ContractError(f"{label}: unsupported memory size")
        if kind in irq_models:
            dev["irq"] = integer(dev["irq"], label + ".irq")
            if not 1 <= dev["irq"] <= 31 or dev["irq"] in irqs:
                raise ContractError("PLIC IRQ must be unique in 1..31")
            irqs.add(dev["irq"])
    for kind in ("rom", "clint", "plic", "finisher"):
        if len(devices(data, kind)) != 1:
            raise ContractError(f"native v1 requires one {kind} model, with any instance name")
    if not devices(data, "uart") or sum(d["size"] for d in devices(data, "ram")) > 1024**3:
        raise ContractError("native v1 needs a console and at most 1 GiB total RAM")
    for field, kind in (("ram", "ram"), ("rom", "rom"), ("console", "uart")):
        if not any(d["name"] == boot[field] for d in devices(data, kind)):
            raise ContractError(f"boot.{field} does not reference a {kind} instance")
    ram = selected(data, "ram")
    if (
        ram["size"] < 4096 * cpu["harts"] + 4096
        or boot["entry"] % 4
        or not ram["base"] <= boot["entry"] < ram["base"] + ram["size"] - 4096 * cpu["harts"]
    ):
        raise ContractError("boot entry outside executable RAM or unaligned")
    ranges = sorted((d["base"], d["base"] + d["size"]) for d in data["devices"])
    if any(b[0] < a[1] for a, b in zip(ranges, ranges[1:], strict=False)):
        raise ContractError("overlapping native memory/IO windows")
    return data


def cpu_instances(spec):
    cpu = spec["cpu"]
    return [cpu["name"]] + [f"{cpu['name']}_hart{i}" for i in range(1, cpu.get("harts", 1))]


def devices(spec, model):
    return [d for d in spec["devices"] if d["model"] == model]


def selected(spec, field):
    return next(d for d in spec["devices"] if d["name"] == spec["boot"][field])


def emit_ipxact(spec, out):
    """Emit parameterized 2022 XML, then re-import with the existing XSD/IR frontend."""
    cat = catalog()
    directory = out / "ipxact"
    directory.mkdir()

    def element(parent, tag, text=None, **attrs):
        e = etree.SubElement(parent, PREFIX + tag, **attrs)
        if text is not None:
            e.text = str(text)
        return e

    def root(tag, vlnv):
        r = etree.Element(PREFIX + tag, nsmap={None: NS})
        for k, v in zip(("vendor", "library", "name", "version"), vlnv.split(":"), strict=True):
            element(r, k, v)
        return r

    def save(r, filename):
        etree.ElementTree(r).write(
            str(directory / filename), encoding="UTF-8", xml_declaration=True, pretty_print=True
        )

    kinds = {d["model"] for d in spec["devices"]}
    for kind in sorted(kinds | {"cpu"}):
        component = root(
            "component",
            cat["rv64"]["vlnv"] if kind == "cpu" else model_descriptor(spec, kind)["vlnv"],
        )
        if kind != "cpu":
            block = element(element(element(component, "memoryMaps"), "memoryMap"), "name", "map")
            memory_map = block.getparent()
            block = element(memory_map, "addressBlock")
            for key, value in (
                ("name", "region"),
                ("baseAddress", 0),
                ("range", "SIZE"),
                ("width", WIDTH.get(kind, 32)),
                ("usage", "memory" if kind in {"ram", "rom"} else "register"),
            ):
                element(block, key, value)
            params = element(component, "parameters")
            param = element(
                params,
                "parameter",
                parameterId="SIZE",
                type="longint",
                sign="unsigned",
                resolve="user",
            )
            element(param, "name", "SIZE")
            element(param, "value", FIXED_SIZE.get(kind, 4096))
        save(component, kind.replace(".", "_") + ".xml")
    design = root("design", f"socsim.native:generated:{spec['name']}:1.0")
    instances = element(design, "componentInstances")
    all_instances = [
        *({"name": n, "model": "cpu", "base": 0} for n in cpu_instances(spec)),
        *spec["devices"],
    ]
    for dev in all_instances:
        instance = element(instances, "componentInstance")
        element(instance, "instanceName", dev["name"])
        vlnv = (
            cat["rv64"]["vlnv"]
            if dev["model"] == "cpu"
            else model_descriptor(spec, dev["model"])["vlnv"]
        )
        attrs = dict(zip(("vendor", "library", "name", "version"), vlnv.split(":"), strict=True))
        ref = element(instance, "componentRef", **attrs)
        if dev["model"] != "cpu":
            values = element(ref, "configurableElementValues")
            element(values, "configurableElementValue", dev["size"], referenceId="SIZE")
    save(design, "design.xml")
    ir = elaborate(out, {d["name"]: d["base"] for d in all_instances}, directory / "design.xml")
    cpu = spec["cpu"]["name"]
    plic, clint = (devices(spec, k)[0]["name"] for k in ("plic", "clint"))
    routes = [
        InterruptRoute(d["name"], plic, d["irq"], "level-high")
        for d in spec["devices"]
        if d["model"] in IRQ_MODELS | set(spec["external_models"])
    ]
    for cpu in cpu_instances(spec):
        routes += [InterruptRoute(clint, cpu, n, "level-high") for n in (3, 7)]
        routes += [InterruptRoute(plic, cpu, n, "level-high") for n in (9, 11)]
    ir = replace(
        ir,
        cpu=CpuContract(
            "rv64imac_zicsr_zifencei",
            "MSU",
            "sv39",
            selected(spec, "rom")["base"],
            spec["boot"]["entry"],
            spec["cpu"]["timebase_hz"],
        ),
        interrupts=tuple(routes),
        address_spaces=tuple(
            AddressSpace(
                "physical" if h == 0 else f"physical_hart{h}",
                n,
                tuple(d["name"] for d in spec["devices"]),
            )
            for h, n in enumerate(cpu_instances(spec))
        ),
        sources=tuple(sorted((*ir.sources, ("spec.yaml", digest(out / "spec.yaml"))))),
    )
    write_json(out / "soc-ir.json", ir.to_dict())
    write_model_lock(spec, out)
    return ir


def write_model_lock(spec, out):
    """Model lock independent of the structural frontend."""
    bindings = [
        {"instance": n, "model": spec["cpu"]["model"], "source_commit": QEMU_SHA}
        for n in cpu_instances(spec)
    ]
    bindings += [
        {
            "instance": d["name"],
            "model": d["model"],
            "vlnv": model_descriptor(spec, d["model"])["vlnv"],
            "implementation": model_descriptor(spec, d["model"])["id"],
            "source": model_descriptor(spec, d["model"])["source"],
        }
        for d in spec["devices"]
    ]
    write_json(
        out / "models.lock.json",
        {
            "policy": "strict",
            "catalog_backend": "qemu",
            "bindings": bindings,
            "execution_bindings": "engine-bindings.json",
        },
    )


def model_descriptor(spec, model):
    if model in ROLE:
        return catalog()[ROLE[model]]
    from .native_external import descriptor

    return descriptor(spec["external_models"][model])


def model_instances(spec):
    return [
        d
        for d in spec["devices"]
        if d["model"] in {"chipyard.gcd", "opentitan.gpio"} | set(spec.get("external_models", {}))
    ]
