"""CHIA-style Parallel DSE & Timing Calibration Cluster Orchestrator (E4/E7).

Executes multi-configuration SoC design-space sweeps across pluggable parallel cluster backends:
  - `thread`: bounded local `ThreadPoolExecutor`
  - `process`: true multi-core local `ProcessPoolExecutor` (zero GIL contention)
  - `ray`: enterprise distributed Ray cluster (requires `ray`; fails explicitly with
    `E_CLUSTER_UNAVAILABLE` when requested without Ray — never silently falls back)
Features content-addressed tamper-checked `ArtifactCache`, `train`/`holdout` split tracking,
monotonicity verification, and M7 RTL-vs-TLM timing calibration.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import datetime
import importlib
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
    resolve_unified_src_dir,
    run_unified_soc,
)
from .v04_contracts import evaluate_calibration_report
from .version import __version__

CONTRACT = "usp-dse-cluster-1"
CLUSTER_BACKENDS = ("thread", "process", "ray")


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
    src_link = work_dir / "unified_systemc"
    if not src_link.exists():
        src_link.symlink_to(resolve_unified_src_dir(base_spec_path))
    return variant_path


def _evaluate_point_worker(
    base_spec_str: str,
    point: DsePoint,
    out_dir_str: str,
    cache_dir_str: str | None,
    simulate: bool,
) -> dict[str, Any]:
    """Top-level picklable worker for thread, process, and Ray cluster backends."""
    base_spec_path = Path(base_spec_str)
    out_dir = Path(out_dir_str)
    cache = ArtifactCache(Path(cache_dir_str)) if cache_dir_str is not None else None
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
        ip_res = summary.get("ip_results", {})
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
            "npu_rtl_cycles": ip_res.get("npu_rtl_cycles", 0),
            "sha256_rtl_cycles": ip_res.get("sha256_rtl_cycles", 0),
            "l2_hits": ip_res.get("l2_hits", 0),
            "l2_misses": ip_res.get("l2_misses", 0),
            "coverage_percent": run_res.get("coverage", {}).get("coverage_percent"),
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
            "sha256_rtl_cycles": 0,
            "l2_hits": 0,
            "l2_misses": 0,
            "coverage_percent": None,
            "wall_seconds": round(time.monotonic() - started, 4),
            "replayed": False,
        }

    if cache is not None and result["status"] == "PASSED":
        cache.put("dse_point", cache_inputs, result)
    return result


def _dispatch_cluster(
    cluster: str,
    workers: int,
    base_spec_path: Path,
    active_points: list[DsePoint],
    out_dir: Path,
    cache_dir: Path | None,
    simulate: bool,
) -> list[dict[str, Any]]:
    if cluster not in CLUSTER_BACKENDS:
        raise ContractError(f"E_DSE_CLUSTER unsupported cluster backend: {cluster}")
    base_str = str(base_spec_path)
    out_str = str(out_dir)
    cache_str = str(cache_dir.resolve()) if cache_dir is not None else None

    if cluster == "ray":
        try:
            ray = importlib.import_module("ray")
        except ImportError as exc:
            raise ContractError(
                "E_CLUSTER_UNAVAILABLE Ray is not installed; install ray on cluster workers "
                "or select --cluster process/thread (silent fallback is forbidden)"
            ) from exc
        if not ray.is_initialized():
            ray.init(ignore_reinit_error=True, num_cpus=workers)
        remote_fn = ray.remote(num_cpus=1)(_evaluate_point_worker)
        refs = [remote_fn.remote(base_str, p, out_str, cache_str, simulate) for p in active_points]
        return list(ray.get(refs))

    pool_cls = (
        concurrent.futures.ProcessPoolExecutor
        if cluster == "process"
        else concurrent.futures.ThreadPoolExecutor
    )
    results_by_id: dict[str, dict[str, Any]] = {}
    with pool_cls(max_workers=workers) as pool:
        futs = {
            pool.submit(_evaluate_point_worker, base_str, p, out_str, cache_str, simulate): p
            for p in active_points
        }
        for fut in concurrent.futures.as_completed(futs):
            res = fut.result()
            results_by_id[res["point_id"]] = res
    return [results_by_id[p.point_id] for p in active_points]


def _render_dse_md(report: dict[str, Any]) -> str:
    lines = [
        "# Parallel DSE & RTL↔TLM Calibration Report",
        "",
        f"- **Contract:** `{report['contract']}`",
        f"- **Status:** **{report['status']}**",
        f"- **Cluster Backend:** `{report['cluster_backend']}` (`workers={report['workers']}`)",
        f"- **Execution Mode:** `{report['execution_mode']}`",
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
    cluster: str = "process",
    cache_dir: Path | None = None,
    points: tuple[DsePoint, ...] = DEFAULT_SWEEP,
) -> dict[str, Any]:
    """Execute a bounded parallel DSE sweep across pluggable cluster backends."""
    if not 1 <= workers <= 8:
        raise ContractError("E_DSE_WORKERS workers must be in 1..8")
    spec_path = spec_path.resolve()
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ContractError("E_DSE_OUT choose a fresh output directory")
    out_dir.mkdir(parents=True)

    ready_rtl, _ = has_unified_toolchain(require_verilator=True)
    ready_tlm, _ = has_unified_toolchain(require_verilator=False)
    active_points = [
        p for p in points if (p.backend != "verilator-rtl" or ready_rtl or not ready_tlm)
    ]
    simulate = ready_tlm

    ordered = _dispatch_cluster(
        cluster, workers, spec_path, active_points, out_dir, cache_dir, simulate
    )
    results_by_id = {r["point_id"]: r for r in ordered}

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
            roi_name="dse_nominal_4rtl_vs_tlm",
            reference_source="native-rtl",
            model_source="qemu-analytical-estimate",
            reference_cycles=float(nom_rtl["systemc_time_ps"]) / 20_000.0,
            estimated_cycles=float(nom_tlm["systemc_time_ps"]) / 20_000.0,
            max_relative_error=0.10,
        )

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
        "cluster_backend": cluster,
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
    sweep.add_argument("--cluster", choices=CLUSTER_BACKENDS, default="process")
    sweep.add_argument("--cache", type=Path)

    args = parser.parse_args(argv)
    try:
        report = run_dse_sweep(
            args.spec,
            args.out,
            workers=args.workers,
            cluster=args.cluster,
            cache_dir=args.cache,
        )
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "cluster_backend": report["cluster_backend"],
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
