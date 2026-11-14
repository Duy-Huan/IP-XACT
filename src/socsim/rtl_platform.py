"""Unified platform entry for already source-verified RV32 kernel adapters."""

import hashlib
import json
from pathlib import Path

from . import rtl_os, rtl_zephyr, ucos
from .cpu_platform import cpu_selection, platform_plan
from .domain import ContractError
from .ipxact import digest
from .project import inside
from .rtl_cpu import run_platform as run_baremetal
from .rtl_cpu import target_ir, verify_run
from .runner import write_json

ADAPTERS = {"freertos": rtl_os, "ucos2": ucos, "ucos3": ucos, "zephyr": rtl_zephyr}


def validate_builds(project, config):
    for layer, directory in config["software_builds"].items():
        record = ADAPTERS[layer].verify_build(project, Path(directory))
        if record.get("case") != "smoke":
            raise ContractError("platform acceptance requires positive smoke kernel firmware")
        if layer in {"ucos2", "ucos3"} and record["kernel"] != layer:
            raise ContractError("selected µC/OS kernel differs from actual software build")


def run(project, config, generated, models, bridge, cpu, out):
    selection = cpu_selection(config, config["engine"])
    if cpu is None:
        raise ContractError("real --rtl-cpu-build required; no ISS substitution")
    validate_builds(project, config)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "plan.json", platform_plan(project, config))
    write_json(out / "soc-ir.json", target_ir(project, Path(selection["rtl_manifest"])).to_dict())
    results = {}
    for layer in config["workloads"]:
        if layer == "baremetal":
            sub = dict(config, workloads=["baremetal"], software_builds={})
            if "artifacts" in sub:
                sub["artifacts"] = {k: v for k, v in sub["artifacts"].items() if k == layer}
            row = run_baremetal(project, sub, generated, models, bridge, cpu, out / layer)
            stage = row["results"]["baremetal"]
        else:
            adapter = ADAPTERS[layer]
            row = adapter.run(
                project,
                generated,
                Path(config["software_builds"][layer]),
                Path(selection["rtl_manifest"]),
                cpu,
                models,
                config["engine"],
                out / layer,
                bridge,
            )
            native = verify_run(out / layer)
            stage = dict(status=row["status"], performance=native["performance"])
        results[layer] = stage
    r = dict(
        schema_version="1",
        profile="rtl-software-platform-v1",
        status="PASSED" if all(s["status"] == "PASSED" for s in results.values()) else "FAILED",
        cpu=selection,
        results=results,
        full_architecture_complete=False,
        scope="Explicit RV32 M-mode kernel/BSP profiles; no Linux/MMU or automatic RTL conversion",
        files={p.relative_to(out).as_posix(): digest(p) for p in out.rglob("*") if p.is_file()},
    )
    write_json(out / "report.json", r)
    (out / "report.md").write_text(
        "# RTL software platform\n\n"
        + r["status"]
        + "\n\n"
        + "\n".join(f"- {k}: {v['status']}" for k, v in results.items())
        + "\n"
    )
    verify(out)
    return r


def verify(out):
    from .rtl_cpu import verify_platform as verify_baremetal

    r = json.loads((out / "report.json").read_text())
    if (
        r["profile"] != "rtl-software-platform-v1"
        or r["schema_version"] != "1"
        or r["full_architecture_complete"] is not False
        or not {"plan.json", "soc-ir.json"} <= r["files"].keys()
    ):
        raise ContractError("invalid RTL software platform scope/inventory")
    for name, sha in r["files"].items():
        if digest(inside(out, name)) != sha:
            raise ContractError("modified RTL software platform artifact")
    plan = json.loads((out / "plan.json").read_text())
    config = plan["requested_configuration"]
    selected = cpu_selection(config, config["engine"])
    sha = hashlib.sha256(
        json.dumps(json.loads((out / "soc-ir.json").read_text()), sort_keys=True).encode()
    ).hexdigest()
    if (
        selected != r["cpu"]
        or selected != plan["cpu"]
        or plan["ir_sha256"] != sha
        or plan["ownership"]["instructions"] != "systemc-verilator-cpu"
        or set(r["results"]) != set(config["workloads"])
    ):
        raise ContractError("mixed RTL software platform CPU/IR/workloads")
    results = {}
    for layer in config["workloads"]:
        if layer == "baremetal":
            row = verify_baremetal(out / layer)
            results[layer] = row["results"]["baremetal"]
            native = verify_run(out / layer / "baremetal")
        else:
            row = ADAPTERS[layer].verify(out / layer)
            native = verify_run(out / layer)
            if layer in {"ucos2", "ucos3"} and row["os_build"]["kernel"] != layer:
                raise ContractError("mixed µC/OS kernel software identity")
            results[layer] = dict(status=row["status"], performance=native["performance"])
        source_ids = dict(json.loads((out / "soc-ir.json").read_text())["sources"])
        provider = "chipyard-ibex" if selected["provider"] == "chipyard" else "custom-rtl"
        if (
            native["base_io_ir_sha256"] != plan["base_io_ir_sha256"]
            or native["engine"] != selected["engine"]
            or native["cpu_model"] != selected["model"]
            or native["cpu_provider"] != provider
            or native["clock_hz"] != selected["clock_hz"]
            or native["cpu_build"]["manifest_sha256"] != source_ids["rtl-cpu-manifest"]
        ):
            raise ContractError("mixed RTL software stage engine/target IR")
    if r["results"] != results or r["status"] != (
        "PASSED" if all(s["status"] == "PASSED" for s in results.values()) else "FAILED"
    ):
        raise ContractError("invalid RTL software platform acceptance")
    return r
