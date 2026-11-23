"""Unit, negative, MCP, and live Podman SDK integration tests for BSoC & Multi-Platform Composer."""

from __future__ import annotations

import ast
import copy
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from socsim.domain import ContractError
from socsim.mcp_server import EmulatorMcpServer
from socsim.multiplatform_composer import (
    audit_composed_soc,
    calibrate_composed_soc,
    load_composed_spec,
    plan_composed_soc,
    run_composed_soc,
)
from socsim.native_package import read_package
from socsim.unified_systemc_soc import has_unified_toolchain

ROOT = Path(__file__).resolve().parents[1]
BSOC_DIR = ROOT / "examples/BSoC"
BSOC_SPEC = BSOC_DIR / "bsoc_composed.yaml"
POLY_PKG = BSOC_DIR / "packages/polyphase_12t/package.yaml"


def test_bsoc_spec_validates_all_telecom_domains_crossbars_streams_and_providers(
    tmp_path: Path,
) -> None:
    spec = load_composed_spec(BSOC_SPEC)
    assert spec["name"] == "BSoC"
    assert len(spec["clock_domains"]) == 2
    assert len(spec["fabrics"]) == 3
    assert len(spec["cdc_bridges"]) == 1
    assert len(spec["devices"]) == 22
    assert len(spec["streams"]) == 5

    audit = audit_composed_soc(BSOC_SPEC)
    assert audit["design"] == "BSoC"
    assert {"chipyard", "gem5", "qemu", "verilator-rtl", "usp-plugin", "mixed-signal-tlm"} <= set(
        audit["provider_summary"]
    )
    assert len(audit["verilator_rtl_blocks"]) == 5
    assert audit["live_usp_plugins"] == ["polyphase_12t_0"]
    assert audit["mixed_signal_blocks"] == ["tisar_adc_0"]

    pkg, _ = read_package(POLY_PKG)
    assert pkg["id"] == "bsoc.polyphase_12t"
    assert pkg["interface"]["protocol"] == "usp-mmio32-v1"

    plan_dir = tmp_path / "bsoc-plan"
    ir = plan_composed_soc(BSOC_SPEC, plan_dir)
    assert ir["name"] == "BSoC"
    assert ir["devices"] == 22
    assert (plan_dir / "binding-lock.json").is_file()
    assert (plan_dir / "ipxact/design.xml").is_file()
    assert (plan_dir / "qemu/bsoc.dts").is_file()
    assert (plan_dir / "chipyard/BSoCChipyardConfig.scala").is_file()

    gem5_code = (plan_dir / "gem5/bsoc_gem5_system.py").read_text()
    ast.parse(gem5_code)
    assert "NativeSocDevice" in gem5_code
    assert "RiscvTimingSimpleCPU" in gem5_code

    cpp_code = (plan_dir / "generated_soc_top.cpp").read_text()
    assert "UspMmio32ProxyTlm" in cpp_code
    assert "VerilatorMmioWrapper<Vfft_128pt_rtl>" in cpp_code
    assert "VerilatorMmioWrapper<Vfir_decim8_rtl>" in cpp_code
    assert "VerilatorMmioWrapper<Vtuner_32ph_rtl>" in cpp_code
    assert "VerilatorMmioWrapper<Vhwacha_vec4_rtl>" in cpp_code


def test_composer_negative_validation_rejects_stream_cycle_bad_clock_and_overlap(
    tmp_path: Path,
) -> None:
    base = yaml.safe_load(BSOC_SPEC.read_text())

    # 1. Cyclic AXI4-Stream DAG must fail explicitly
    cyc = copy.deepcopy(base)
    cyc["streams"].append(
        {
            "name": "illegal_feedback_cycle",
            "from": "fft_128pt_0",
            "to": "tisar_adc_0",
            "width_bits": 32,
            "clock_domain": "dsp_clk",
        }
    )
    p1 = tmp_path / "cyclic_stream.yaml"
    p1.write_text(yaml.safe_dump(cyc))
    with pytest.raises(ContractError, match="E_COMPOSE_STREAM_CYCLE"):
        load_composed_spec(p1)

    # 2. Undeclared clock domain must fail
    bad_clk = copy.deepcopy(base)
    bad_clk["devices"][0]["clock_domain"] = "missing_clk"
    p2 = tmp_path / "bad_clk.yaml"
    p2.write_text(yaml.safe_dump(bad_clk))
    with pytest.raises(ContractError, match="undeclared fabric/clock"):
        load_composed_spec(p2)

    # 3. Overlapping device aperture must fail
    ov = copy.deepcopy(base)
    ov["devices"][1]["base"] = ov["devices"][0]["base"]
    p3 = tmp_path / "overlap.yaml"
    p3.write_text(yaml.safe_dump(ov))
    with pytest.raises(ContractError, match="overlapping address windows"):
        load_composed_spec(p3)


def test_mcp_server_exposes_and_executes_compose_audit_and_plan(tmp_path: Path) -> None:
    server = EmulatorMcpServer(ROOT, allow_external_paths=True)
    tools = {t.name for t in server._tool_specs}
    assert {"emulator_compose_audit", "emulator_compose_plan", "emulator_compose_run"} <= tools

    audit_res = server.call_tool(
        "emulator_compose_audit", {"spec_path": "examples/BSoC/bsoc_composed.yaml"}
    )
    assert audit_res["structuredContent"]["design"] == "BSoC"
    assert audit_res["structuredContent"]["devices"] == 22

    out_dir = tmp_path / "mcp-bsoc-plan"
    plan_res = server.call_tool(
        "emulator_compose_plan",
        {"spec_path": "examples/BSoC/bsoc_composed.yaml", "out_dir": str(out_dir)},
    )
    assert plan_res["structuredContent"]["name"] == "BSoC"
    assert (out_dir / "binding-lock.json").is_file()


