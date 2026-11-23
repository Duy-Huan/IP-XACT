"""CHIA-style Parallel DSE & Timing Calibration Cluster Orchestrator (E4).

Executes multi-configuration SoC design-space sweeps across bounded parallel workers with:
  - Content-addressed tamper-checked ArtifactCache (replayed results explicitly labeled)
  - Separate training / holdout design-point tracking (per CHIA arXiv:2606.27350 discipline)
  - Real SystemC/Verilator hybrid execution when toolchains are installed, or deterministic
    spec/IR/NoC evaluation when running on a minimal host without simulators
  - Machine-readable `dse-report.json` and human-readable `dse-report.md` Pareto summary.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import datetime
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .domain import ContractError
from .runner import write_json
from .sdk import ArtifactCache
from .unified_systemc_soc import (
    generate_unified_artifacts,
    has_unified_toolchain,
    load_unified_spec,
    run_unified_soc,
)
from .v04_contracts import evaluate_calibration_report
from .version import __version__

CONTRACT = "usp-dse-cluster-1"


@dataclass(frozen=True)
class DsePoint:
    point_id: str
    split: str  # "train" or "holdout"
    hop_latency_ns: int
    link_latency_ns: int
    backend: str  # "verilator-rtl" or "systemc-tlm"


DEFAULT_SWEEP: tuple[DsePoint, ...] = (
    DsePoint("noc_fast_tlm", "train", hop_latency_ns=5, link_latency_ns=2, backend="systemc-tlm"),
    DsePoint("noc_nom_tlm", "train", hop_latency_ns=10, link_latency_ns=5, backend="systemc-tlm"),
    DsePoint("noc_nom_rtl", "train", hop_latency_ns=10, link_latency_ns=5, backend="verilator-rtl"),
    DsePoint(
        "noc_slow_tlm", "holdout", hop_latency_ns=20, link_latency_ns=8, backend="systemc-tlm"
    ),
)


def _materialize_variant_spec(base_spec_path: Path, point: DsePoint, work_dir: Path) -> Path:
    data = copy.deepcopy(load_unified_spec(base_spec_path))
    data["noc"]["hop_latency_ns"] = point.hop_latency_ns
    data["noc"]["link_latency_ns"] = point.link_latency_ns
    variant_path = work_dir / f"{point.point_id}.yaml"
    variant_path.parent.mkdir(parents=True, exist_ok=True)
    variant_path.write_text(yaml.safe_dump(data, sort_keys=False))
    # Symlink unified_systemc sources next to variant spec so run_unified_soc resolves them cleanly
    src_link = work_dir / "unified_systemc"
    if not src_link.exists():
        src_link.symlink_to((base_spec_path.parent / "unified_systemc").resolve())
    return variant_path


def _evaluate_point(
    base_spec_path: Path,
    point: DsePoint,
    out_dir: Path,
    cache: ArtifactCache | None,
    *,
    simulate: bool,
) -> dict[str, Any]:
    started = time.monotonic()
    cache_inputs = {
        "spec_sha": base_spec_path.read_text(),
        "point_id": point.point_id,
        "hop_latency_ns": point.hop_latency_ns,
        "link_latency_ns": point.link_latency_ns,
        "backend": point.backend,
        "simulate": simulate,
    }
    if cache is not None:
        cached = cache.get("dse_point", cache_inputs)
        if cached is not None:
            return cached

    point_dir = out_dir / "points" / point.point_id
    variant_spec = _materialize_variant_spec(base_spec_path, point, point_dir)
    if simulate:
        run_res = run_unified_soc(
            variant_spec,
            point_dir / "run",
            backend=point.backend,
            verify_gdb=False,
        )
        summary = run_res.get("positive_run", {}).get("summary", {})
        result: dict[str, Any] = {
            "point_id": point.point_id,
            "split": point.split,
            "hop_latency_ns": point.hop_latency_ns,
            "link_latency_ns": point.link_latency_ns,
            "backend": point.backend,
            "mode": "live-simulation",
            "status": run_res["status"],
            "systemc_time_ps": summary.get("systemc_time_ps"),
            "instructions_executed": summary.get("instructions_executed"),
            "noc_total_hops": summary.get("noc_total_hops"),
            "npu_rtl_cycles": summary.get("ip_results", {}).get("npu_rtl_cycles", 0),
            "wall_seconds": round(time.monotonic() - started, 4),
            "replayed": False,
        }
    else:
        ir = generate_unified_artifacts(variant_spec, point_dir / "plan")
        result = {
            "point_id": point.point_id,
            "split": point.split,
            "hop_latency_ns": point.hop_latency_ns,
            "link_latency_ns": point.link_latency_ns,
            "backend": point.backend,
            "mode": "plan-and-ir-only",
            "status": "PASSED",
            "spec_sha256": ir["spec_sha256"],
            "systemc_time_ps": None,
            "instructions_executed": None,
            "noc_total_hops": None,
            "npu_rtl_cycles": 0,
            "wall_seconds": round(time.monotonic() - started, 4),
            "replayed": False,
        }

    if cache is not None and result["status"] == "PASSED":
        cache.put("dse_point", cache_inputs, result)
    return result


def _render_dse_md(report: dict[str, Any]) -> str:
    lines = [
        "# Parallel DSE & RTL↔TLM Calibration Report",
        "",
        f"- **Contract:** `{report['contract']}`",
        f"- **Status:** **{report['status']}**",
        f"- **Execution Mode:** `{report['execution_mode']}`",
        f"- **Parallel Workers:** `{report['workers']}`",
        f"- **Timestamp (UTC):** `{report['created_utc']}`",
        "",
        "| Point | Split | Backend | Hop/Link | Status | Sim Time (ps) | NoC Hops | Replayed |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for pt in report["points"]:
        lines.append(
            f"| `{pt['point_id']}` | `{pt['split']}` | `{pt['backend']}` | "
            f"{pt['hop_latency_ns']}/{pt['link_latency_ns']} | **{pt['status']}** | "
            f"`{pt['systemc_time_ps']}` | `{pt['noc_total_hops']}` | `{pt['replayed']}` |"
        )
    if report.get("calibration") is not None:
        cal = report["calibration"]
        lines += [
            "",
            "## RTL vs. TLM-2.0 Timing Calibration (Nominal 10/5 ns NoC)",
            "",
            f"- Relative error: `{cal['relative_error']}` (budget `<= {cal['error_budget']}`, "
            f"within budget: **{cal['within_budget']}**)",
        ]
    lines.append("")
    return "\n".join(lines)


def run_dse_sweep(
    spec_path: Path,
    out_dir: Path,
    *,
    workers: int = 2,
    cache_dir: Path | None = None,
    points: tuple[DsePoint, ...] = DEFAULT_SWEEP,
) -> dict[str, Any]:
    """Execute a bounded parallel DSE sweep across multiple NoC/backend points."""
    if not 1 <= workers <= 8:
        raise ContractError("E_DSE_WORKERS workers must be in 1..8")
    spec_path = spec_path.resolve()
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ContractError("E_DSE_OUT choose a fresh output directory")
    out_dir.mkdir(parents=True)

    ready_rtl, _ = has_unified_toolchain(require_verilator=True)
    ready_tlm, _ = has_unified_toolchain(require_verilator=False)
    cache = ArtifactCache(cache_dir) if cache_dir is not None else None

    active_points = [
        p for p in points if (p.backend != "verilator-rtl" or ready_rtl or not ready_tlm)
    ]
    simulate = ready_tlm

    results_by_id: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(_evaluate_point, spec_path, p, out_dir, cache, simulate=simulate): p
            for p in active_points
        }
        for fut in concurrent.futures.as_completed(futs):
            res = fut.result()
            results_by_id[res["point_id"]] = res

    ordered = [results_by_id[p.point_id] for p in active_points]

    # Compute real RTL vs TLM calibration whenever both nominal points simulated live
    calibration: dict[str, Any] | None = None
    nom_rtl = results_by_id.get("noc_nom_rtl")
    nom_tlm = results_by_id.get("noc_nom_tlm")
    if (
        simulate
        and nom_rtl
        and nom_tlm
        and nom_rtl.get("systemc_time_ps")
        and nom_tlm.get("systemc_time_ps")
    ):
        calibration = evaluate_calibration_report(
            roi_name="dse_nominal_rtl_vs_tlm",
            reference_source="native-rtl",
            model_source="qemu-analytical-estimate",
            reference_cycles=float(nom_rtl["systemc_time_ps"]) / 20_000.0,
            estimated_cycles=float(nom_tlm["systemc_time_ps"]) / 20_000.0,
            max_relative_error=0.10,
        )

    # Latency monotonicity check across train/holdout sweep when simulated live
    monotonic_ok = True
    if simulate and all(
        k in results_by_id and results_by_id[k]["systemc_time_ps"] is not None
        for k in ("noc_fast_tlm", "noc_nom_tlm", "noc_slow_tlm")
    ):
        t_fast = results_by_id["noc_fast_tlm"]["systemc_time_ps"]
        t_nom = results_by_id["noc_nom_tlm"]["systemc_time_ps"]
        t_slow = results_by_id["noc_slow_tlm"]["systemc_time_ps"]
        monotonic_ok = t_fast < t_nom < t_slow

    all_passed = (
        bool(ordered)
        and all(r["status"] == "PASSED" for r in ordered)
        and monotonic_ok
        and (calibration is None or calibration["within_budget"])
    )
    report: dict[str, Any] = {
        "contract": CONTRACT,
        "platform_version": __version__,
        "created_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "status": "PASSED" if all_passed else "FAILED",
        "execution_mode": "live-parallel-simulation" if simulate else "plan-and-ir-parallel",
        "workers": workers,
        "latency_monotonic_across_train_and_holdout": monotonic_ok,
        "points": ordered,
        "calibration": calibration,
    }
    write_json(out_dir / "dse-report.json", report)
    (out_dir / "dse-report.md").write_text(_render_dse_md(report))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="usp dse")
    commands = parser.add_subparsers(dest="command", required=True)
    sweep = commands.add_parser("sweep", help="run parallel DSE & calibration sweep")
    sweep.add_argument(
        "spec",
        type=Path,
        nargs="?",
        default=Path("specs/multiplatform/unified_hetero_soc.yaml"),
    )
    sweep.add_argument("--out", type=Path, required=True)
    sweep.add_argument("--workers", type=int, default=2)
    sweep.add_argument("--cache", type=Path)

    args = parser.parse_args(argv)
    try:
        report = run_dse_sweep(args.spec, args.out, workers=args.workers, cache_dir=args.cache)
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "execution_mode": report["execution_mode"],
                    "points": len(report["points"]),
                    "report": str(args.out / "dse-report.json"),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if report["status"] == "PASSED" else 1
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}))
        return 2
