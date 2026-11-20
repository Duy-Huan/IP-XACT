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


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    from dataclasses import asdict
    from pathlib import Path

    from .ip_catalog import IP_CATALOG, catalog_summary
    from .native_ir import read_ir

    parser = argparse.ArgumentParser(prog="usp contracts")
    commands = parser.add_subparsers(dest="command", required=True)
    adapters_cmd = commands.add_parser("adapters")
    adapters_cmd.add_argument("--provider")
    adapters_cmd.add_argument("--model")
    adapters_cmd.add_argument("--protocol")
    adapters_cmd.add_argument("--engine")

    noc_cmd = commands.add_parser("noc")
    noc_cmd.add_argument("ir", type=Path)
    noc_cmd.add_argument("--rows", type=int, required=True)
    noc_cmd.add_argument("--cols", type=int, required=True)
    noc_cmd.add_argument("--pio-bypass-disclosed", action="store_true")
    noc_cmd.add_argument("--coherence", default="ruby-garnet-simple")
    noc_cmd.add_argument("--sync", default="lockstep-annotated")

    cal_cmd = commands.add_parser("calibrate")
    cal_cmd.add_argument("--roi", required=True)
    cal_cmd.add_argument("--ref-source", required=True)
    cal_cmd.add_argument("--model-source", required=True)
    cal_cmd.add_argument("--ref-cycles", type=float, required=True)
    cal_cmd.add_argument("--est-cycles", type=float, required=True)
    cal_cmd.add_argument("--budget", type=float, default=0.15)

    commands.add_parser("ip-catalog")
    args = parser.parse_args(argv)
    try:
        result: dict[str, Any]
        if args.command == "adapters":
            if any(v is not None for v in (args.provider, args.model, args.protocol, args.engine)):
                if not all(
                    v is not None for v in (args.provider, args.model, args.protocol, args.engine)
                ):
                    raise ContractError(
                        "E_CPU_ADAPTER specify --provider, --model, --protocol and --engine"
                    )
                result = check_cpu_bus_adapter(
                    args.provider, args.model, args.protocol, args.engine
                )
            else:
                result = {"adapters": [asdict(entry) for entry in CPU_ADAPTER_MATRIX]}
        elif args.command == "noc":
            result = derive_noc_topology(
                read_ir(args.ir),
                rows=args.rows,
                cols=args.cols,
                pio_bypass_disclosed=args.pio_bypass_disclosed,
                coherence_protocol=args.coherence,
                synchronization=args.sync,
            )
        elif args.command == "calibrate":
            result = evaluate_calibration_report(
                roi_name=args.roi,
                reference_source=args.ref_source,
                model_source=args.model_source,
                reference_cycles=args.ref_cycles,
                estimated_cycles=args.est_cycles,
                max_relative_error=args.budget,
            )
        else:
            result = {
                "summary": catalog_summary(),
                "entries": [asdict(entry) for entry in IP_CATALOG],
            }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}))
        return 2
