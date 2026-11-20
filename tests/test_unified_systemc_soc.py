"""Tests for the single unified multi-platform SystemC/TLM-2.0 SoC emulator and RV64 SW."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest
import yaml

from socsim.domain import ContractError
from socsim.unified_systemc_soc import (
    EXPECTED_POSITIVE_MARKERS,
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
    assert len(spec["devices"]) == 10

    out = tmp_path / "plan"
    ir = generate_unified_artifacts(SPEC, out)
    assert ir["contract"] == "unified-systemc-soc-1"
    header = (out / "unified_soc_map.h").read_text()
    linker = (out / "link.ld").read_text()
    assert "#define USOC_CHIPYARD_GCD_0_BASE 0x13000000ULL" in header
    assert "#define USOC_OPENTITAN_GPIO_0_IRQ 13U" in header
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
    """Compile and execute unified SystemC 2x2 NoC SoC + real RV64 SW when toolchain is present."""
    ready, missing = has_unified_toolchain()
    out = tmp_path / "unified-run"
    if not ready:
        if os.environ.get("SOCSIM_REQUIRE_INTEGRATION") == "1":
            pytest.fail(f"SOCSIM_REQUIRE_INTEGRATION=1 requires unified toolchain: {missing}")
        # Even without local SystemC/RV64-GCC, verify honest BLOCKED reporting (no fake pass)
        blocked = run_unified_soc(SPEC, out)
        assert blocked["status"] == "BLOCKED"
        assert (out / "unified_soc_map.h").is_file()
        assert (out / "report.json").is_file()
        return

    report = run_unified_soc(SPEC, out)
    assert report["status"] == "PASSED", json.dumps(report, indent=2)
    pos = report["positive_run"]
    assert pos["returncode"] == 0
    for marker in EXPECTED_POSITIVE_MARKERS:
        assert marker in pos["console_output"]
    assert all(pos["checks"].values())
    assert pos["summary"]["ip_results"]["gcd_ops_completed"] == 3
    assert pos["summary"]["ip_results"]["npu_acc_final"] == 126
    assert pos["summary"]["ip_results"]["pmp_guard_fault_count"] == 1
    assert all(h > 0 for h in pos["summary"]["router_hits"])

    neg = report["negative_run"]
    assert neg["expected_failure_detected"] is True
    assert neg["status"] == "FAILED"
    assert neg["returncode"] != 0
    assert (out / "report.md").is_file()
    assert (out / "noc-trace.jsonl").is_file()
