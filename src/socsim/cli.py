"""Headless development CLI."""

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .backends import CAPABILITIES, qemu_plan
from .domain import ContractError
from .project import load_project
from .runner import run_qemu, tool_version


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="socsim")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("backends", help="show implemented and planned capabilities")
    commands.add_parser("doctor", help="inspect runtime tools without installing")
    for name in ("validate", "elaborate", "plan", "run"):
        sub = commands.add_parser(name)
        sub.add_argument("project", type=Path)
        if name in {"plan", "run"}:
            sub.add_argument("--backend", default="qemu")
        if name == "elaborate":
            sub.add_argument("--out", type=Path)
        if name == "run":
            sub.add_argument("--firmware", type=Path, required=True)
            sub.add_argument("--out", type=Path, required=True)
            sub.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args(argv)
    try:
        if args.command == "backends":
            result = CAPABILITIES
        elif args.command == "doctor":
            result = {"platform_version": __version__, "tools": {}}
            for tool in ("qemu-system-riscv64", "riscv64-unknown-elf-gcc"):
                try:
                    path, version = tool_version(tool)
                    result["tools"][tool] = {
                        "status": "available",
                        "path": path,
                        "version": version,
                    }
                except ContractError as exc:
                    result["tools"][tool] = {"status": "BLOCKED", "reason": str(exc)}
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if all(v["status"] == "available" for v in result["tools"].values()) else 1
        else:
            project = load_project(args.project)
            if args.command == "validate":
                result = {
                    "status": "PASSED",
                    "scope": "XSD + supported semantic subset",
                    "design": project.ir.vlnv,
                    "instances": len(project.ir.instances),
                }
            elif args.command == "elaborate":
                result = project.ir.to_dict()
                if args.out:
                    args.out.parent.mkdir(parents=True, exist_ok=True)
                    # Exclusive creation keeps manually curated evidence safe.
                    with args.out.open("x") as stream:
                        json.dump(result, stream, indent=2, sort_keys=True)
                        stream.write("\n")
            elif args.command == "plan":
                result = qemu_plan(project, args.backend)
            else:
                qemu_plan(project, args.backend)
                result = run_qemu(project, args.firmware, args.out, args.timeout)
                print(json.dumps(result, indent=2, sort_keys=True))
                return 0 if result["status"] == "PASSED" else 1
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ContractError, OSError) as exc:
        print(f"socsim: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
