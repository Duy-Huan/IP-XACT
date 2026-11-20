"""Tests for heterogeneous multi-platform SoC designs, SystemC MAC NPU, and blocker checks."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from socsim.domain import ContractError
from socsim.multiplatform_qa import _has_systemc_toolchain, run_multiplatform_qa
from socsim.native_flow import preflight_flow
from socsim.native_package import (
    build_package,
    generate_package_header,
    package_capabilities,
    read_package,
)
from socsim.qa_runner import run_qa, verify_scorecard
from socsim.sdk import EmulatorNode

ROOT = Path(__file__).resolve().parents[1]


def test_design1_chipyard_rocket_opentitan_qemu_io(tmp_path):
    spec = ROOT / "specs/multiplatform/design1_rocket_chipyard_ot_qemu.yaml"
    flow = ROOT / "specs/multiplatform/design1_flow.yaml"
    pf = preflight_flow(flow)
    assert pf["status"] == "READY"
    with EmulatorNode("mp_design1") as node:
        art = node.generate_soc(spec, tmp_path / "gen", engine="both")
    assert art.success
    assert art.design == "MP_Design1_Rocket_OT_QEMU"


def test_design2_gem5_noc_smp_hetero(tmp_path):
    spec = ROOT / "specs/multiplatform/design2_gem5_noc_smp_hetero.yaml"
    flow = ROOT / "specs/multiplatform/design2_flow.yaml"
    pf = preflight_flow(flow)
    assert pf["status"] == "READY"
    with EmulatorNode("mp_design2") as node:
        art = node.generate_soc(spec, tmp_path / "gen", engine="both")
    assert art.success
    assert art.design == "MP_Design2_Gem5NoC_SMP"


def test_design3_mac_npu_package_contract_and_tamper(tmp_path):
    pkg_path = ROOT / "specs/multiplatform/packages/mac_npu/package.yaml"
    flow = ROOT / "specs/multiplatform/design3_flow.yaml"
    pkg, hashes = read_package(pkg_path)
    assert pkg["id"] == "user.mac_npu"
    assert len(hashes) == 4
    caps = package_capabilities(pkg)
    assert caps["supports_reset"] and caps["supports_irq"] and caps["supports_bus_error"]
    assert caps["register_count"] == 5
    hdr = generate_package_header(pkg)
    assert "USP_USER_MAC_NPU_REG_CTRL_CMD_OFFSET 0x000U" in hdr
    assert "USP_USER_MAC_NPU_REG_ACC_RESULT_OFFSET 0x00CU /* read-only */" in hdr
    pf = preflight_flow(flow)
    assert pf["status"] == "READY"

    tampered = tmp_path / "mac_npu"
    shutil.copytree(pkg_path.parent, tampered)
    (tampered / "selftest.c").write_text((tampered / "selftest.c").read_text() + "\n/* x */\n")
    with pytest.raises(ContractError, match="modified package input"):
        read_package(tampered / "package.yaml")


def test_multiplatform_qa_matrix_and_scorecard(tmp_path):
    out = tmp_path / "qa-mp"
    report = run_qa(ROOT, out, suite="multiplatform")
    assert report["status"] == "PASSED"
    mp = report["gates"]["multiplatform_soc"]
    assert mp["status"] == "PASSED"
    for key in (
        "design1_rocket_chipyard_ot_qemu",
        "design2_gem5_noc_smp_hetero",
        "design3_mac_npu_hetero",
        "design4_full_gemmini_ot_pmp_blockers",
    ):
        assert mp["designs"][key]["status"] == "PASSED"
    assert (
        mp["designs"]["design4_full_gemmini_ot_pmp_blockers"]["architecture_verdict"]
        == "BLOCKED_AS_EXPECTED_NO_FAKE_FALLBACK"
    )
    verified = verify_scorecard(out)
    assert verified["status"] == "PASSED"


def test_design3_real_systemc_mac_npu_build_and_conformance(tmp_path):
    """Compile and run the real SystemC user.mac_npu binary over usp-mmio32-v1 when available."""
    if not _has_systemc_toolchain():
        if os.environ.get("SOCSIM_REQUIRE_INTEGRATION") == "1":
            pytest.fail("SOCSIM_REQUIRE_INTEGRATION=1 requires libsystemc-dev and c++")
        pytest.skip("libsystemc-dev / pkg-config systemc not installed on this host")

    pkg_path = ROOT / "specs/multiplatform/packages/mac_npu/package.yaml"
    build_out = tmp_path / "mac-npu-build"
    built = build_package(pkg_path, build_out)
    assert built["package_id"] == "user.mac_npu"
    import json

    qual = json.loads((build_out / "qualification.json").read_text())
    assert qual["status"] == "PASSED"
    assert all(qual["checks"].values())
    assert len(qual["actual"]) == 17
    assert (build_out / "model").is_file()
    assert (build_out / "conformance.trace.jsonl").is_file()

    # Also verify direct multiplatform QA runner executes SystemC mode in this environment
    with EmulatorNode("mp_systemc") as node:
        res = run_multiplatform_qa(ROOT, tmp_path / "mp-live", node)
    assert res["status"] == "PASSED"
    assert res["systemc_toolchain_available"] is True
    d3 = res["designs"]["design3_mac_npu_hetero"]
    assert d3["systemc_execution"]["mode"] == "real-systemc-compiled-and-executed"
    assert d3["systemc_execution"]["conformance_vectors_passed"] == 17


def test_design3_driver_compiles_with_riscv_gcc_when_installed(tmp_path):
    """Compile selftest.c with riscv64-unknown-elf-gcc when installed (e.g. Podman SDK)."""
    gcc = shutil.which("riscv64-unknown-elf-gcc")
    if gcc is None:
        if os.environ.get("SOCSIM_REQUIRE_INTEGRATION") == "1":
            pytest.fail("SOCSIM_REQUIRE_INTEGRATION=1 requires riscv64-unknown-elf-gcc")
        pytest.skip("riscv64-unknown-elf-gcc not installed on this host")

    inc = tmp_path / "inc"
    inc.mkdir()
    (inc / "soc.h").write_text(
        "#define USP_CONSOLE_BASE 0x11000000UL\n"
        "#define USP_PLIC_BASE 0x0C000000UL\n"
        "#define USP_FINISHER_BASE 0x00100000UL\n"
    )
    (inc / "driver_bindings.h").write_text(
        "#define USP_DRIVER_COUNT 2U\n"
        "static const uint64_t usp_driver_bases[2] = {0x191000000UL, 0x191003000UL};\n"
        "static const unsigned usp_driver_irqs[2] = {20U, 21U};\n"
    )
    obj = tmp_path / "selftest.o"
    proc = subprocess.run(
        [
            gcc,
            "-march=rv64imac",
            "-mabi=lp64",
            "-mcmodel=medany",
            "-ffreestanding",
            "-Wall",
            "-Wextra",
            "-Werror",
            f"-I{inc}",
            "-c",
            str(ROOT / "specs/multiplatform/packages/mac_npu/selftest.c"),
            "-o",
            str(obj),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert obj.is_file()
