import json
import os
import shutil
from pathlib import Path

import pytest

from socsim.asic import build_asic, verify_asic
from socsim.domain import ContractError
from socsim.ipblocks import verify_models
from socsim.ipxact import digest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("top", ["bad;top", "$(touch owned)", "../other", ""])
def test_invalid_rtl_top_rejected_before_docker_or_output_mutation(tmp_path, top):
    rtl, sdc = tmp_path / "input.sv", tmp_path / "input.sdc"
    rtl.write_text("module example(input clock); endmodule\n")
    sdc.write_text("create_clock -period 10 [get_ports clock]\n")
    with pytest.raises(ContractError, match="safe top"):
        build_asic(tmp_path / "missing-tools", tmp_path / "out", rtl, top, sdc)
    assert not (tmp_path / "out").exists()
    assert not (tmp_path / "owned").exists()


def test_actual_reference_gds_and_evidence_integrity(tmp_path):
    if os.environ.get("USP_REQUIRE_ASIC") != "1":
        pytest.skip("explicit actual OpenROAD reference layout required")
    build = Path(os.environ.get("USP_ASIC_BUILD_DIR", str(ROOT / "build/asic-gcd-003")))
    report = verify_asic(build)
    models = Path(os.environ.get("USP_IP_MODELS_DIR", str(ROOT / "build/source-ip-models")))
    model = verify_models(models)
    assert model["binary_sha256"] == report["source_model_binary_sha256"]
    assert digest(models / "generator/rtl/GCDMMIOChiselModule.sv") == report["rtl_sha256"]
    assert report["metrics"]
    assert not report["full_system_complete"] and not report["foundry_signoff"]
    out = tmp_path / "copy"
    out.mkdir()
    for relative in report["artifacts"]:
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(build / relative, target)
    shutil.copy2(build / "report.json", out / "report.json")
    verify_asic(out)
    gds = next(p for p in out.rglob("6_final.gds"))
    gds.write_bytes(gds.read_bytes() + b"modified")
    with pytest.raises(ContractError, match="modified ASIC"):
        verify_asic(out)
    shutil.copy2(build / gds.relative_to(out), gds)
    report["metrics"]["6_report.json"]["finish__timing__setup__ws"] = -1
    (out / "report.json").write_text(json.dumps(report))
    with pytest.raises(ContractError, match="routing/timing"):
        verify_asic(out)
    report["foundry_signoff"] = True
    (out / "report.json").write_text(json.dumps(report))
    with pytest.raises(ContractError, match="scope/provenance"):
        verify_asic(out)
