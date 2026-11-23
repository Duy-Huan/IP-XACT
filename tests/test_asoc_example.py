"""Unit and live SystemC/Verilator integration tests for the official ASoC example."""

from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from socsim.chia_loop import run_chia_loop
from socsim.dashboard import generate_dashboard
from socsim.domain import ContractError
from socsim.dse_cluster import run_dse_sweep
from socsim.native_flow import preflight_flow
from socsim.soc_native import generate as generate_native_soc
from socsim.unified_systemc_soc import (
    calibrate_unified_soc,
    expected_markers_for_spec,
    generate_unified_artifacts,
    has_unified_toolchain,
    load_unified_spec,
    resolve_unified_src_dir,
    run_unified_soc,
)

ROOT = Path(__file__).resolve().parents[1]
ASOC_DIR = ROOT / "examples/ASoC"
ASOC_UNIFIED = ASOC_DIR / "asoc_unified.yaml"
ASOC_FABRIC = ASOC_DIR / "asoc_fabric.yaml"
ASOC_FLOW = ASOC_DIR / "asoc_flow.yaml"


def test_asoc_unified_spec_validates_20_ips_2_clusters_and_3_bridges(tmp_path: Path) -> None:
    spec = load_unified_spec(ASOC_UNIFIED)
    assert spec["name"] == "ASoC"
    assert spec["cpu"]["harts"] == 4
    assert len(spec["cpu"]["clusters"]) == 2
    assert len(spec["noc"]["bridges"]) == 3
    assert len(spec["devices"]) == 20

    models = {str(d["model"]) for d in spec["devices"]}
    assert {
        "user.mac_npu",
        "user.lpu_engine",
        "user.pmp_guard",
        "user.cluster_mailbox",
        "user.dma_engine",
        "sram",
        "uart_aux",
    } <= models

    plan_dir = tmp_path / "asoc-plan"
    ir = generate_unified_artifacts(ASOC_UNIFIED, plan_dir)
    assert ir["name"] == "ASoC"
    header = (plan_dir / "unified_soc_map.h").read_text()
    routes = (plan_dir / "unified_soc_routes.h").read_text()
    linker = (plan_dir / "link.ld").read_text()

    assert "#define USOC_HART_COUNT 4U" in header
    assert "#define USOC_CLUSTER_COUNT 2U" in header
    assert "#define USOC_HAS_LPU 1" in header
    assert "#define USOC_HAS_MAILBOX 1" in header
    assert "#define USOC_HAS_AI_SRAM 1" in header
    assert "#define USOC_HAS_UART_AUX 1" in header
    assert "#define USOC_PRIMARY_UART_BASE 0x11000000ULL" in header
    assert "USOC_ROUTE_COUNT = 20U;" in routes
    assert "USOC_BRIDGE_AXI_AI" in routes
    assert "USOC_BRIDGE_AXI_SEC" in routes
    assert "USOC_BRIDGE_APB_PERIPH" in routes
    assert "PROVIDE(_hart2_stack_top = 0x800D0000);" in linker
    assert "PROVIDE(_hart3_stack_top = 0x800C0000);" in linker


def test_asoc_negative_validation_rejects_bad_bridge_and_bad_cluster(tmp_path: Path) -> None:
    base = yaml.safe_load(ASOC_UNIFIED.read_text())

    bad_bridge = copy.deepcopy(base)
    bad_bridge["devices"][5]["bridge"] = "unknown_bus_bridge"
    p1 = tmp_path / "bad_bridge.yaml"
    p1.write_text(yaml.safe_dump(bad_bridge))
    with pytest.raises(ContractError, match="undeclared bus bridge"):
        load_unified_spec(p1)

    bad_cluster = copy.deepcopy(base)
    bad_cluster["cpu"]["clusters"][1]["harts"] = [1, 2]
    p2 = tmp_path / "bad_cluster.yaml"
    p2.write_text(yaml.safe_dump(bad_cluster))
    with pytest.raises(ContractError, match="invalid/duplicate cluster hart"):
        load_unified_spec(p2)


def test_asoc_fabric_ipxact_generation_and_flow_preflight(tmp_path: Path) -> None:
    gen_dir = tmp_path / "asoc-fabric-gen"
    res = generate_native_soc(ASOC_FABRIC, gen_dir, engine="both", input_format="fabric")
    assert res["status"] == "GENERATED"
    assert res["instances"] == 20
    assert (gen_dir / "native-ir.json").is_file()
    assert (gen_dir / "ipxact/design.xml").is_file()

    pf = preflight_flow(ASOC_FLOW)
    assert pf["status"] == "READY"
    assert pf["harts"] == 4
    assert pf["blocking_issues"] == []


