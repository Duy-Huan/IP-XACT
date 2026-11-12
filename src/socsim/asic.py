"""Reference Nangate45 RTL-to-layout flow using pinned, local OpenROAD tools."""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from .domain import ContractError
from .ipblocks import verify_models
from .ipxact import digest
from .pipeline import execute
from .project import inside
from .runner import write_json

SOURCE_IMAGE = (
    "registry-1.docker.io/openroad/orfs@sha256:"
    "688272c2cca337c25e98437ae0e2dc919f1bb659d8e488427c52d97ae903e507"
)
DOCKER = ["docker", "--host=unix:///var/run/docker.sock"]
RESOURCES = Path(__file__).parent / "resources/asic"


def docker_environment():
    selectors = {
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    }
    return {k: v for k, v in os.environ.items() if k not in selectors}


def verify_toolchain(path):
    record = json.loads((path / "openroad-tools.json").read_text())
    if record["source_image"] != SOURCE_IMAGE:
        raise ContractError("unsupported OpenROAD toolchain image provenance")
    identity = record["flat_image_id"]
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", identity):
        raise ContractError("invalid immutable local image identity")
    actual = subprocess.check_output(
        [*DOCKER, "image", "inspect", "--format", "{{.Id}}", identity],
        text=True,
        env=docker_environment(),
    ).strip()
    if identity != actual:
        raise ContractError("OpenROAD image identity mismatch")
    return record


