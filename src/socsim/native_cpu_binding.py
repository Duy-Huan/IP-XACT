"""Explicit native RTL CPU identity and physical-memory-engine contracts."""

from dataclasses import dataclass

from .domain import ContractError
from .rocket_build import CPU_CONTRACT, PINS, address_map_contract, ram_contract

ROCKET = "chipyard." + str(CPU_CONTRACT["model"])
ROCKET_VLNV = "socsim.pinned:chipyard:rocket64-imac-axi32:1.0"


@dataclass(frozen=True)
class NativeCpuBinding:
    provider: str
    model: str
    source_pins: tuple[tuple[str, str], ...]
    protocol: str
    instruction_owner: str
    memory_contexts: tuple[tuple[str, str], ...]
    external_dma_coherence: bool
    timing_correlated: bool


def is_rtl(cpu):
    return cpu.get("model") == ROCKET


def binding(cpu):
    if not is_rtl(cpu):
        return None
    return NativeCpuBinding(
        "chipyard",
        str(CPU_CONTRACT["model"]),
        tuple(sorted(PINS.items())),
        "axi4-le64-pa32-to-systemc-mmio32-v1",
        "actual-RocketTile-RTL",
        (
            ("qemu", "qtest-rv64-halted-irq-context"),
            ("gem5", "RiscvAtomicSimpleCPU-halted-irq-context"),
        ),
        False,
        False,
    )


def validate_cpu(cpu):
    if not is_rtl(cpu):
        return
    if cpu["harts"] != 1 or "gem5_model" in cpu:
        raise ContractError("native Rocket is one actual RTL hart; gem5 is a memory engine")
    hz = cpu["clock_hz"]
    if not 1000000 <= hz <= 500000000 or 1000000000 % hz:
        raise ContractError("native Rocket clock must have an integral 2..1000ns period")


def layout(spec):
    if not is_rtl(spec["cpu"]):
        raise ContractError("native RTL binding requires the explicit source CPU model")
    memories = [d for d in spec["devices"] if d["model"] == "ram"]
    if len(memories) != 1:
        raise ContractError("this native Rocket PMA profile requires one RAM region")
    d = memories[0]
    ram = ram_contract(d["base"], d["size"])
    regions = [
        {"base": d["base"], "size": d["size"], "kind": "rom" if d["model"] == "rom" else "mmio"}
        for d in spec["devices"]
        if d["model"] != "ram"
    ]
    return ram, address_map_contract(ram, regions)


def verify_cpu(spec, out):
    from .rocket_build import verify_build

    record = verify_build(out)
    ram, regions = layout(spec)
    if record["ram"] != ram or record["regions"] != regions:
        raise ContractError("native IR and actual Rocket PMA/AXI build differ")
    return record


def build_cpu(spec, out, upstream, tools, java_home, scala_cache, jobs=2):
    from .rocket_build import elaborate

    ram, regions = layout(spec)
    return elaborate(
        out, upstream, tools, java_home, scala_cache, ram["base"], ram["size"], regions, jobs
    )
