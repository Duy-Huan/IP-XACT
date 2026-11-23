"""Tests for the single unified SystemC/TLM-2.0 + Verilator RTL SoC emulator and RV64 SW."""

from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from socsim.domain import ContractError
from socsim.unified_systemc_soc import (
    EXPECTED_POSITIVE_MARKERS,
    calibrate_unified_soc,
    generate_unified_artifacts,
    has_unified_toolchain,
    load_unified_spec,
    run_unified_soc,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "specs/multiplatform/unified_hetero_soc.yaml"


def test_unified_spec_validates_and_generates_shared_map_and_linker(tmp_path):
    spec = load_unified_spec(SPEC)
    assert spec["name"] == "Unified_Hetero_MultiPlatform_SoC"
    assert len(spec["devices"]) == 14

    out = tmp_path / "plan"
    ir = generate_unified_artifacts(SPEC, out)
    assert ir["contract"] == "unified-systemc-soc-1"
    header = (out / "unified_soc_map.h").read_text()
    linker = (out / "link.ld").read_text()
    assert "#define USOC_CHIPYARD_GCD_0_BASE 0x13000000ULL" in header
    assert "#define USOC_OPENTITAN_GPIO_0_IRQ 13U" in header
    assert "#define USOC_DMA_ENGINE_0_IRQ 14U" in header
    assert "#define USOC_SHA256_CRYPTO_0_IRQ 15U" in header
    assert "#define USOC_SPI_CTRL_0_IRQ 16U" in header
    assert "#define USOC_L2_CACHE_0_BASE 0x1A000000ULL" in header
    assert "#define USOC_MAC_NPU_0_BASE 0x15000000ULL" in header
    assert "#define USOC_PMP_GUARD_0_IRQ 21U" in header
    assert "PROVIDE(_stack_top = 0x800F0000);" in linker


def test_unified_spec_rejects_overlap_and_duplicate_irq(tmp_path):
    base = yaml.safe_load(SPEC.read_text())

    overlap = copy.deepcopy(base)
    overlap["devices"][-1]["base"] = overlap["devices"][-2]["base"]
    p1 = tmp_path / "overlap.yaml"
    p1.write_text(yaml.safe_dump(overlap))
    with pytest.raises(ContractError, match="overlapping address windows"):
        load_unified_spec(p1)

    dup_irq = copy.deepcopy(base)
    dup_irq["devices"][-1]["irq"] = dup_irq["devices"][-2]["irq"]
    p2 = tmp_path / "dup_irq.yaml"
    p2.write_text(yaml.safe_dump(dup_irq))
    with pytest.raises(ContractError, match="duplicate or invalid PLIC IRQ"):
        load_unified_spec(p2)


def test_unified_systemc_soc_real_sw_execution(tmp_path):
    """Run 4x Verilator RTL + SystemC NoC, VCD, 20-bin coverage, GDB RSP, and calibration."""
    ready, missing = has_unified_toolchain()
    out = tmp_path / "unified-run"
    if not ready:
        if os.environ.get("USP_REQUIRE_SYSTEMC") == "1":
            pytest.fail(f"USP_REQUIRE_SYSTEMC=1 requires unified toolchain: {missing}")
        blocked = run_unified_soc(SPEC, out)
        assert blocked["status"] == "BLOCKED"
        assert (out / "unified_soc_map.h").is_file()
        assert (out / "report.json").is_file()
        return

    report = run_unified_soc(SPEC, out, backend="auto", verify_gdb=True)
    assert report["status"] == "PASSED", json.dumps(report, indent=2)
    pos = report["positive_run"]
    assert pos["returncode"] == 0
    for marker in EXPECTED_POSITIVE_MARKERS:
        assert marker in pos["console_output"]
    assert all(pos["checks"].values())
    ip_res = pos["summary"]["ip_results"]
    assert ip_res["gcd_ops_completed"] == 3
    assert ip_res["dma_bytes_transferred"] == 16
    assert ip_res["dma_bus_errors"] == 1
    assert ip_res["npu_acc_final"] == 126
    assert ip_res["npu_matmul_ops"] == 1
    assert ip_res["sha256_blocks_hashed"] == 1
    assert ip_res["spi_xfers_completed"] == 1
    assert ip_res["l2_hits"] > 0
    assert ip_res["l2_misses"] > 0
    assert ip_res["l2_dma_snoops"] >= 8
    assert ip_res["pmp_guard_fault_count"] == 2
    assert pos["summary"]["noc_dma_master_transactions"] >= 9
    assert all(h > 0 for h in pos["summary"]["router_hits"])

    # IEEE 1364 VCD waveform & 20-bin Functional Coverage
    assert (out / "unified-soc.vcd").is_file()
    assert (out / "coverage.json").is_file()
    assert report["coverage"]["bins_total"] == 20
    assert report["coverage"]["coverage_percent"] == 100.0
    assert report["coverage"]["status"] == "PASSED"

    # Live GDB RSP check
    assert report["gdb_rsp"]["status"] == "PASSED"
    assert report["gdb_rsp"]["continue_reply"] == "W00"

    # Negative guest failure check
    neg = report["negative_run"]
    assert neg["expected_failure_detected"] is True
    assert neg["status"] == "FAILED"
    assert neg["returncode"] != 0

    # Real 4x Verilator RTL vs TLM-2.0 calibration check when Verilator is installed
    if shutil.which("verilator") is not None:
        assert pos["summary"]["rtl_backend"] == "verilator-rtl"
        assert ip_res["npu_rtl_cycles"] > 0
        assert ip_res["sha256_rtl_cycles"] >= 64
        assert ip_res["spi_rtl_cycles"] >= 32
        assert ip_res["l2_rtl_cycles"] > 0
        cal = calibrate_unified_soc(SPEC, tmp_path / "unified-cal")
        assert cal["status"] == "PASSED", json.dumps(cal, indent=2)
        assert cal["functional_parity"] is True
        assert cal["calibration"]["within_budget"] is True
