"""Headless development CLI."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .backends import CAPABILITIES, qemu_plan
from .domain import ContractError
from .project import load_project
from .runner import run_qemu, tool_version


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    json_errors = "--json" in argv
    argv = [argument for argument in argv if argument != "--json"]
    if argv[:2] == ["ipxact", "validate"]:
        argv = argv[1:]
    if argv and argv[0] == "unified":
        from .unified import main as unified_main

        return unified_main(argv[1:])
    if argv and argv[0] == "noc":
        from .noc import main as noc_main

        return noc_main(argv[1:])
    if argv and argv[0] == "chip":
        from .testchip import main as chip_main

        return chip_main(argv[1:])
    if argv and argv[0] == "asic":
        from .asic import main as asic_main

        return asic_main(argv[1:])
    if argv and argv[0] == "system":
        from .systems import main as system_main

        return system_main(argv[1:])
    if argv and argv[0] == "workspace":
        from .workspace import main as workspace_main

        return workspace_main(argv[1:])
    if argv and (
        argv[0] in {"generate", "build", "software", "test", "report", "models", "gem5", "ipblocks"}
        or (argv[0] == "run" and "--generated" in argv)
    ):
        from .lifecycle_cli import main as lifecycle_main

        return lifecycle_main(argv)
    parser = argparse.ArgumentParser(prog="usp (socsim compatibility)")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("unified", help="one SoC/ELF with SystemC IPs on QEMU or gem5; use --help")
    commands.add_parser("backends", help="show implemented and planned capabilities")
    commands.add_parser("doctor", help="inspect runtime tools without installing")
    commands.add_parser("workspace", help="validate/run/verify multi-SoC acceptance; use --help")
    commands.add_parser("system", help="validate/plan/test AI_SOC and AIZ_SOC; use --help")
    commands.add_parser("noc", help="build pinned Garnet network tools; use --help")
    commands.add_parser("asic", help="build/verify reference RTL-to-GDS flow; use --help")
    commands.add_parser("chip", help="build/run CPU-owned RTL test chips; use --help")
    for extra in ("generate", "build", "software", "test", "report", "models", "gem5", "ipblocks"):
        commands.add_parser(extra, help="custom QEMU/Zephyr lifecycle; use --help")
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
    result: dict[str, Any]
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
                if project.manifest["profile"] in {
                    "usp-rv64-v1",
                    "usp-rv64-hybrid-v1",
                    "usp-rv64-ips-v1",
                }:
                    from .pipeline import resolve

                    if args.backend != "qemu":
                        raise ContractError("UNSUPPORTED custom backend")
                    result = {
                        "backend": "qemu",
                        "machine": "usp-rv64",
                        "bindings": resolve(project),
                    }
                else:
                    result = qemu_plan(project, args.backend)
            else:
                qemu_plan(project, args.backend)
                result = run_qemu(project, args.firmware, args.out, args.timeout)
                print(json.dumps(result, indent=2, sort_keys=True))
                return 0 if result["status"] == "PASSED" else 1
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ContractError, OSError) as exc:
        if json_errors:
            print(json.dumps({"status": "ERROR", "code": "E_CONTRACT", "message": str(exc)}))
        else:
            print(f"socsim: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
