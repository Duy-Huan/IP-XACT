"""Complete architecture intake plus explicitly scoped executable bring-up stages."""

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path

from jsonschema import ValidationError, validate

from .domain import ContractError
from .gem5 import GEM5_SHA
from .hybrid import OPENTITAN_SHA
from .ipblocks import CHIPYARD_SHA, run_ipblocks
from .ipxact import digest
from .noc import run_noc, validate_noc
from .pipeline import resolve
from .project import inside, load_project, read_yaml
from .runner import write_json
from .synthesis import synthesize_gcd

FULL_REQUIREMENTS = {
    "full-cpu-models": "Integrate all Rocket/Ibex/DSP cores, caches and boot/interrupt domains",
    "full-ip-models": "Implement accelerators, fabric, memory controllers and external IO/PHYs",
    "live-noc": "Connect CPU/RTL traffic to Garnet with verified ownership/time/reset semantics",
    "full-rtl": "Elaborate a complete SoC top including a synthesizable RTL interconnect",
    "firmware-os": "Boot firmware/OS on the complete heterogeneous SoC, not its bring-up subset",
    "asic": "Actual PDK mapping, constraints, STA, place/route, DRC/LVS and release acceptance",
}


def load_system(root):
    root = root.resolve()
    data = read_yaml(inside(root, "system.yaml"))
    schema = json.loads((Path(__file__).parent / "schemas/system-v1.json").read_text())
    try:
        validate(data, schema)
    except ValidationError as exc:
        raise ContractError(f"E_SYSTEM_SCHEMA {list(exc.absolute_path)}: {exc.message}") from exc
    if data["sources"] != {"chipyard": CHIPYARD_SHA, "opentitan": OPENTITAN_SHA, "gem5": GEM5_SHA}:
        raise ContractError("system sources differ from supported immutable pins")
    full = load_project(inside(root, data["architecture"]))
    bringup = load_project(inside(root, data["bringup"]))
    resolve(bringup)
    if full.ir.vlnv != f"socsim.public:architecture:{data['name']}:1.0":
        raise ContractError("architecture identity must match the system name")
    if full.manifest["profile"] != "architecture-only-v1" or full.models["bindings"]:
        raise ContractError("architecture-only input must not claim executable bindings")
    if bringup.ir.vlnv.split(":")[2] != data["name"] + "_bringup":
        raise ContractError("bring-up identity must match the system")
    validate_noc(data["noc"])
    blocks = data["blocks"]
    names = [b["instance"] for b in blocks]
    if len(set(names)) != len(names) or set(names) != {i.name for i in full.ir.instances}:
        raise ContractError("system blocks must describe exactly every architecture instance")
    used = {b["noc_endpoint"] for b in blocks}
    if used != set(data["noc"]["endpoints"]):
        raise ContractError("NoC endpoints must all be used and cover every architecture block")
    return data, full, bringup


def plan_system(root):
    data, full, bringup = load_system(root)
    return {
        "name": data["name"],
        "status": "VALIDATED_ARCHITECTURE",
        "full_instances": len(full.ir.instances),
        "providers": dict(Counter(b["provider"] for b in data["blocks"])),
        "cpu_groups": dict(
            Counter(b["role"] for b in data["blocks"] if b["role"].startswith("cpu-"))
        ),
        "bringup_instances": len(bringup.ir.instances),
        "bringup_harts": 1,
        "bringup_cpu": "QEMU rv64; not a Rocket/Ibex heterogeneous implementation",
        "bringup_os": "Zephyr",
        "sources": data["sources"],
        "noc": data["noc"],
        "asic": {
            **data["asic"],
            "openroad_on_path": shutil.which("openroad") is not None,
            "docker_client_on_path": shutil.which("docker") is not None,
        },
        "full_system_complete": False,
        "open_requirements": FULL_REQUIREMENTS,
    }