def test_asoc_live_multicluster_7rtl_simulation_calibration_dse_loop_dashboard(
    tmp_path: Path,
) -> None:
    """Execute ASoC live (2 clusters/4 harts + 20 IPs + 7x Verilator RTL + 30-bin coverage)."""
    ready, missing = has_unified_toolchain()
    run_dir = tmp_path / "asoc-run"
    if not ready:
        if os.environ.get("USP_REQUIRE_SYSTEMC") == "1":
            pytest.fail(f"USP_REQUIRE_SYSTEMC=1 requires unified toolchain for ASoC: {missing}")
        blocked = run_unified_soc(ASOC_UNIFIED, run_dir)
        assert blocked["status"] == "BLOCKED"
        assert (run_dir / "unified_soc_routes.h").is_file()
        # Plan-only DSE & CHIA loop still verify IR generation across all ASoC candidates
        dse = run_dse_sweep(ASOC_UNIFIED, tmp_path / "asoc-dse", workers=2, cluster="process")
        assert dse["status"] == "PASSED"
        loop = run_chia_loop(ASOC_UNIFIED, tmp_path / "asoc-loop", max_iters=4)
        assert loop["status"] == "PASSED"
        return

    report = run_unified_soc(ASOC_UNIFIED, run_dir, backend="auto", verify_gdb=True)
    assert report["status"] == "PASSED", json.dumps(report, indent=2)
    spec = load_unified_spec(ASOC_UNIFIED)
    pos = report["positive_run"]
    assert pos["returncode"] == 0
    for marker in expected_markers_for_spec(spec):
        assert marker in pos["console_output"], f"Missing ASoC marker: {marker}"

    summ = pos["summary"]
    ip_res = summ["ip_results"]
    bridges = summ["bus_bridge_hits"]
    assert summ["harts"] == 4
    assert summ["clusters"] == 2
    assert summ["ir_route_entries"] == 20
    assert summ["hart0_instructions"] > 0
    assert summ["hart1_instructions"] > 0
    assert summ["hart2_instructions"] > 0
    assert summ["hart3_instructions"] > 0
    assert summ["atomic_ops_executed"] >= 48
    assert summ["smp_ipi_delivered"] >= 3
    assert ip_res["mailbox_messages"] == 1
    assert ip_res["lpu_linear_ops"] == 2
    assert ip_res["lpu_tokens_decoded"] == 1
    assert ip_res["lpu_argmax_idx"] == 1
    assert ip_res["lpu_max_logit"] == 485
    assert ip_res["npu_acc_final"] == 126
    assert ip_res["npu_matmul_ops"] == 1
    assert ip_res["uart_aux_tx_bytes"] > 0
    assert bridges["noc_backbone"] > 0
    assert bridges["axi_ai_bridge"] > 0
    assert bridges["axi_sec_bridge"] > 0
    assert bridges["apb_periph_bridge"] > 0

    assert report["coverage"]["bins_total"] == 30
    assert report["coverage"]["coverage_percent"] == 100.0
    assert report["coverage"]["status"] == "PASSED"
    assert report["gdb_rsp"]["status"] == "PASSED"
    assert report["negative_run"]["expected_failure_detected"] is True

    cal_path: Path | None = None
    if shutil.which("verilator") is not None:
        assert ip_res["lpu_rtl_cycles"] > 0
        cal_dir = tmp_path / "asoc-cal"
        cal = calibrate_unified_soc(ASOC_UNIFIED, cal_dir)
        assert cal["status"] == "PASSED", json.dumps(cal, indent=2)
        assert cal["functional_parity"] is True
        assert cal["rtl_cycles"]["lpu"] > 0
        cal_path = cal_dir / "calibration.json"

    loop_dir = tmp_path / "asoc-loop"
    loop = run_chia_loop(ASOC_UNIFIED, loop_dir, max_iters=4, backend="systemc-tlm")
    assert loop["status"] == "PASSED"
    assert loop["converged"] is True

    dash = generate_dashboard(
        tmp_path / "asoc-dashboard.html",
        unified_report=run_dir / "report.json",
        calibration_report=cal_path,
        loop_report=loop_dir / "loop-report.json",
    )
    assert dash["status"] == "PASSED"
    html_text = (tmp_path / "asoc-dashboard.html").read_text()
    assert "ASoC" in html_text


def test_resolve_unified_src_dir_works_for_examples_and_temp_copies(tmp_path: Path) -> None:
    resolved_direct = resolve_unified_src_dir(ASOC_UNIFIED)
    assert (resolved_direct / "unified_systemc_soc.cpp").is_file()
    assert (resolved_direct / "rtl" / "lpu_rtl.sv").is_file()

    copied = tmp_path / "copied_asoc.yaml"
    copied.write_text(ASOC_UNIFIED.read_text())
    resolved_copy = resolve_unified_src_dir(copied)
    assert resolved_copy == resolved_direct
