"""Tests for E3 Versioned IP Plugin Registry and E4 CHIA-style Parallel DSE Orchestrator."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from socsim.domain import ContractError
from socsim.dse_cluster import run_dse_sweep
from socsim.ip_registry import (
    list_registry,
    publish_package,
    resolve_packages,
    verify_registry,
)
from socsim.unified_systemc_soc import has_unified_toolchain

ROOT = Path(__file__).resolve().parents[1]
MAC_NPU_PKG = ROOT / "specs/multiplatform/packages/mac_npu/package.yaml"
COUNTER_PKG = ROOT / "specs/flows/packages/counter/package.yaml"
UNIFIED_SPEC = ROOT / "specs/multiplatform/unified_hetero_soc.yaml"


def test_ip_registry_publish_immutability_resolve_and_tamper_detection(tmp_path):
    reg = tmp_path / "registry"
    e1 = publish_package(MAC_NPU_PKG, reg, version="1.0.0")
    e2 = publish_package(COUNTER_PKG, reg, version="1.0.0")
    assert e1["key"] == "user.mac_npu@1.0.0"
    assert e2["key"] == "user.counter@1.0.0"

    # Re-publishing the exact same version must fail immutably
    with pytest.raises(ContractError, match="E_REGISTRY_IMMUTABLE"):
        publish_package(MAC_NPU_PKG, reg, version="1.0.0")

    listing = list_registry(reg)
    assert listing["count"] == 2
    assert verify_registry(reg)["status"] == "PASSED"

    # Resolve into a fresh collaborator workspace
    ws = tmp_path / "team-workspace"
    lock = resolve_packages(
        reg,
        {"user.mac_npu": "1.0.0", "user.counter": "1.0.0"},
        ws,
    )
    assert lock["contract"] == "usp-ip-registry-lock-1"
    assert len(lock["resolved"]) == 2
    assert (ws / "ip-registry.lock.json").is_file()
    assert Path(lock["resolved"]["user.mac_npu@1.0.0"]["package_yaml"]).is_file()

    # Corrupting an archive must be caught by both verify_registry and resolve_packages
    archive = reg / e1["archive"]
    archive.write_bytes(archive.read_bytes() + b"\x00")
    with pytest.raises(ContractError, match="E_REGISTRY_TAMPER"):
        verify_registry(reg)
    with pytest.raises(ContractError, match="E_REGISTRY_TAMPER"):
        resolve_packages(reg, {"user.mac_npu": "1.0.0"}, tmp_path / "ws-bad")


def test_parallel_dse_sweep_cache_replay_and_calibration(tmp_path):
    cache_dir = tmp_path / "dse-cache"
    run1 = run_dse_sweep(UNIFIED_SPEC, tmp_path / "dse-1", workers=2, cache_dir=cache_dir)
    assert run1["status"] == "PASSED"
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
    run2 = run_dse_sweep(UNIFIED_SPEC, tmp_path / "dse-2", workers=2, cache_dir=cache_dir)
    assert run2["status"] == "PASSED"
    assert all(pt["replayed"] is True for pt in run2["points"])
