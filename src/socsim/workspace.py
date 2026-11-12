"""Sequential multi-SoC acceptance with evidence integrity and explicit open scope."""

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .domain import ContractError
from .ipxact import digest
from .project import inside, keys, load_project, read_yaml
from .runner import write_json

KINDS = {"zephyr", "freertos", "linux", "gem5-se", "fifo-tlm", "fifo-rtl", "source-ips"}
OPEN_CRITERIA = {
    "P1-general-elaboration": (
        "General hierarchy/parameters, register side effects, "
        "bus/clock/reset graphs and translations"
    ),
    "P4-yocto-image": "Actual Yocto image build and boot; download access remains blocked",
    "P5-full-system": "gem5 full-system board and hardware/software conformance",
    "P6-full-socs": "Rocket/Chipyard and Earlgrey full SoCs beyond selected peripherals",
    "P7-release": "Hosted checks, branch protection, release and fresh-environment restore",
    "other-rtos": "Selected and licensed uC/OS product and its real BSP acceptance",
}


def load_workspace(path):
    path = path.resolve()
    data = keys(read_yaml(path), {"schema_version", "targets"}, "workspace")
    if data["schema_version"] != "1" or not isinstance(data["targets"], list):
        raise ContractError("unsupported workspace schema")
    if not 1 <= len(data["targets"]) <= 64:
        raise ContractError("workspace must contain 1..64 targets")
    names = set()
    for target in data["targets"]:
        if not isinstance(target, dict):
            raise ContractError("workspace target must be an object")
        kind = target.get("kind")
        if not isinstance(kind, str) or kind not in KINDS:
            raise ContractError("unsupported workspace target kind")
        required = {"name", "kind", "project", "generated"}
        if kind in {"gem5-se", "fifo-tlm", "fifo-rtl", "source-ips"}:
            required.add("models")
        keys(target, required, "workspace target")
        name = target["name"]
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name)
            or name in names
        ):
            raise ContractError("duplicate/unsafe workspace target name")
        names.add(name)
        for field in required & {"project", "generated", "models"}:
            inside(path.parent, target[field])
        project = load_project(inside(path.parent, target["project"]))
        expected = "usp-rv64-v1"
        if kind.startswith("fifo-"):
            expected = "usp-rv64-hybrid-v1"
        elif kind == "source-ips":
            expected = "usp-rv64-ips-v1"
        if project.manifest["profile"] != expected:
            raise ContractError(f"{name}: kind/profile mismatch")
    return data


def run_target(root, target, out):
    project = load_project(inside(root, target["project"]))
    generated = inside(root, target["generated"])
    kind = target["kind"]
    if kind in {"zephyr", "linux"}:
        from .pipeline import run_custom

        return run_custom(project, generated, out, osname=kind)
    if kind == "freertos":
        from .freertos import run_freertos

        return run_freertos(project, generated, out)
    models = inside(root, target["models"])
    if kind == "gem5-se":
        from .gem5 import run_gem5

        return run_gem5(project, generated, models, out)
    if kind == "source-ips":
        from .ipblocks import run_ipblocks

        return run_ipblocks(project, generated, models, out)
    from .hybrid import run_hybrid

    return run_hybrid(project, generated, models, out, kind.removeprefix("fifo-"))


def run_workspace(path, out):
    path, out = path.resolve(), out.resolve()
    data = load_workspace(path)
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "workspace.json", data)
    outcomes = []
    for target in data["targets"]:
        target_dir = out / target["name"]
        try:
            report = run_target(path.parent, target, target_dir)
        except (ContractError, OSError, ValueError, subprocess.SubprocessError) as exc:
            target_dir.mkdir(exist_ok=True)
            report = {"status": "ERROR", "message": str(exc)}
            write_json(target_dir / "report.json", report)
        outcomes.append(
            {
                "name": target["name"],
                "kind": target["kind"],
                "status": report["status"],
                "report": target["name"] + "/report.json",
                "report_sha256": digest(target_dir / "report.json"),
                "files": {
                    p.relative_to(out).as_posix(): digest(p)
                    for p in sorted(target_dir.rglob("*"))
                    if p.is_file()
                },
            }
        )
    head = subprocess.run(
        ["git", "-C", str(path.parent), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    modules = Path(__file__).parent
    result: dict[str, Any] = {
        "schema_version": "1",
        "scope": "selected workspace acceptance; not full master completion",
        "status": "PASSED" if all(r["status"] == "PASSED" for r in outcomes) else "FAILED",
        "workspace_sha256": digest(out / "workspace.json"),
        "configuration_sha256": digest(path),
        "source_commit": head.stdout.strip() if head.returncode == 0 else None,
        "runtime_modules": {p.name: digest(p) for p in sorted(modules.glob("*.py"))},
        "targets": outcomes,
        "full_master_complete": False,
        "open_criteria": OPEN_CRITERIA,
    }
    write_json(out / "report.json", result)
    (out / "report.md").write_text(
        "# Workspace acceptance\n\nSelected gate: **"
        + result["status"]
        + "**\n\n"
        + "\n".join(f"- {r['name']} ({r['kind']}): {r['status']}" for r in outcomes)
        + "\n\nFull master completion: **false**\n\nOpen criteria:\n\n"
        + "\n".join(f"- {k}: {v}" for k, v in OPEN_CRITERIA.items())
        + "\n"
    )
    return result


def verify_bundle(out):
    out = out.resolve()
    result = json.loads((out / "report.json").read_text())
    if result.get("schema_version") != "1" or digest(out / "workspace.json") != result.get(
        "workspace_sha256"
    ):
        raise ContractError("workspace evidence identity mismatch")
    config = json.loads((out / "workspace.json").read_text())
    targets = result.get("targets", [])
    if [r["name"] for r in targets] != [r["name"] for r in config["targets"]]:
        raise ContractError("incomplete workspace evidence")
    for entry in targets:
        path = inside(out, entry["report"])
        if digest(path) != entry["report_sha256"]:
            raise ContractError("modified target evidence")
        report = json.loads(path.read_text())
        if report["status"] != entry["status"]:
            raise ContractError("target status mismatch")
        files = entry.get("files", {})
        if entry["report"] not in files:
            raise ContractError("incomplete target artifacts")
        for relative, sha in files.items():
            artifact = inside(out, relative)
            if not artifact.is_relative_to(out / entry["name"]) or digest(artifact) != sha:
                raise ContractError("modified target artifact")
    expected = "PASSED" if targets and all(r["status"] == "PASSED" for r in targets) else "FAILED"
    if result["status"] != expected or result["full_master_complete"] is not False:
        raise ContractError("invalid workspace acceptance claim")
    return result


def main(argv):
    parser = argparse.ArgumentParser(prog="usp workspace")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "test"):
        sub = commands.add_parser(name)
        sub.add_argument("workspace", type=Path)
        if name == "test":
            sub.add_argument("--out", type=Path, required=True)
    commands.add_parser("verify").add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            result = {"status": "PASSED", "targets": len(load_workspace(args.workspace)["targets"])}
        elif args.command == "verify":
            result = verify_bundle(args.run_dir)
        else:
            result = run_workspace(args.workspace, args.out)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "PASSED" else 1
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "ERROR", "code": "E_WORKSPACE", "message": str(exc)}))
        return 2
