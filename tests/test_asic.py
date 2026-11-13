import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from socsim.asic import build_asic, require_inactive_container, validate_resume_inputs, verify_asic
from socsim.domain import ContractError
from socsim.ipblocks import verify_models
from socsim.ipxact import digest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("state", ["missing", "stopped", "running", "offline"])
def test_resume_requires_confirmed_container_shutdown(monkeypatch, state):
    name = "usp-asic-" + "a" * 32
    reply = SimpleNamespace(returncode=1, stdout="", stderr="error: no such object: " + name)
    if state == "stopped":
        reply = SimpleNamespace(returncode=0, stdout="false\n", stderr="")
    if state == "running":
        reply = SimpleNamespace(returncode=0, stdout="true\n", stderr="")
    if state == "offline":
        reply.stderr = "Cannot connect: no such file or directory"
    monkeypatch.setattr("socsim.asic.subprocess.run", lambda *a, **k: reply)
    if state in {"running", "offline"}:
        with pytest.raises(ContractError):
            require_inactive_container(["docker", "--name", name])
    else:
        require_inactive_container(["docker", "--name", name])


@pytest.mark.parametrize("bad", [0, True, float("inf")])
def test_invalid_asic_timeout_is_rejected_before_output(tmp_path, bad):
    with pytest.raises(ContractError, match="timeout"):
        build_asic(tmp_path / "missing-tools", tmp_path / "out", timeout=bad)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("changed", ["rtl", "sdc", "config", "image"])
def test_asic_resume_cannot_reuse_different_inputs_or_tools(tmp_path, changed):
    out = tmp_path / "run"
    out.mkdir()
    (out / "flow").mkdir()
    rtl = tmp_path / "cpu.sv"
    sdc = tmp_path / "cpu.sdc"
    rtl.write_text("module cpu; endmodule\n")
    sdc.write_text("create_clock -period 10\n")
    shutil.copy2(rtl, out / "input.sv")
    shutil.copy2(sdc, out / "constraints.sdc")
    config = "export DESIGN_NAME = cpu\n"
    (out / "config.mk").write_text(config)
    image = "sha256:" + "a" * 64
    (out / "flow.command.json").write_text(
        json.dumps(
            {
                "cwd": str(out),
                "argv": [
                    "docker",
                    "run",
                    "--name",
                    "usp-asic-" + "0" * 32,
                    "--mount",
                    f"type=bind,src={out},dst=/work",
                    image,
                    "/bin/bash",
                    "-c",
                    "original native flow",
                ],
            }
        )
    )
    validate_resume_inputs(out, rtl, sdc, config, image)
    if changed == "rtl":
        rtl.write_text("module other; endmodule\n")
    if changed == "sdc":
        sdc.write_text("create_clock -period 20\n")
    if changed == "config":
        (out / "config.mk").write_text(config + "changed\n")
    if changed == "image":
        image = "sha256:" + "b" * 64
    with pytest.raises(ContractError, match="resume"):
        validate_resume_inputs(out, rtl, sdc, config, image)


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
