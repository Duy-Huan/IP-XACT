"""Supported custom-machine hardware contract, separate from simulator internals."""

import json
from dataclasses import replace
from pathlib import Path

from jsonschema import ValidationError, validate

from .domain import AddressSpace, ContractError, CpuContract, InterruptRoute
from .ipxact import digest, integer
from .project import inside, keys, read_yaml

ROLES = {"rv64", "ram", "rom", "uart", "finisher", "clint", "plic"}
PROFILE_EXTRAS = {
    "usp-rv64-v1": set(),
    "usp-rv64-hybrid-v1": {"scfifo"},
    "usp-rv64-ips-v1": {"cygcd", "otgpio"},
}


def attach_contract(project):
    path = inside(project.root, project.manifest["hardware"])
    data = keys(
        read_yaml(path), {"schema_version", "cpu", "interrupts", "address_space"}, "hardware"
    )
    schema = json.loads((Path(__file__).parent / "schemas/hardware-v1.json").read_text())
    try:
        validate(data, schema)
    except ValidationError as exc:
        raise ContractError(f"E_HARDWARE_SCHEMA {list(exc.absolute_path)}: {exc.message}") from exc
    if data["schema_version"] != "1":
        raise ContractError("unsupported hardware schema")
    c = keys(
        data["cpu"], {"isa", "privilege", "mmu", "reset_vector", "entry", "timebase_hz"}, "cpu"
    )
    if (c["isa"], c["privilege"], c["mmu"]) != ("rv64imac_zicsr_zifencei", "MSU", "sv39"):
        raise ContractError("unsupported CPU ISA/privilege/MMU contract")
    cpu = CpuContract(
        c["isa"],
        c["privilege"],
        c["mmu"],
        integer(c["reset_vector"], "reset"),
        integer(c["entry"], "entry"),
        integer(c["timebase_hz"], "timebase"),
    )
    if cpu.timebase_hz != 10000000 or cpu.reset_vector % 4 or cpu.entry % 4:
        raise ContractError("invalid timebase/reset/entry alignment")
    profile = project.manifest["profile"]
    if profile not in PROFILE_EXTRAS:
        raise ContractError("unsupported custom hardware profile")
    roles = ROLES | PROFILE_EXTRAS[profile]
    instances = {i.name: i for i in project.ir.instances}
    if set(instances) != roles:
        raise ContractError(
            f"{profile} requires exactly the declared profile roles: {sorted(roles)}"
        )
    if instances["rv64"].windows:
        raise ContractError("CPU cannot expose MMIO")
    ranges = {
        "ram": (0x8000000, 64),
        "rom": (0x1000, 32),
        "uart": (0x100, 8),
        "clint": (0x10000, 64),
        "plic": (0x4000000, 32),
        "finisher": (0x1000, 32),
    }
    if "scfifo" in roles:
        ranges["scfifo"] = (0x1000, 32)
    for name in ("cygcd", "otgpio"):
        if name in roles:
            ranges[name] = (0x1000, 32)
    for name, (size, width) in ranges.items():
        windows = instances[name].windows
        if len(windows) != 1 or (windows[0].size, windows[0].width) != (size, width):
            raise ContractError(f"{name}: incompatible model register/window contract")
        if windows[0].base % (0x100 if name == "uart" else 0x1000):
            raise ContractError(f"{name}: invalid base alignment")
        if windows[0].base + size > 2**32:
            raise ContractError("custom v1 device addresses must fit 32-bit DTS cells")
    if cpu.reset_vector != instances["rom"].windows[0].base:
        raise ContractError("CPU reset vector must equal ROM base")
    if cpu.entry != instances["ram"].windows[0].base:
        raise ContractError("boot entry must equal RAM base")
    if not isinstance(data["interrupts"], list):
        raise ContractError("interrupts must be a list")
    routes = []
    for r in data["interrupts"]:
        keys(r, {"source", "target", "line", "trigger"}, "interrupt")
        if r["source"] not in roles or r["target"] not in roles or r["trigger"] != "level-high":
            raise ContractError("invalid interrupt endpoint/trigger")
        routes.append(
            InterruptRoute(r["source"], r["target"], integer(r["line"], "irq"), r["trigger"])
        )
    fixed = {("clint", "rv64", 3), ("clint", "rv64", 7), ("plic", "rv64", 9), ("plic", "rv64", 11)}
    uart = [r for r in routes if (r.source, r.target) == ("uart", "plic")]
    actual = {(r.source, r.target, r.line) for r in routes}
    if (
        len(routes) != 5 + len(PROFILE_EXTRAS[profile])
        or not fixed.issubset(actual)
        or len(uart) != 1
        or not 1 <= uart[0].line < 32
    ):
        raise ContractError("invalid CLINT/PLIC/UART interrupt routing")
    peripheral_lines = {uart[0].line}
    for peripheral in sorted(PROFILE_EXTRAS[profile]):
        device_routes = [r for r in routes if (r.source, r.target) == (peripheral, "plic")]
        if (
            len(device_routes) != 1
            or not 1 <= device_routes[0].line < 32
            or device_routes[0].line in peripheral_lines
        ):
            raise ContractError(f"invalid {peripheral} IRQ routing")
        peripheral_lines.add(device_routes[0].line)
    space = keys(data["address_space"], {"name", "initiator", "targets"}, "address_space")
    if space["initiator"] != "rv64" or not isinstance(space["name"], str):
        raise ContractError("invalid address-space initiator")
    if not isinstance(space["targets"], list) or sorted(space["targets"]) != sorted(
        roles - {"rv64"}
    ):
        raise ContractError("CPU address space must expose exactly all supported targets")
    return replace(
        project,
        ir=replace(
            project.ir,
            schema_version="2",
            cpu=cpu,
            interrupts=tuple(sorted(routes, key=lambda r: (r.source, r.target, r.line))),
            address_spaces=(AddressSpace(space["name"], "rv64", tuple(sorted(space["targets"]))),),
            sources=tuple(
                sorted(
                    (*project.ir.sources, (path.relative_to(project.root).as_posix(), digest(path)))
                )
            ),
        ),
    )
