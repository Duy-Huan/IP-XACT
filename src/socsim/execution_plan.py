"""Immutable execution lowering of the canonical hardware IR and CPU selection.

This version describes the executable nine-role bridge contract. It does not
infer arbitrary IP behavior or pretend annotated and native time are correlated.
"""

import hashlib
import json
from dataclasses import asdict, dataclass

from .domain import ContractError
from .gem5 import GEM5_SHA
from .pipeline import QEMU_SHA, identity


@dataclass(frozen=True)
class ModelBinding:
    instance: str
    vlnv: str
    domain: str
    implementation: str | None
    source_commit: str | None


@dataclass(frozen=True)
class AddressRoute:
    initiator: str
    target: str
    base: int
    size: int
    owner: str
    transport: str


@dataclass(frozen=True)
class InterruptRoute:
    source: str
    target: str
    line: int
    trigger: str
    sampled_at_transaction_boundary: bool


@dataclass(frozen=True)
class TimeDomain:
    name: str
    kind: str
    clock_hz: int | None
    progression: str
    calibrated_to_silicon: bool = False


@dataclass(frozen=True)
class ExecutionPlan:
    schema_version: str
    status: str
    soc_ir_sha256: str
    configuration_sha256: str
    cpu_provider: str
    cpu_model: str
    engine: str
    bindings: tuple[ModelBinding, ...]
    address_routes: tuple[AddressRoute, ...]
    interrupt_routes: tuple[InterruptRoute, ...]
    time_domains: tuple[TimeDomain, ...]
    synchronization: str = "blocking-transaction-boundary-v1"
    dma_supported: bool = False
    coherence_supported: bool = False
    checkpoint_supported: bool = False
    cpu_rtl_cycle_correlation: bool = False

    def to_dict(self):
        return json.loads(json.dumps(asdict(self)))

    def require_executable(self):
        if self.status != "AVAILABLE_ADAPTER":
            raise ContractError("execution plan has an unresolved CPU adapter")


def compile_execution_plan(project, config, selected, peripheral_bindings):
    engine = config["engine"]
    bindings = []
    for instance in project.ir.instances:
        if instance.name == "rv64":
            implementation = selected.get("gem5_cpu", selected.get("qemu_cpu"))
            commit = (GEM5_SHA if engine == "gem5" else QEMU_SHA) if implementation else None
            bindings.append(
                ModelBinding(instance.name, instance.vlnv, engine, implementation, commit)
            )
        else:
            b = next(b for b in peripheral_bindings if b["instance"] == instance.name)
            bindings.append(
                ModelBinding(
                    instance.name,
                    instance.vlnv,
                    b["owner"],
                    b["implementation"],
                    b["source_commit"],
                )
            )
    routes = tuple(
        AddressRoute(
            "rv64",
            i.name,
            w.base,
            w.size,
            "systemc-verilator" if i.name in {"cygcd", "otgpio"} else engine,
            "usp-tlm-mmio-v1"
            if i.name in {"cygcd", "otgpio"}
            else "native-memory"
            if i.name in {"ram", "rom"}
            else "native-mmio",
        )
        for i in project.ir.instances
        for w in i.windows
    )
    irqs = tuple(
        InterruptRoute(i.source, i.target, i.line, i.trigger, i.source in {"cygcd", "otgpio"})
        for i in project.ir.interrupts
    )
    domains = (
        TimeDomain(
            engine,
            "native-model" if engine == "gem5" else "functional-tcg",
            selected["clock_hz"] if engine == "gem5" else None,
            "gem5-event-queue" if engine == "gem5" else "qemu-functional-execution",
        ),
        TimeDomain(
            "systemc-verilator",
            "annotated-rtl-cycles",
            100000000,
            "explicit-rtl-cycles-on-mmio; 10ns annotation per cycle",
        ),
    )
    plan = ExecutionPlan(
        "1",
        selected["status"],
        identity(project),
        hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        selected["provider"],
        selected["model"],
        engine,
        tuple(bindings),
        routes,
        irqs,
        domains,
    )
    owners = {r.target: r.owner for r in routes}
    if owners.get("ram") != engine or owners.get("rom") != engine:
        raise ContractError("CPU engine must exclusively own ROM/RAM in this execution contract")
    if len({b.instance for b in bindings}) != len(project.ir.instances):
        raise ContractError("execution ownership/binding is ambiguous")
    return plan


def validate_bridge_trace(rows):
    """Check the actual shared IPC/TLM contract, without CPU cycle inference."""
    previous_time, previous_cycles = -1, -1
    ordered = valid_time = valid_route = valid_levels = bool(rows)
    for index, row in enumerate(rows, 1):
        ordered &= row["seq"] == index
        valid_time &= (
            row["time_ps"] >= 0
            and row["cycles"] >= 0
            and row["time_ps"] >= previous_time
            and row["cycles"] >= previous_cycles
        )
        previous_time, previous_cycles = row["time_ps"], row["cycles"]
        offset, op = row["offset"], row["op"]
        valid_route &= (op == 2 and offset == 0) or (
            op in {0, 1} and offset % 4 == 0 and (0 <= offset <= 12 or 0x1000 <= offset < 0x1040)
        )
        valid_levels &= 0 <= row["irq"] <= 3 and row["status"] == 0
    return {
        "bridge_order": bool(ordered),
        "bridge_annotated_time": bool(valid_time),
        "bridge_routes": bool(valid_route),
        "bridge_irq_levels": bool(valid_levels),
    }
