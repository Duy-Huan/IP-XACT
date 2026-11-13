import json
import os
from pathlib import Path

import pytest

from socsim import gem5 as gem5_adapter
from socsim.domain import ContractError
from socsim.gem5 import run_gem5, verify_gem5_configuration
from socsim.ipxact import digest
from socsim.pipeline import run_custom
from socsim.project import load_project


@pytest.mark.parametrize("case", ["flag", "kconfig", "executable_info"])
def test_configuration_provenance_rejects_semantic_mismatch(tmp_path, case):
    config = tmp_path / "gem5.config"
    info = tmp_path / "build-info.log"
    config.write_text("# USE_SYSTEMC is not set\nUSE_RISCV_ISA=y\n")
    info.write_text("    USE_SYSTEMC = False\n")
    c = {
        "embedded_systemc": False,
        "adapter_sha256": digest(Path(gem5_adapter.__file__)),
        "kconfig_sha256": digest(config),
        "build_info_sha256": digest(info),
    }
    record = {"configuration": c}
    verify_gem5_configuration(tmp_path, record)
    if case == "flag":
        c["embedded_systemc"] = True
    if case == "kconfig":
        config.write_text("USE_SYSTEMC=y\n")
        c["kconfig_sha256"] = digest(config)
    if case == "executable_info":
        info.write_text("    USE_SYSTEMC = True\n")
        c["build_info_sha256"] = digest(info)
    with pytest.raises(ContractError, match="configuration"):
        verify_gem5_configuration(tmp_path, record)


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_same_elf_on_qemu_linux_and_gem5_with_real_stats(tmp_path):
    generated = os.environ.get("USP_GENERATED_DIR")
    build = os.environ.get("USP_GEM5_BUILD_DIR")
    if not generated or not build:
        if os.environ.get("USP_REQUIRE_GEM5") == "1":
            pytest.fail("shared Linux workload and gem5 build are required")
        pytest.skip("gem5 source-build test is a separate expensive gate")
    project = load_project(ROOT / "examples/rv64-minimal")
    qemu = run_custom(project, Path(generated).resolve(), tmp_path / "qemu-linux", osname="linux")
    assert qemu["status"] == "PASSED"
    report = run_gem5(
        load_project(ROOT / "examples/rv64-minimal"),
        Path(generated).resolve(),
        Path(build).resolve(),
        tmp_path / "gem5",
    )
    assert report["status"] == "PASSED"
    assert report["workload_sha256"] == report["qemu_shared_workload_sha256"]
    assert report["workload_sha256"] == qemu["software_source"]["workload_sha256"]
    assert report["statistics"]["simInsts"] > 0
    assert report["statistics"]["simTicks"] > 0
    assert "uart" in report["unmodeled_instances"]
    assert report["fidelity"] == "timing-uncalibrated"
    assert (
        json.loads((tmp_path / "gem5/report.json").read_text())["profile"] == "architecture-gem5-se"
    )