def run_system(root, generated, models, noc_build, out, scope="full", asic_build=None):
    if scope not in {"bringup", "full"}:
        raise ContractError("unknown system acceptance scope")
    data, full, bringup = load_system(root)
    generated, models, noc_build = generated.resolve(), models.resolve(), noc_build.resolve()
    if asic_build is not None:
        asic_build = asic_build.resolve()
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "plan.json", plan_system(root))
    write_json(out / "architecture-ir.json", full.ir.to_dict())
    write_json(out / "system.json", data)
    stages = []
    checks = [
        ("bringup", lambda: run_ipblocks(bringup, generated, models, out / "bringup")),
        ("noc", lambda: run_noc(data["noc"], noc_build, out / "noc")),
        ("synthesis", lambda: synthesize_gcd(models, out / "synthesis")),
    ]
    if asic_build is not None:
        from .asic import snapshot_asic

        checks.append(
            ("asic-reference", lambda: snapshot_asic(asic_build, out / "asic-reference", models))
        )
    for name, function in checks:
        try:
            report = function()
        except (ContractError, OSError, ValueError) as exc:
            (out / name).mkdir(parents=True, exist_ok=True)
            report = {"status": "ERROR", "message": str(exc)}
            write_json(out / name / "report.json", report)
        stages.append({"name": name, "status": report["status"]})
    selected = "PASSED" if all(s["status"] == "PASSED" for s in stages) else "FAILED"
    report = {
        "schema_version": "1",
        "name": data["name"],
        "scope": scope,
        "status": ("BLOCKED" if selected == "PASSED" else "FAILED")
        if scope == "full"
        else selected,
        "selected_stages_status": selected,
        "asic_reference_requested": asic_build is not None,
        "stages": stages,
        "full_system_complete": False,
        "open_requirements": FULL_REQUIREMENTS,
        "runtime_modules": {p.name: digest(p) for p in sorted(Path(__file__).parent.glob("*.py"))},
        "files": {
            p.relative_to(out).as_posix(): digest(p) for p in sorted(out.rglob("*")) if p.is_file()
        },
    }
    write_json(out / "report.json", report)
    (out / "report.md").write_text(
        "# "
        + data["name"]
        + " acceptance\n\nRequested scope: "
        + scope
        + "\n\nStatus: **"
        + report["status"]
        + "**\n\n"
        + "\n".join(f"- {s['name']}: {s['status']}" for s in stages)
        + "\n\nFull system complete: **false**\n\nOpen requirements:\n\n"
        + "\n".join(f"- {k}: {v}" for k, v in FULL_REQUIREMENTS.items())
        + "\n"
    )
    return report


def verify_system(out):
    out = out.resolve()
    report = json.loads((out / "report.json").read_text())
    if report.get("schema_version") != "1" or report.get("full_system_complete") is not False:
        raise ContractError("invalid full-system acceptance claim")
    files = report["files"]
    required = {"system.json", "plan.json", "architecture-ir.json"}
    required |= {stage + "/report.json" for stage in ("bringup", "noc", "synthesis")}
    if not required.issubset(files):
        raise ContractError("incomplete system evidence")
    for path, expected in files.items():
        if digest(inside(out, path)) != expected:
            raise ContractError("modified system evidence")
    stages = report["stages"]
    expected_stages = ["bringup", "noc", "synthesis"]
    if report.get("asic_reference_requested") is True:
        expected_stages.append("asic-reference")
    if [s["name"] for s in stages] != expected_stages:
        raise ContractError("incomplete system stages")
    if not {stage + "/report.json" for stage in expected_stages}.issubset(files):
        raise ContractError("incomplete system stage artifacts")
    for stage in stages:
        raw = json.loads((out / stage["name"] / "report.json").read_text())
        if raw["status"] != stage["status"]:
            raise ContractError("system stage status mismatch")
    selected = "PASSED" if all(s["status"] == "PASSED" for s in stages) else "FAILED"
    expected_status = "BLOCKED" if selected == "PASSED" else "FAILED"
    if report["scope"] == "bringup":
        expected_status = selected
    elif report["scope"] != "full":
        raise ContractError("invalid system evidence scope")
    if report["status"] != expected_status or report["selected_stages_status"] != selected:
        raise ContractError("invalid system acceptance status")
    return report


def main(argv):
    parser = argparse.ArgumentParser(prog="usp system")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "plan", "test"):
        command = sub.add_parser(name)
        command.add_argument("system", type=Path)
        if name == "test":
            for arg in ("generated", "models", "noc-build", "out"):
                command.add_argument("--" + arg, type=Path, required=True)
            command.add_argument("--scope", choices=("full", "bringup"), default="full")
            command.add_argument("--asic-build", type=Path)
    sub.add_parser("verify").add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result = verify_system(args.run_dir)
        elif args.command == "test":
            result = run_system(
                args.system,
                args.generated,
                args.models,
                args.noc_build,
                args.out,
                args.scope,
                args.asic_build,
            )
        else:
            result = plan_system(args.system)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] in {"PASSED", "VALIDATED_ARCHITECTURE"} else 1
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "ERROR", "code": "E_SYSTEM", "message": str(exc)}))
        return 2
