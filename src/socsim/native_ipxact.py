"""Direct structural IP-XACT intake with explicit, versioned runtime bindings."""

from dataclasses import replace

from .domain import ContractError
from .ipxact import digest, elaborate
from .native_ir import compile_design
from .project import inside, keys, read_yaml
from .soc_spec import validate_spec


def import_design(overlay_path):
    overlay_path = overlay_path.resolve()
    root = overlay_path.parent
    data = read_yaml(overlay_path)
    keys(
        data,
        {"schema_version", "design", "name", "cpu", "boot", "placements", "bindings"}
        | (set(data) & {"instance_aliases", "external_models"}),
        "native IP-XACT overlay",
    )
    if data["schema_version"] != "native-ipxact-1":
        raise ContractError("E_NATIVE_IPXACT unsupported overlay schema")
    design = inside(root, data["design"])
    if not design.is_relative_to(root / "ipxact") or design.suffix != ".xml":
        raise ContractError("E_NATIVE_IPXACT design must be under ipxact/")
    if not isinstance(data["placements"], dict) or not isinstance(data["bindings"], list):
        raise ContractError("E_NATIVE_IPXACT explicit placements and ordered bindings required")
    structural = elaborate(root, data["placements"], design, data.get("instance_aliases"))
    by_name = {i.name: i for i in structural.instances}
    seen = set()
    devices = []
    cpu_names = []
    for b in data["bindings"]:
        keys(
            b,
            {"instance", "model"} | ({"irq"} if isinstance(b, dict) and "irq" in b else set()),
            "native instance binding",
        )
        n = b["instance"]
        if not isinstance(n, str) or n in seen or n not in by_name:
            raise ContractError("E_NATIVE_BINDING duplicate/unknown instance")
        seen.add(n)
        i = by_name[n]
        if b["model"] == "cpu":
            if "irq" in b:
                raise ContractError("E_NATIVE_CPU CPU cannot bind a peripheral IRQ")
            cpu_names.append(n)
            continue
        if len(i.windows) != 1:
            raise ContractError("E_NATIVE_WINDOW native adapter requires one window per device")
        w = i.windows[0]
        d = {"name": n, "model": b["model"], "base": w.base, "size": w.size}
        if "irq" in b:
            d["irq"] = b["irq"]
        devices.append(d)
    if seen != set(by_name):
        raise ContractError("E_NATIVE_BINDING every elaborated instance requires one binding")
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
    from .soc_spec import cpu_instances

    if cpu_names != cpu_instances(spec):
        raise ContractError("E_NATIVE_CPU declared hart order/names differ from CPU bindings")
    sources = (*structural.sources, (overlay_path.name, digest(overlay_path)))
    if overlay_path.name in {n for n, _ in structural.sources}:
        raise ContractError("E_NATIVE_IPXACT overlay collides with XML source identity")
    structural = replace(structural, sources=tuple(sorted(sources)))
    # Validate exact VLNV, register/width/CPU contracts before creating any output.
    canonical = compile_design(spec, structural)
    return spec, canonical, root, data["design"], data.get("instance_aliases", {})
