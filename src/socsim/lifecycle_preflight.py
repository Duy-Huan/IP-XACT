"""Design-agnostic lifecycle capability & ownership preflight checks (M3)."""

from __future__ import annotations

from typing import Any

from .domain import ContractError
from .native_cpu_binding import ROCKET
from .native_domains import check_lowering_capability, derive_domains_from_ir
from .native_ir import NativeIR

SUPPORTED_ENGINES = frozenset({"qemu", "gem5", "both"})
ISS_QEMU_MODELS = frozenset({"qemu.rv64", "qemu.max"})
ISS_GEM5_MODELS = frozenset({"gem5.RiscvTimingSimpleCPU", "gem5.RiscvAtomicSimpleCPU"})
WORKLOAD_KINDS = frozenset(
    {"baremetal", "linux", "freertos", "zephyr", "ucos2", "ucos3", "package-driver"}
)


def preflight_design(
    ir: NativeIR,
    *,
    engine: str,
    workloads: list[str],
    max_harts: int = 8,
) -> dict[str, Any]:
    """Verify CPU/RAM/time ownership, adapter compatibility and workload eligibility.

    Never allows an ISS model to silently substitute for a source-bound RTL CPU.
    """
    if engine not in SUPPORTED_ENGINES:
        raise ContractError(f"E_PREFLIGHT unsupported execution engine: {engine}")
    if not workloads or any(w not in WORKLOAD_KINDS for w in workloads):
        raise ContractError("E_PREFLIGHT invalid or empty workload list")

    harts = len(ir.cpus)
    if not 1 <= harts <= max_harts:
        raise ContractError(f"E_PREFLIGHT hart count {harts} exceeds bound 1..{max_harts}")

    domains = derive_domains_from_ir(ir)
    cpu0 = ir.cpus[0]
    is_rtl_cpu = ir.cpu_binding is not None
    issues: list[str] = []

    if is_rtl_cpu:
        assert ir.cpu_binding is not None
        bound_model = f"{ir.cpu_binding.provider}.{ir.cpu_binding.model}"
        if cpu0.qemu_model != ROCKET or bound_model != ROCKET:
            issues.append(f"source CPU model {cpu0.qemu_model} lacks a qualified RTL adapter")
        if harts != 1:
            issues.append("qualified native Rocket RTL CPU currently supports exactly 1 hart")
        if "linux" in workloads:
            issues.append("native RTL Linux requires a dedicated generated RTL-Linux BSP")
        cpu_owner = ir.cpu_binding.instruction_owner
        time_owner = "rtl-clock-and-backing-engine"
        ram_owner = "backing-engine-memory-context"
    else:
        if cpu0.qemu_model not in ISS_QEMU_MODELS:
            issues.append(f"QEMU CPU model {cpu0.qemu_model} is not qualified")
        if engine in {"gem5", "both"} and cpu0.gem5_model not in ISS_GEM5_MODELS:
            issues.append(f"gem5 CPU model {cpu0.gem5_model} is not qualified")
        cpu_owner = "selected-iss-engine"
        time_owner = "engine-native-gem5-or-qemu-events"
        ram_owner = "selected-iss-engine"

    if harts > 1 and any(w in {"freertos", "zephyr", "ucos2", "ucos3"} for w in workloads):
        issues.append("native RV64 RTOS/Zephyr BSPs currently qualify single-hart topologies only")

    ram_regions = [r for r in ir.regions if r.model == "ram"]
    if not ram_regions:
        issues.append("design declares no executable RAM region")
    elif is_rtl_cpu and len(ram_regions) != 1:
        issues.append("qualified native Rocket PA32 profile requires exactly one RAM region")

    lowering = check_lowering_capability(
        _flat_views_from_ir(ir),
        domains,
        backend="native-flat",
    )
    if lowering["status"] != "SUPPORTED":
        issues.extend(lowering["blocking_reasons"])

    return {
        "status": "READY" if not issues else "BLOCKED",
        "design": ir.name,
        "schema_version": ir.schema_version,
        "engine": engine,
        "harts": harts,
        "is_rtl_cpu": is_rtl_cpu,
        "ownership": {
            "cpu_instructions": cpu_owner,
            "physical_ram": ram_owner,
            "time_domain": time_owner,
            "clocks": [c.name for c in domains.clocks],
            "resets": [r.name for r in domains.resets],
        },
        "workloads": list(workloads),
        "blocking_issues": issues,
    }


def _flat_views_from_ir(ir: NativeIR):
    from .native_domains import AddressViewGraph, InitiatorView, TranslationAperture

    views = []
    for cpu in ir.cpus:
        apertures = tuple(
            TranslationAperture(
                name=r.instance.name,
                local_base=r.instance.windows[0].base,
                physical_base=r.instance.windows[0].base,
                size=r.instance.windows[0].size,
                target=r.instance.name,
                protocol="flat-functional-v1",
            )
            for r in ir.regions
        )
        views.append(InitiatorView(cpu.instance.name, 48, apertures))
    return AddressViewGraph(tuple(views))
