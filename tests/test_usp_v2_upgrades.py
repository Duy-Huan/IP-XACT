"""Verification tests for the 3 core USP v2 multi-platform upgrades:
1. Real QEMU (`qemu-system-riscv64` TCG/KVM) direct CPU Master over VirtIO-MMIO + mmap RAM
2. `usp-bus-v2` plugin wire protocol (reverse bus-master DMA, 64-bit bursts, quantum time sync,
   and wire-level + runtime backward compatibility with `usp-mmio32-v1`)
3. Automatic pin-level Verilator RTL adapters for Chipyard AXI4 Full, AMBA AXI4-Lite, and
   OpenTitan TL-UL (zero manual MMIO32 wrapping).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

from socsim.domain import ContractError
from socsim.multiplatform_composer import (
    _resolve_qemu_accel,
    audit_composed_soc,
    plan_composed_soc,
    run_composed_soc,
)
from socsim.native_package import (
    build_package,
    generate_package_header,
    package_capabilities,
    read_package,
)
from socsim.rtl_pin_adapter import emit_cpp_pin_traits, introspect_sv_rtl_interface
from socsim.unified_systemc_soc import has_unified_toolchain

ROOT = Path(__file__).resolve().parents[1]
NEXTGEN_DIR = ROOT / "examples" / "UspNextGen"
NEXTGEN_SPEC = NEXTGEN_DIR / "usp_nextgen_soc.yaml"
DMA_V2_PKG = NEXTGEN_DIR / "packages" / "dma_burst_v2" / "package.yaml"
BSOC_SPEC = ROOT / "examples" / "BSoC" / "bsoc_composed.yaml"
REQUIRE_SYSTEMC = os.environ.get("USP_REQUIRE_SYSTEMC") == "1"


def _has_systemc_cxx() -> bool:
    if shutil.which("c++") is None or shutil.which("pkg-config") is None:
        return False
    return subprocess.run(["pkg-config", "--exists", "systemc"], check=False).returncode == 0


def _full_stack_status() -> tuple[bool, list[str]]:
    ready, missing = has_unified_toolchain(require_verilator=True)
    if shutil.which("qemu-system-riscv64") is None:
        ready = False
        missing = [*missing, "qemu-system-riscv64"]
    return ready, missing


# ---------------------------------------------------------------------------
# Upgrade 3: Automatic Pin-Level SV RTL Introspection & Adapter Codegen
# ---------------------------------------------------------------------------


def test_sv_port_introspection_chipyard_axi4_full() -> None:
    sv = NEXTGEN_DIR / "rtl" / "chipyard_gcd_axi4_rtl.sv"
    meta = introspect_sv_rtl_interface(sv, "chipyard_gcd_axi4_rtl", requested_protocol="auto")
    assert meta["protocol"] == "axi4"
    assert meta["data_bits"] == 64
    assert meta["reset_active_low"] is False
    assert meta["ports"]["clk"] == "clock"
    assert meta["ports"]["rst"] == "reset"
    assert meta["ports"]["aw_valid"] == "io_axi_aw_valid"
    assert meta["ports"]["aw_len"] == "io_axi_aw_bits_len"
    assert meta["ports"]["r_last"] == "io_axi_r_bits_last"
    assert meta["ports"]["irq"] == "io_irq"
    traits = "\n".join(emit_cpp_pin_traits("Traits_chipyard_gcd_0", meta))
    assert "struct Traits_chipyard_gcd_0" in traits
    assert "io_axi_aw_bits_addr" in traits
    assert "static constexpr uint32_t kDataBytes = 8u;" in traits


def test_sv_port_introspection_amba_axi4_lite() -> None:
    sv = NEXTGEN_DIR / "rtl" / "axi4_lite_crc32_rtl.sv"
    meta = introspect_sv_rtl_interface(sv, "axi4_lite_crc32_rtl", requested_protocol="auto")
    assert meta["protocol"] == "axi4-lite"
    assert meta["data_bits"] == 32
    assert meta["reset_active_low"] is True
    assert meta["ports"]["clk"] == "aclk"
    assert meta["ports"]["rst"] == "aresetn"
    assert meta["ports"]["aw_addr"] == "s_axi_awaddr"
    assert meta["ports"]["r_resp"] == "s_axi_rresp"
    assert meta["ports"]["irq"] == "irq_o"
    traits = "\n".join(emit_cpp_pin_traits("Traits_axi4_lite_crc32_0", meta))
    assert "struct Traits_axi4_lite_crc32_0" in traits
    assert "s_axi_bvalid" in traits


def test_sv_port_introspection_opentitan_tlul() -> None:
    sv = NEXTGEN_DIR / "rtl" / "opentitan_gpio_tlul_rtl.sv"
    meta = introspect_sv_rtl_interface(sv, "opentitan_gpio_tlul_rtl", requested_protocol="auto")
    assert meta["protocol"] == "tl-ul"
    assert meta["data_bits"] == 32
    assert meta["reset_active_low"] is True
    assert meta["ports"]["a_valid"] == "tl_a_valid_i"
    assert meta["ports"]["a_ready"] == "tl_a_ready_o"
    assert meta["ports"]["d_valid"] == "tl_d_valid_o"
    assert meta["ports"]["d_ready"] == "tl_d_ready_i"
    assert meta["ports"]["d_error"] == "tl_d_error_o"
    assert meta["ports"]["irq"] == "intr_gpio_o"
    traits = "\n".join(emit_cpp_pin_traits("Traits_opentitan_gpio_0", meta))
    assert "struct Traits_opentitan_gpio_0" in traits
    assert "tl_d_error_o" in traits


def test_sv_port_introspection_legacy_mmio32_backward_compat() -> None:
    sv = ROOT / "examples" / "BSoC" / "rtl" / "fft_128pt_rtl.sv"
    meta = introspect_sv_rtl_interface(sv, "fft_128pt_rtl", requested_protocol="auto")
    assert meta["protocol"] == "mmio32"
    assert meta["data_bits"] == 32


def test_sv_port_introspection_rejects_incomplete_axi4_lite(tmp_path: Path) -> None:
    bad_sv = tmp_path / "broken_axi.sv"
    bad_sv.write_text(
        "module broken_axi (\n"
        "  input logic aclk, input logic aresetn,\n"
        "  input logic s_axi_awvalid, output logic s_axi_awready,\n"
        "  input logic [11:0] s_axi_awaddr\n"
        ");\nendmodule\n"
    )
    with pytest.raises(ContractError, match="E_COMPOSE_RTL_PROTO"):
        introspect_sv_rtl_interface(bad_sv, "broken_axi", requested_protocol="axi4-lite")


# ---------------------------------------------------------------------------
# Upgrade 2: `usp-bus-v2` Package Schema, Capabilities & Live Qualification
# ---------------------------------------------------------------------------


def test_usp_bus_v2_package_schema_and_capabilities() -> None:
    pkg, hashes = read_package(DMA_V2_PKG)
    assert pkg["id"] == "usp.dma_burst_v2"
    assert "tlm-dma-burst-v2.cpp" in hashes
    caps = package_capabilities(pkg)
    assert caps["protocol"] == "usp-bus-v2"
    assert caps["access_bits"] == 64
    assert caps["supports_dma_master"] is True
    assert caps["supports_burst64"] is True
    assert caps["supports_quantum_sync"] is True
    assert caps["v1_backward_compat"] is True
    hdr = generate_package_header(pkg)
    assert "USP_USP_DMA_BURST_V2_REG_DMA_SRC_LO_OFFSET" in hdr


def test_usp_bus_v2_live_package_build_and_qualification(tmp_path: Path) -> None:
    import json

    if not _has_systemc_cxx():
        if REQUIRE_SYSTEMC:
            pytest.fail("SystemC / c++ missing under USP_REQUIRE_SYSTEMC=1")
        pytest.skip("SystemC / c++ unavailable on host")

    out_dir = tmp_path / "dma-v2-build"
    res = build_package(DMA_V2_PKG, out_dir)
    assert res["package_id"] == "usp.dma_burst_v2"
    assert res["binary_sha256"]
    qual = json.loads((out_dir / "qualification.json").read_text())
    assert qual["status"] == "PASSED"
    assert qual["protocol"] == "usp-bus-v2"
    assert qual["checks"]["v1_backward_compat_reset"] is True
    assert all(qual["checks"].values())
    dma = qual["dma_qualification"]
    assert dma["reads"] >= 1
    assert dma["writes"] >= 1
    assert dma["bytes"] >= 64


# ---------------------------------------------------------------------------
# Upgrade 1 + 2 + 3: UspNextGen Plan, Audit, Accel Policy & Live Execution
# ---------------------------------------------------------------------------


def test_usp_nextgen_plan_and_audit(tmp_path: Path) -> None:
    audit = audit_composed_soc(NEXTGEN_SPEC)
    assert audit["design"] == "UspNextGen"
    assert audit["cpu_master_engine"] == "qemu"
    assert audit["quantum_ns"] == 100_000
    assert audit["rtl_protocols"]["chipyard_gcd_0"] == "axi4"
    assert audit["rtl_protocols"]["axi4_lite_crc32_0"] == "axi4-lite"
    assert audit["rtl_protocols"]["opentitan_gpio_0"] == "tl-ul"
    assert audit["plugin_protocols"]["dma_burst_v2_0"] == "usp-bus-v2"
    assert audit["plugin_protocols"]["polyphase_v1_0"] == "usp-mmio32-v1"

    out = tmp_path / "nextgen-plan"
    ir = plan_composed_soc(NEXTGEN_SPEC, out)
    assert ir["devices"] == 12
    assert (out / "link_qemu.ld").is_file()
    cpp = (out / "generated_soc_top.cpp").read_text()
    for token in (
        "VerilatorAxi4FullAdapter<Vchipyard_gcd_axi4_rtl, Traits_chipyard_gcd_0>",
        "VerilatorAxi4LiteAdapter<Vaxi4_lite_crc32_rtl, Traits_axi4_lite_crc32_0>",
        "VerilatorTlulAdapter<Vopentitan_gpio_tlul_rtl, Traits_opentitan_gpio_0>",
        "usp::UspBusV2ProxyTlm dev_dma_burst_v2_0",
        "usp::UspMmio32ProxyTlm dev_polyphase_v1_0",
        "usp::QemuCpuMaster",
    ):
        assert token in cpp

    gem5_py = (out / "gem5" / "uspnextgen_gem5_system.py").read_text()
    assert '"protocol": "usp-bus-v2"' in gem5_py
    assert '"dma_enabled": True' in gem5_py
    assert "QUANTUM_SYNC_NS = 100000" in gem5_py


def test_qemu_accel_kvm_honest_error_when_unavailable() -> None:
    arch = platform.machine().lower()
    if arch == "riscv64" and os.access("/dev/kvm", os.R_OK | os.W_OK):
        assert _resolve_qemu_accel("kvm") == "kvm"
    else:
        with pytest.raises(ContractError, match="E_COMPOSE_QEMU_KVM"):
            _resolve_qemu_accel("kvm")
        assert _resolve_qemu_accel("auto") == "tcg"


def test_usp_nextgen_live_qemu_cpu_master_v2_and_pin_rtl(tmp_path: Path) -> None:
    ready, missing = _full_stack_status()
    out = tmp_path / "nextgen-live"
    if not ready:
        if REQUIRE_SYSTEMC:
            pytest.fail(f"USP_REQUIRE_SYSTEMC=1 requires full stack: {', '.join(missing)}")
        blocked = run_composed_soc(NEXTGEN_SPEC, out, mode="hybrid", verify_qemu_crosscheck=False)
        assert blocked["status"] == "BLOCKED"
        assert (out / "binding-lock.json").is_file()
        assert (out / "link_qemu.ld").is_file()
        return

    rep = run_composed_soc(NEXTGEN_SPEC, out, mode="hybrid", verify_qemu_crosscheck=False)
    assert rep["status"] == "PASSED", rep
    assert rep["cpu_master_engine"] == "qemu"
    assert rep["qemu_accel"] in {"tcg", "kvm"}
    assert rep["coverage"]["coverage_percent"] == 100.0
    assert rep["negative_run"]["expected_failure_detected"] is True

    summ = rep["positive_run"]["summary"]
    assert summ["cpu_master"] == "qemu"
    assert summ["instructions_executed"] >= 2_000_000
    assert summ["quantum_sync_barriers"] >= 2
    assert summ["burst64_transactions"] >= 4
    assert summ["burst64_bytes"] >= 256
    assert summ["reverse_dma_bytes"] >= 256
    assert summ["cdc_crossings"] > 0
    assert summ["l2_hits"] > 0
    assert summ["l2_misses"] > 0
    assert summ["l2_dma_snoops"] > 0

    # Pin-level Verilator RTL adapters exercised with real valid/ready handshakes
    hs = summ["bus_handshakes"]
    assert hs["chipyard_gcd_0"] > 0
    assert hs["axi4_lite_crc32_0"] > 0
    assert hs["opentitan_gpio_0"] > 0
    backends = summ["device_backends"]
    assert backends["chipyard_gcd_0"] == "verilator-axi4-full"
    assert backends["axi4_lite_crc32_0"] == "verilator-axi4-lite"
    assert backends["opentitan_gpio_0"] == "verilator-tl-ul"
    assert backends["dma_burst_v2_0"] == "usp-bus-v2-live-plugin"
    assert backends["polyphase_v1_0"] == "usp-mmio32-v1-live-plugin"


def test_bsoc_live_under_qemu_cpu_master(tmp_path: Path) -> None:
    ready, missing = _full_stack_status()
    out = tmp_path / "bsoc-qemu-live"
    if not ready:
        if REQUIRE_SYSTEMC:
            pytest.fail(f"USP_REQUIRE_SYSTEMC=1 requires full stack: {', '.join(missing)}")
        blocked = run_composed_soc(
            BSOC_SPEC, out, mode="hybrid", cpu_master="qemu", verify_qemu_crosscheck=False
        )
        assert blocked["status"] == "BLOCKED"
        return

    rep = run_composed_soc(
        BSOC_SPEC, out, mode="hybrid", cpu_master="qemu", verify_qemu_crosscheck=False
    )
    assert rep["status"] == "PASSED", rep
    assert rep["cpu_master_engine"] == "qemu"
    assert rep["coverage"]["coverage_percent"] == 100.0
    summ = rep["positive_run"]["summary"]
    assert summ["cpu_master"] == "qemu"
    assert summ["instructions_executed"] > 10_000
    assert summ["quantum_sync_barriers"] > 0
    assert summ["axi4_stream_packets_total"] > 0
