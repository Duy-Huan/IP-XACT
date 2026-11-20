"""Explicit M5 CPU/bus, M6 NoC/time and M7 ROI/calibration contracts (v0.4.0)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .domain import ContractError
from .native_ir import NativeIR
from .validation import require_bounded_int, require_finite_number, require_identifier

VALID_FIDELITY_SOURCES = frozenset({"native-gem5", "native-rtl", "qemu-analytical-estimate"})


@dataclass(frozen=True)
class CpuBusAdapterEntry:
    provider: str
    model: str
    protocol: str
    isa: str
    mmu: str
    status: str
    supported_engines: tuple[str, ...]
    notes: str


CPU_ADAPTER_MATRIX: tuple[CpuBusAdapterEntry, ...] = (
    CpuBusAdapterEntry(
        "qemu",
        "qemu.rv64",
        "flat-functional-v1",
        "rv64imac_zicsr_zifencei",
        "sv39",
        "implemented",
        ("qemu",),
        "Functional ISS engine; analytical event costs only.",
    ),
    CpuBusAdapterEntry(
        "gem5",
        "gem5.RiscvTimingSimpleCPU",
        "flat-functional-v1",
        "rv64imac_zicsr_zifencei",
        "sv39",
        "implemented",
        ("gem5",),
        "Architectural timing ISS with native instruction/cycle counters.",
    ),
    CpuBusAdapterEntry(
        "chipyard",
        "chipyard.ibex",
        "obi-rvfi32-v1",
        "rv32imc",
        "none",
        "implemented",
        ("qemu", "gem5"),
        "Qualified RV32 M-mode RTL master on backing memory engines.",
    ),
    CpuBusAdapterEntry(
        "custom",
        "custom.obi_rvfi32",
        "obi-rvfi32-v1",
        "rv32imc",
        "none",
        "implemented",
        ("qemu", "gem5"),
        "Qualified custom RV32 M-mode RTL master.",
    ),
    CpuBusAdapterEntry(
        "chipyard",
        "chipyard.rocket_axi32",
        "axi4-32-pa32",
        "rv64imac",
        "sv39",
        "implemented",
        ("qemu", "gem5"),
        "Qualified single-tile Rocket RTL master; native IR v6 M-mode + separate Linux fixture.",
    ),
    CpuBusAdapterEntry(
        "chipyard",
        "chipyard.boom",
        "axi4-64",
        "rv64gc",
        "sv39",
        "planned",
        (),
        "Requires BOOM Chisel elaboration and out-of-order AXI64 adapter.",
    ),
    CpuBusAdapterEntry(
        "opentitan",
        "opentitan.earlgrey_full",
        "tilelink-ul-32",
        "rv32imcb",
        "none",
        "planned",
        (),
        "Requires checked-in full-top builder and unified engine ownership contract.",
    ),
)


def check_cpu_bus_adapter(provider: str, model: str, protocol: str, engine: str) -> dict[str, Any]:
    """Resolve an exact CPU/bus adapter entry; never silently fall back to ISS."""
    for entry in CPU_ADAPTER_MATRIX:
        if entry.provider == provider and entry.model == model and entry.protocol == protocol:
            if entry.status != "implemented":
                raise ContractError(
                    f"E_CPU_ADAPTER {provider}/{model} ({protocol}) is {entry.status}; "
                    "ISS substitution is forbidden"
                )
            if engine not in entry.supported_engines:
                raise ContractError(
                    f"E_CPU_ADAPTER {model} does not support execution engine {engine}"
                )
            return {
                "status": "QUALIFIED_ADAPTER",
                "provider": entry.provider,
                "model": entry.model,
                "protocol": entry.protocol,
                "isa": entry.isa,
                "mmu": entry.mmu,
                "engine": engine,
            }
    raise ContractError(
        f"E_CPU_ADAPTER unknown CPU/bus combination {provider}/{model}/{protocol}; "
        "explicit adapter required"
    )


def derive_noc_topology(
    ir: NativeIR,
    *,
    rows: int,
    cols: int,
    pio_bypass_disclosed: bool,
    coherence_protocol: str = "ruby-garnet-simple",
    synchronization: str = "lockstep-annotated",
) -> dict[str, Any]:
    """Derive an IR-bound mesh topology and enforce explicit PIO-bypass disclosure (M6)."""
    require_bounded_int(rows, 1, 16, "mesh rows")
    require_bounded_int(cols, 1, 16, "mesh cols")
    if not pio_bypass_disclosed:
        raise ContractError("E_NOC_BYPASS any PIO bypass of the live NoC must be disclosed")
    if coherence_protocol not in {"ruby-garnet-simple", "mesi-two-level", "none-functional"}:
        raise ContractError(f"E_NOC_COHERENCE unsupported coherence protocol: {coherence_protocol}")
    if synchronization not in {"lockstep-annotated", "quantum-bounded", "cycle-accurate-rtl"}:
        raise ContractError(f"E_NOC_SYNC unsupported synchronization contract: {synchronization}")

    endpoints = [c.instance.name for c in ir.cpus] + [r.instance.name for r in ir.regions]
    router_count = rows * cols
    placement = [
        {
            "instance": inst,
            "router_id": idx % router_count,
            "role": "initiator" if idx < len(ir.cpus) else "target",
        }
        for idx, inst in enumerate(endpoints)
    ]
    return {
        "contract": "native-noc-topology-1",
        "design": ir.name,
        "topology": f"mesh-{rows}x{cols}",
        "routers": router_count,
        "endpoint_count": len(endpoints),
        "placements": placement,
        "coherence_protocol": coherence_protocol,
        "synchronization": synchronization,
        "pio_bypass_disclosed": pio_bypass_disclosed,
        "synthesizable_rtl_counterpart": False,
    }


def evaluate_calibration_report(
    *,
    roi_name: str,
    reference_source: str,
    model_source: str,
    reference_cycles: float,
    estimated_cycles: float,
    max_relative_error: float = 0.15,
) -> dict[str, Any]:
    """Evaluate ROI model calibration error and uncertainty bounds (M7)."""
    require_identifier(roi_name, "roi_name")
    if reference_source not in {"native-gem5", "native-rtl"}:
        raise ContractError(
            "E_PERF_CALIBRATION reference must be native-gem5 or native-rtl, never wall time"
        )
    if model_source not in VALID_FIDELITY_SOURCES:
        raise ContractError(f"E_PERF_CALIBRATION unknown model source: {model_source}")
    ref = require_finite_number(reference_cycles, "reference_cycles", low=1.0)
    est = require_finite_number(estimated_cycles, "estimated_cycles", low=1.0)
    budget = require_finite_number(max_relative_error, "max_relative_error", low=0.0, high=1.0)
    rel_error = abs(est - ref) / ref
    return {
        "contract": "native-calibration-report-1",
        "roi": roi_name,
        "reference_source": reference_source,
        "model_source": model_source,
        "reference_cycles": ref,
        "estimated_cycles": est,
        "relative_error": round(rel_error, 6),
        "error_budget": budget,
        "within_budget": rel_error <= budget,
        "silicon_signoff": False,
    }
