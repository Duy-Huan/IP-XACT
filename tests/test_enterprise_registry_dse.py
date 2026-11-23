"""Tests for E3 Versioned IP Plugin Registry and E4 CHIA-style Parallel DSE Orchestrator."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from socsim.chia_loop import run_chia_loop
from socsim.dashboard import generate_dashboard
from socsim.domain import ContractError
from socsim.dse_cluster import run_dse_sweep
from socsim.ip_registry import (
    list_registry,
    publish_package,
    resolve_packages,
    verify_registry,
)
from socsim.ip_scaffold import scaffold_ip_package
from socsim.unified_systemc_soc import has_unified_toolchain

ROOT = Path(__file__).resolve().parents[1]
MAC_NPU_PKG = ROOT / "specs/multiplatform/packages/mac_npu/package.yaml"
COUNTER_PKG = ROOT / "specs/flows/packages/counter/package.yaml"
UNIFIED_SPEC = ROOT / "specs/multiplatform/unified_hetero_soc.yaml"


def test_ip_registry_publish_immutability_semver_ranges_and_tamper_detection(tmp_path):
    reg = tmp_path / "registry"
    e1 = publish_package(MAC_NPU_PKG, reg, version="1.0.0")
    e1_minor = publish_package(MAC_NPU_PKG, reg, version="1.2.0")
    e2 = publish_package(COUNTER_PKG, reg, version="1.0.0")
    assert e1["key"] == "user.mac_npu@1.0.0"
    assert e1_minor["key"] == "user.mac_npu@1.2.0"
    assert e2["key"] == "user.counter@1.0.0"

    # Re-publishing the exact same version must fail immutably
    with pytest.raises(ContractError, match="E_REGISTRY_IMMUTABLE"):
        publish_package(MAC_NPU_PKG, reg, version="1.0.0")

    listing = list_registry(reg)
    assert listing["count"] == 3
    assert verify_registry(reg)["status"] == "PASSED"

    # Resolve using semver ranges (^1.0.0 picks 1.2.0; >=1.0.0,<2.0.0 picks 1.0.0)
    ws = tmp_path / "team-workspace"
    lock = resolve_packages(
        reg,
        {"user.mac_npu": "^1.0.0", "user.counter": ">=1.0.0,<2.0.0"},
        ws,
    )
    assert lock["contract"] == "usp-ip-registry-lock-1"
    assert "user.mac_npu@1.2.0" in lock["resolved"]
    assert "user.counter@1.0.0" in lock["resolved"]
    assert (ws / "ip-registry.lock.json").is_file()
    assert Path(lock["resolved"]["user.mac_npu@1.2.0"]["package_yaml"]).is_file()

    # Tilde constraint ~1.0.0 must pick 1.0.0 (not 1.2.0)
    lock_tilde = resolve_packages(reg, {"user.mac_npu": "~1.0.0"}, tmp_path / "ws-tilde")
    assert "user.mac_npu@1.0.0" in lock_tilde["resolved"]

    # Corrupting an archive must be caught by both verify_registry and resolve_packages
    archive = reg / e1["archive"]
    archive.write_bytes(archive.read_bytes() + b"\x00")
    with pytest.raises(ContractError, match="E_REGISTRY_TAMPER"):
        verify_registry(reg)
    with pytest.raises(ContractError, match="E_REGISTRY_TAMPER"):
        resolve_packages(reg, {"user.mac_npu": "1.0.0"}, tmp_path / "ws-bad")


def test_ip_scaffolder_generates_publishable_ipxact_rtl_and_systemc_package(tmp_path):
    sc_dir = tmp_path / "neural_crc"
    res = scaffold_ip_package(name="neural_crc", out_dir=sc_dir)
    assert res["status"] == "PASSED"
    assert res["package_id"] == "user.neural_crc"
    assert Path(res["ipxact_xml"]).is_file()
    assert Path(res["rtl_sv"]).is_file()
    assert Path(res["tlm_cpp"]).is_file()
    assert "1685-2022" in Path(res["ipxact_xml"]).read_text()

    reg = tmp_path / "reg"
    pub = publish_package(Path(res["package_yaml"]), reg, version="1.0.0")
    assert pub["key"] == "user.neural_crc@1.0.0"
    assert verify_registry(reg)["status"] == "PASSED"
    lock = resolve_packages(reg, {"user.neural_crc": "^1.0.0"}, tmp_path / "ws")
    assert "user.neural_crc@1.0.0" in lock["resolved"]


def test_parallel_dse_process_cluster_cache_ray_gate_and_html5_dashboard(tmp_path):
    cache_dir = tmp_path / "dse-cache"
    run1 = run_dse_sweep(
        UNIFIED_SPEC, tmp_path / "dse-1", workers=2, cluster="process", cache_dir=cache_dir
    )
    assert run1["status"] == "PASSED"
    assert run1["cluster_backend"] == "process"
    assert (tmp_path / "dse-1/dse-report.json").is_file()
    assert (tmp_path / "dse-1/dse-report.md").is_file()
    assert all(pt["replayed"] is False for pt in run1["points"])

    ready_tlm, _ = has_unified_toolchain(require_verilator=False)
    if ready_tlm:
        assert run1["execution_mode"] == "live-parallel-simulation"
        assert run1["latency_monotonic_across_train_and_holdout"] is True
        if run1["calibration"] is not None:
            assert run1["calibration"]["within_budget"] is True
    else:
        if os.environ.get("USP_REQUIRE_SYSTEMC") == "1":
            pytest.fail("USP_REQUIRE_SYSTEMC=1 requires live parallel DSE simulation")
        assert run1["execution_mode"] == "plan-and-ir-parallel"

    # Second sweep using same cache MUST replay and explicitly mark replayed=True
    run2 = run_dse_sweep(
        UNIFIED_SPEC, tmp_path / "dse-2", workers=2, cluster="thread", cache_dir=cache_dir
    )
    assert run2["status"] == "PASSED"
    assert all(pt["replayed"] is True for pt in run2["points"])

    # Requesting Ray cluster without ray installed MUST fail explicitly (no silent fallback)
    try:
        import ray  # noqa: F401
    except ImportError:
        with pytest.raises(ContractError, match="E_CLUSTER_UNAVAILABLE"):
            run_dse_sweep(UNIFIED_SPEC, tmp_path / "dse-ray", workers=2, cluster="ray")

    # CHIA-style Cyclic Optimization Loop + Isolated Verification Oracle + Replay
    loop_cache = tmp_path / "loop-cache"
    loop1 = run_chia_loop(UNIFIED_SPEC, tmp_path / "loop-1", max_iters=3, cache_dir=loop_cache)
    assert loop1["status"] == "PASSED"
    assert loop1["contract"] == "usp-chia-loop-1"
    assert len(loop1["history"]) == 3
    assert all(h["replayed"] is False for h in loop1["history"])
    if ready_tlm:
        assert loop1["execution_mode"] == "live-cyclic-optimization"
        assert loop1["best_speedup_vs_baseline"] > 1.0
        assert len(loop1["pareto_frontier"]) >= 1

    loop2 = run_chia_loop(UNIFIED_SPEC, tmp_path / "loop-2", max_iters=3, cache_dir=loop_cache)
    assert loop2["status"] == "PASSED"
    assert all(h["replayed"] is True for h in loop2["history"])

    # Zero-CDN air-gapped HTML5 Verification Dashboard with DSE + CHIA Loop SVG
    dash_path = tmp_path / "dashboard.html"
    dash = generate_dashboard(
        out_html=dash_path,
        dse_report=tmp_path / "dse-1/dse-report.json",
        loop_report=tmp_path / "loop-1/loop-report.json",
    )
    assert dash["status"] == "PASSED"
    assert dash["contract"] == "usp-verification-dashboard-1"
    html_text = dash_path.read_text()
    assert "<!DOCTYPE html>" in html_text
    assert "noc_fast_tlm" in html_text
    assert "baseline_distributed_2x2" in html_text
    assert "http://" not in html_text and "https://" not in html_text