def run_container(image, mounts, command, cwd, log, timeout=1800):
    name = "usp-asic-" + uuid.uuid4().hex
    args = [
        *DOCKER,
        "run",
        "--rm",
        "--name",
        name,
        "--network=none",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--env",
        "PATH=/opt/orfs-python/bin:/usr/local/bin/wrapped-cc:/usr/local/bin:/usr/bin:/bin",
    ]
    for source, destination in mounts:
        # Docker --mount treats commas as separators; reject rather than reinterpret paths.
        if "," in str(source):
            raise ContractError("Docker mount paths containing commas are unsupported")
        args += ["--mount", f"type=bind,src={source},dst={destination}"]
    args += [image, "/bin/bash", "-c", command]
    try:
        execute(args, cwd, log, timeout, docker_environment())
    finally:
        subprocess.run(
            [*DOCKER, "rm", "--force", name],
            env=docker_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def build_asic(toolchain, out, rtl=None, top=None, sdc=None, models=None):
    model = None
    if models is not None:
        if any(v is not None for v in (rtl, top, sdc)):
            raise ContractError("select either verified GCD models or explicit RTL/top/SDC")
        model = verify_models(models)
        rtl = models / "generator/rtl/GCDMMIOChiselModule.sv"
        top = "GCDMMIOChiselModule"
        sdc = RESOURCES / "gcd.sdc"
    if (
        rtl is None
        or sdc is None
        or top is None
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", top)
    ):
        raise ContractError("ASIC flow requires RTL, safe top name and explicit SDC")
    if not rtl.is_file() or not sdc.is_file():
        raise ContractError("missing RTL or constraints input")
    tools = verify_toolchain(toolchain)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    shutil.copy2(rtl, out / "input.sv")
    shutil.copy2(sdc, out / "constraints.sdc")
    image = tools["flat_image_id"]
    config = f"""export DESIGN_NAME = {top}
export PLATFORM = nangate45
export VERILOG_FILES = /work/input.sv
export SDC_FILE = /work/constraints.sdc
export CORE_UTILIZATION = 40
export PLACE_DENSITY_LB_ADDON = 0.20
export TNS_END_PERCENT = 100
export GPL_PLACE_IOS = 1
export ADDER_MAP_FILE :=
export PDN_TCL = $(DESIGN_HOME)/nangate45/gcd/grid_strategy-M1-M4-M7.tcl
export NUM_CORES = 2
"""
    (out / "config.mk").write_text(config)
    run_container(
        image,
        [(out, "/work")],
        "set -e; cp -a /OpenROAD-flow-scripts/flow /work/flow; "
        "ln -s /OpenROAD-flow-scripts/tools /work/tools; "
        "source /OpenROAD-flow-scripts/env.sh; export FLOW_HOME=/work/flow; "
        "openroad -version; yosys -V; cd /work/flow; "
        f"mkdir -p results/nangate45/{top}/base logs/nangate45/{top}/base "
        f"reports/nangate45/{top}/base objects/nangate45/{top}/base; "
        "make DESIGN_CONFIG=/work/config.mk -j2",
        out,
        out / "flow.log",
        3600,
    )
    results = out / "flow/results/nangate45" / top / "base"
    gds = results / "6_final.gds"
    if not gds.is_file() or gds.stat().st_size < 100:
        raise ContractError("OpenROAD flow did not produce a final GDS")
    artifacts = {
        p.relative_to(out).as_posix(): digest(p) for p in sorted(results.rglob("*")) if p.is_file()
    }
    artifacts.update(
        {name: digest(out / name) for name in ("input.sv", "constraints.sdc", "config.mk")}
    )
    metrics = {}
    for path in sorted((out / "flow/logs/nangate45" / top / "base").glob("*.json")):
        metrics[path.name] = json.loads(path.read_text())
    report = {
        "status": "IMPLEMENTED_REFERENCE_LAYOUT",
        "scope": "provided RTL top on reference Nangate45",
        "top": top,
        "pdk": "nangate45",
        "source_image": SOURCE_IMAGE,
        "flat_image_id": image,
        "rtl_sha256": digest(out / "input.sv"),
        "sdc_sha256": digest(out / "constraints.sdc"),
        "gds_sha256": digest(gds),
        "artifacts": artifacts,
        "metrics": metrics,
        "source_model_binary_sha256": model["binary_sha256"] if model else None,
        "full_system_complete": False,
        "foundry_signoff": False,
        "limitations": [
            "Reference library only; not production signoff or fabricated silicon",
            "Generated layout does not prove full SoC, NoC, PHY or security integration",
            "Inspect timing, routing/DRC and LVS reports before any release",
        ],
    }
    write_json(out / "report.json", report)
    (out / "report.md").write_text(
        "# Reference RTL to layout\n\nTop: "
        + top
        + "\n\nStatus: "
        + report["status"]
        + "\n\nGDS SHA256: "
        + report["gds_sha256"]
        + "\n\n"
        + "\n".join("- " + item for item in report["limitations"])
        + "\n"
    )
    verify_asic(out)
    return report


def verify_asic(out):
    out = out.resolve()
    report = json.loads((out / "report.json").read_text())
    if (
        report["source_image"] != SOURCE_IMAGE
        or report["status"] != "IMPLEMENTED_REFERENCE_LAYOUT"
        or report["full_system_complete"] is not False
        or report["foundry_signoff"] is not False
    ):
        raise ContractError("invalid ASIC scope/provenance claim")
    expected_gds = f"flow/results/nangate45/{report['top']}/base/6_final.gds"
    files = report["artifacts"]
    if not {"input.sv", "constraints.sdc", "config.mk", expected_gds}.issubset(files):
        raise ContractError("incomplete ASIC artifacts")
    for relative, sha in files.items():
        if digest(inside(out, relative)) != sha:
            raise ContractError("modified ASIC artifact")
    if files[expected_gds] != report["gds_sha256"] or files["input.sv"] != report["rtl_sha256"]:
        raise ContractError("inconsistent ASIC artifact identity")
    if not all(reference_checks(report).values()):
        raise ContractError("reference routing/timing acceptance failed")
    return report


def reference_checks(report):
    metrics = report["metrics"]
    final = metrics.get("6_report.json", {})
    route = metrics.get("5_2_route.json", {})
    checks = {}
    for kind in ("setup", "hold"):
        slack = final.get(f"finish__timing__{kind}__ws")
        tns = final.get(f"finish__timing__{kind}__tns")
        checks[kind] = (
            type(slack) in {int, float}
            and math.isfinite(slack)
            and slack >= 0
            and type(tns) in {int, float}
            and math.isfinite(tns)
            and tns == 0
        )
    checks["detailed_route_drc"] = route.get("detailedroute__route__drc_errors") == 0
    return checks


def snapshot_asic(build, out, models):
    report = verify_asic(build)
    model = verify_models(models)
    if report["source_model_binary_sha256"] != model["binary_sha256"] or report[
        "rtl_sha256"
    ] != digest(models / "generator/rtl/GCDMMIOChiselModule.sv"):
        raise ContractError("ASIC layout differs from the verified source-model bundle")
    out.mkdir(parents=True, exist_ok=False)
    for relative in report["artifacts"]:
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(inside(build.resolve(), relative), target)
    shutil.copy2(build / "report.json", out / "physical-report.json")
    shutil.copy2(build / "report.md", out / "physical-report.md")
    shutil.copy2(build / "flow.log", out / "flow.log")
    result = {
        "status": "PASSED",
        "scope": "verified reference GCD leaf layout; not full SoC",
        "gds_sha256": report["gds_sha256"],
        "foundry_signoff": False,
        "reference_checks": reference_checks(report),
    }
    write_json(out / "report.json", result)
    return result


def main(argv):
    parser = argparse.ArgumentParser(prog="usp asic")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    for arg in ("toolchain", "out"):
        build.add_argument("--" + arg, type=Path, required=True)
    for arg in ("rtl", "sdc", "models"):
        build.add_argument("--" + arg, type=Path)
    build.add_argument("--top")
    sub.add_parser("verify").add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        result = (
            verify_asic(args.run_dir)
            if args.command == "verify"
            else build_asic(args.toolchain, args.out, args.rtl, args.top, args.sdc, args.models)
        )
        print(json.dumps(result, indent=2))
        return 0
    except (ContractError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "ERROR", "code": "E_ASIC", "message": str(exc)}))
        return 2
