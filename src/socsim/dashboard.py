"""Self-contained air-gapped HTML5 Verification, Coverage, Calibration & DSE Dashboard (E7)."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from .domain import ContractError
from .ipxact import digest
from .version import __version__


def _read_optional_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    return json.loads(path.read_text())


def generate_dashboard(
    *,
    out_html: Path,
    unified_report: Path | None = None,
    calibration_report: Path | None = None,
    dse_report: Path | None = None,
) -> dict[str, Any]:
    """Generate a zero-CDN self-contained HTML5 engineering dashboard from real run JSONs."""
    u = _read_optional_json(unified_report)
    c = _read_optional_json(calibration_report)
    d = _read_optional_json(dse_report)
    if u is None and c is None and d is None:
        raise ContractError("E_DASHBOARD provide at least one existing JSON evidence report")

    out_html = out_html.resolve()
    out_html.parent.mkdir(parents=True, exist_ok=True)

    design = (u or {}).get("design", "Unified_Hetero_MultiPlatform_SoC")
    status = (u or c or d or {}).get("status", "UNKNOWN")
    cov = (u or {}).get("coverage", {})
    cov_pct = cov.get("coverage_percent", 0)
    summary = (u or {}).get("positive_run", {}).get("summary", {})
    ip_hits = summary.get("ip_hits", {})
    ip_res = summary.get("ip_results", {})

    ip_rows = "\n".join(
        f"<tr><td><code>{html.escape(str(k))}</code></td><td>{int(v)}</td></tr>"
        for k, v in ip_hits.items()
    )
    cov_rows = "\n".join(
        f"<tr><td><code>{html.escape(str(k))}</code></td>"
        f"<td class='{'ok' if v else 'fail'}'>{'HIT' if v else 'MISS'}</td></tr>"
        for k, v in cov.get("bins", {}).items()
    )
    dse_rows = "\n".join(
        f"<tr><td><code>{html.escape(str(pt['point_id']))}</code></td>"
        f"<td>{html.escape(str(pt['split']))}</td>"
        f"<td><code>{html.escape(str(pt['backend']))}</code></td>"
        f"<td>{pt['hop_latency_ns']}/{pt['link_latency_ns']} ns</td>"
        f"<td class='{'ok' if pt['status'] == 'PASSED' else 'fail'}'>{pt['status']}</td>"
        f"<td>{pt.get('systemc_time_ps')}</td><td>{pt.get('replayed')}</td></tr>"
        for pt in (d or {}).get("points", [])
    )
    cal_block = ""
    if c is not None and c.get("calibration"):
        cal = c["calibration"]
        cal_block = (
            f"<section class='card'><h2>RTL ↔ TLM-2.0 Timing Calibration</h2>"
            f"<p>Status: <strong class='{'ok' if c['status'] == 'PASSED' else 'fail'}'>"
            f"{html.escape(str(c['status']))}</strong> | "
            f"Functional parity: <code>{c.get('functional_parity')}</code> | "
            f"Relative error: <code>{cal['relative_error']}</code> "
            f"(budget ≤ <code>{cal['error_budget']}</code>)</p></section>"
        )

    status_cls = "ok" if status == "PASSED" else "fail"
    hops_dma = (
        f"{summary.get('noc_total_hops', '—')} / {summary.get('noc_dma_master_transactions', '—')}"
    )
    rtl_total = (
        ip_res.get("npu_rtl_cycles", 0)
        + ip_res.get("sha256_rtl_cycles", 0)
        + ip_res.get("spi_rtl_cycles", 0)
        + ip_res.get("l2_rtl_cycles", 0)
    )

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>USP SoC Verification & Coverage Dashboard — {html.escape(str(design))}</title>
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #e2e8f0;
         margin: 0; padding: 24px; line-height: 1.5; }}
  h1, h2 {{ margin-top: 0; color: #f8fafc; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
           gap: 14px; margin-bottom: 18px; }}
  .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px;
           padding: 16px; margin-bottom: 16px; }}
  .metric {{ font-size: 1.7rem; font-weight: 700; color: #38bdf8; }}
  .ok {{ color: #4ade80; font-weight: 600; }}
  .fail {{ color: #f87171; font-weight: 600; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.92rem; }}
  th, td {{ border-bottom: 1px solid #334155; padding: 8px 10px; text-align: left; }}
  th {{ color: #94a3b8; }}
  code {{ background: #0b1220; padding: 2px 6px; border-radius: 4px; color: #7dd3fc; }}
  .bar {{ height: 12px; background: #334155; border-radius: 6px; overflow: hidden; }}
  .bar > div {{ height: 100%; background: #22c55e; width: {cov_pct}%; }}
</style>
</head>
<body>
  <h1>USP Multi-Platform SoC Verification Dashboard</h1>
  <p>Design: <code>{html.escape(str(design))}</code> | Platform: <code>v{__version__}</code> |
     Overall: <strong class="{status_cls}">{html.escape(str(status))}</strong></p>
  <div class="grid">
    <div class="card"><div>Functional Coverage</div><div class="metric">{cov_pct}%</div>
      <div class="bar"><div></div></div></div>
    <div class="card"><div>Instructions Executed</div>
      <div class="metric">{summary.get("instructions_executed", "—")}</div></div>
    <div class="card"><div>NoC 2×2 Hops / DMA Tx</div>
      <div class="metric">{hops_dma}</div></div>
    <div class="card"><div>4× Verilator RTL Cycles</div>
      <div class="metric">{rtl_total}</div></div>
  </div>
  {cal_block}
  <section class="card">
    <h2>Mapped Multi-Platform IP Transactions</h2>
    <table><thead><tr><th>IP Instance</th><th>Transactions</th></tr></thead>
    <tbody>{ip_rows or "<tr><td colspan='2'>No unified run data</td></tr>"}</tbody></table>
  </section>
  <section class="card">
    <h2>20-Bin Functional &amp; Assertion Coverage</h2>
    <table><thead><tr><th>Verification Bin</th><th>Status</th></tr></thead>
    <tbody>{cov_rows or "<tr><td colspan='2'>No coverage data</td></tr>"}</tbody></table>
  </section>
  <section class="card">
    <h2>Parallel DSE Sweep Results</h2>
    <table><thead><tr>
      <th>Point</th><th>Split</th><th>Backend</th><th>Hop/Link</th>
      <th>Status</th><th>Sim Time (ps)</th><th>Replayed</th>
    </tr></thead>
    <tbody>{dse_rows or "<tr><td colspan='7'>No DSE sweep data</td></tr>"}</tbody></table>
  </section>
</body>
</html>
"""
    out_html.write_text(doc)
    return {
        "contract": "usp-verification-dashboard-1",
        "status": "PASSED",
        "dashboard_html": str(out_html),
        "sha256": digest(out_html),
        "coverage_percent": cov_pct,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="usp dashboard")
    commands = parser.add_subparsers(dest="command", required=True)
    gen = commands.add_parser("generate", help="render self-contained HTML5 verification dashboard")
    gen.add_argument("--out", type=Path, required=True)
    gen.add_argument("--unified", type=Path)
    gen.add_argument("--calibration", type=Path)
    gen.add_argument("--dse", type=Path)
    args = parser.parse_args(argv)
    try:
        res = generate_dashboard(
            out_html=args.out,
            unified_report=args.unified,
            calibration_report=args.calibration,
            dse_report=args.dse,
        )
        print(json.dumps(res, indent=2, sort_keys=True))
        return 0
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}))
        return 2