def test_bsoc_live_multiplatform_5rtl_live_plugin_qemu_and_calibration(tmp_path: Path) -> None:
    """Execute BSoC live: 5x Verilator RTL + live socket plugin + QEMU + calibration."""
    ready, missing = has_unified_toolchain()
    run_dir = tmp_path / "bsoc-run"
    if not ready:
        if os.environ.get("USP_REQUIRE_SYSTEMC") == "1":
            pytest.fail(f"USP_REQUIRE_SYSTEMC=1 requires unified toolchain for BSoC: {missing}")
        blocked = run_composed_soc(BSOC_SPEC, run_dir)
        assert blocked["status"] == "BLOCKED"
        assert (run_dir / "binding-lock.json").is_file()
        assert (run_dir / "generated_soc_top.cpp").is_file()
        return

    rep = run_composed_soc(BSOC_SPEC, run_dir, mode="auto", verify_qemu_crosscheck=True)
    assert rep["status"] == "PASSED", json.dumps(rep, indent=2)
    pos = rep["positive_run"]
    assert pos["returncode"] == 0
    for marker in load_composed_spec(BSOC_SPEC)["firmware"]["expected_markers"]:
        assert marker in pos["console_output"], f"Missing BSoC marker: {marker}"

    summ = pos["summary"]
    assert summ["cdc_crossings"] > 0
    assert summ["dma_bytes_transferred"] == 512
    assert summ["jtag_axi4_tx"] >= 2
    assert summ["axi4_stream_packets_total"] == 2432
    assert summ["axi4_stream_stages"] == [1024, 1024, 128, 128, 128]
    assert summ["l2_dma_snoops"] >= 128
    assert summ["fabric_hits"]["tilelink_xbar"] > 0
    assert summ["fabric_hits"]["axi4_ctrl_xbar"] > 0
    assert summ["fabric_hits"]["axi4_data_xbar"] > 0

    # Verify live out-of-process usp-mmio32-v1 Unix socket plugin ran and passed 100%
    assert rep["live_plugins"]["polyphase_12t_0"]["status"] == "PASSED"
    assert rep["live_plugins"]["polyphase_12t_0"]["tx_count"] > 128
    assert summ["device_backends"]["polyphase_12t_0"] == "usp-mmio32-v1-live-plugin"

    # Verify 100% functional coverage + negative failure + live QEMU crosscheck
    assert rep["coverage"]["coverage_percent"] == 100.0
    assert rep["coverage"]["status"] == "PASSED"
    assert rep["negative_run"]["expected_failure_detected"] is True
    if shutil.which("qemu-system-riscv64") is not None:
        assert rep["qemu_crosscheck"]["status"] == "PASSED"

    # Verify 5x Verilator RTL + Live Plugin vs Pure SystemC-TLM calibration parity
    if shutil.which("verilator") is not None:
        for rtl_dev in (
            "l2_cache_512k",
            "hwacha_vec_0",
            "tuner_32ph_0",
            "fir_decim8_0",
            "fft_128pt_0",
        ):
            assert summ["rtl_cycles"][rtl_dev] > 0, f"Zero RTL cycles for {rtl_dev}"
        assert summ["rtl_cycles"]["fft_128pt_0"] >= 704
        assert summ["rtl_cycles"]["fir_decim8_0"] >= 17000

        cal = calibrate_composed_soc(BSOC_SPEC, tmp_path / "bsoc-cal")
        assert cal["status"] == "PASSED", json.dumps(cal, indent=2)
        assert cal["functional_parity"] is True
        assert cal["calibration"]["within_budget"] is True


def test_bsoc_official_reports_artifacts_contract() -> None:
    """Verify official verbatim BSoC CI reports in examples/BSoC/reports/ are complete."""
    rep_dir = ROOT / "examples/BSoC/reports"
    console = (rep_dir / "bsoc-console.log").read_text(encoding="utf-8")
    sim_md = (rep_dir / "bsoc-simulation-report.md").read_text(encoding="utf-8")
    cal = json.loads((rep_dir / "bsoc-calibration-report.json").read_text(encoding="utf-8"))
    qemu_log = (rep_dir / "bsoc-qemu-crosscheck.log").read_text(encoding="utf-8")

    backend_log = (rep_dir / "bsoc-backend-traces.log").read_text(encoding="utf-8")

    for marker in load_composed_spec(BSOC_SPEC)["firmware"]["expected_markers"]:
        assert marker in console
    assert "peak_bin=112" in console
    assert "power[14..17]=[25, 25, 5618, 17]" in console
    assert "PASSED" in sim_md
    assert "100.0%" in sim_md
    assert "usp-mmio32-v1-live-plugin" in sim_md
    assert cal["status"] == "PASSED"
    assert cal["functional_parity"] is True
    assert cal["calibration"]["within_budget"] is True
    assert cal["live_plugins"]["polyphase_12t_0"]["status"] == "PASSED"
    assert cal["rtl_cycles"]["fft_128pt_0"] >= 704
    assert cal["rtl_cycles"]["fir_decim8_0"] >= 17000
    assert "BSOC_QEMU_CROSSCHECK_PASS" in qemu_log
    assert "verilator-fft_128pt_rtl.log" in backend_log
    assert '"cdc":true' in backend_log
    assert '"systemc_time_ps":7721000' in backend_log
    assert "$scope module composed_soc $end" in backend_log
