"""Executable lifecycle commands for the custom QEMU + Zephyr profile."""

import argparse
import json
import os
from pathlib import Path

from .domain import ContractError
from .pipeline import build_qemu, build_zephyr, generate, resolve, run_custom
from .project import load_project


def main(argv):
    parser = argparse.ArgumentParser(prog="usp")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "build", "run", "test"):
        sub = commands.add_parser(name)
        sub.add_argument("project", type=Path)
        sub.add_argument(
            "--profile", choices=["functional-qemu", "usp-rv64-v1"], default="functional-qemu"
        )
        if name == "generate":
            sub.add_argument("--out", type=Path, required=True)
        else:
            sub.add_argument("--generated", type=Path, required=True)
        if name == "build":
            sub.add_argument("--source-qemu", type=Path, required=True)
            sub.add_argument("--jobs", type=int, choices=range(1, 9), default=4)
        if name in {"run", "test"}:
            sub.add_argument("--out", type=Path, required=True)
            sub.add_argument("--os", choices=["zephyr", "linux", "freertos"], default="zephyr")
            sub.add_argument("--timeout", type=float, default=20)
            sub.add_argument("--bridge-models", type=Path)
            sub.add_argument("--bridge-mode", choices=["tlm", "rtl"], default="tlm")
        if name == "test":
            sub.add_argument(
                "--suite", choices=["zephyr-boot", "linux-boot", "freertos-boot"], default=None
            )
    software = commands.add_parser("software").add_subparsers(dest="action", required=True)
    build = software.add_parser("build")
    build.add_argument("project", type=Path)
    build.add_argument("--os", choices=["zephyr", "linux", "freertos"], required=True)
    build.add_argument("--generated", type=Path, required=True)
    build.add_argument("--jobs", type=int, choices=range(1, 9), default=4)
    build.add_argument("--source-zephyr", type=Path)
    build.add_argument("--source-linux", type=Path)
    build.add_argument("--source-opensbi", type=Path)
    build.add_argument("--source-freertos", type=Path)
    build.add_argument(
        "--test-case", choices=["smoke", "assert-failure", "tick-disabled"], default="smoke"
    )
    build.add_argument("--cross-compile", default=os.environ.get("CROSS_COMPILE"), required=False)
    models = commands.add_parser("models").add_subparsers(dest="action", required=True)
    for action in ("resolve", "list", "explain"):
        sub = models.add_parser(action)
        sub.add_argument("project", type=Path)
        sub.add_argument("--policy", choices=["strict"], default="strict")
        if action == "explain":
            sub.add_argument("--instance", required=True)
    native_build = models.add_parser("build")
    native_build.add_argument("--out", type=Path, required=True)
    native_build.add_argument("--native-source", type=Path, required=True)
    native_build.add_argument("--source-opentitan", type=Path, required=True)
    gem5 = commands.add_parser("gem5").add_subparsers(dest="action", required=True)
    gem5_build = gem5.add_parser("build")
    gem5_build.add_argument("--source-gem5", type=Path, required=True)
    gem5_build.add_argument("--out", type=Path, required=True)
    gem5_build.add_argument("--jobs", type=int, choices=range(1, 9), default=4)
    gem5_run = gem5.add_parser("run")
    gem5_run.add_argument("project", type=Path)
    gem5_run.add_argument("--generated", type=Path, required=True)
    gem5_run.add_argument("--build-dir", type=Path, required=True)
    gem5_run.add_argument("--out", type=Path, required=True)
    report = commands.add_parser("report")
    report.add_argument("run_dir", type=Path)
    report.add_argument("--format", choices=["json", "md"], default="json")
    args = parser.parse_args([arg for arg in argv if arg != "--json"])
    try:
        if args.command == "report":
            path = args.run_dir / ("report.md" if args.format == "md" else "report.json")
            print(path.read_text())
            return 0
        if args.command == "models" and args.action == "build":
            from .hybrid import build_models

            result = build_models(
                args.out.resolve(), args.native_source.resolve(), args.source_opentitan
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "gem5" and args.action == "build":
            from .gem5 import build_gem5

            binary = build_gem5(args.out.resolve(), args.source_gem5, args.jobs)
            print(json.dumps({"status": "BUILT", "binary": str(binary)}))
            return 0
        project = load_project(args.project)
        if args.command == "gem5":
            from .gem5 import run_gem5

            result = run_gem5(
                project, args.generated.resolve(), args.build_dir.resolve(), args.out.resolve()
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["status"] == "PASSED" else 1
        if args.command == "generate":
            result = {"status": "GENERATED", "directory": str(generate(project, args.out))}
        elif args.command == "build":
            binary = build_qemu(project, args.generated.resolve(), args.source_qemu, args.jobs)
            result = {"status": "BUILT", "binary": str(binary)}
        elif args.command == "software":
            if not args.cross_compile:
                raise ContractError("--cross-compile or CROSS_COMPILE is required")
            if args.test_case != "smoke" and args.os != "freertos":
                raise ContractError(
                    "--test-case applies only to the FreeRTOS acceptance application"
                )
            if args.os == "freertos":
                from .freertos import build_freertos

                if not args.source_freertos:
                    raise ContractError("--source-freertos is required")
                elf = build_freertos(
                    project,
                    args.generated.resolve(),
                    args.source_freertos,
                    args.cross_compile,
                    args.test_case,
                )
            elif args.os == "zephyr":
                if not args.source_zephyr:
                    raise ContractError("--source-zephyr is required")
                elf = build_zephyr(
                    project,
                    args.generated.resolve(),
                    args.source_zephyr,
                    args.cross_compile,
                    args.jobs,
                )
            else:
                from .linux import build_linux

                if not args.source_linux or not args.source_opensbi:
                    raise ContractError("--source-linux and --source-opensbi are required")
                elf = build_linux(
                    project,
                    args.generated.resolve(),
                    args.source_linux,
                    args.source_opensbi,
                    args.cross_compile,
                    args.jobs,
                )
            result = {"status": "BUILT", "elf": str(elf)}
        elif args.command == "models":
            bindings = resolve(project)
            if args.action == "explain":
                bindings = [b for b in bindings if b["instance"] == args.instance]
                if not bindings:
                    raise ContractError("unknown instance")
            result = {
                "policy": "strict",
                "models": bindings,
                "reason": "Exact VLNV/implementation match and validated hardware/IRQ contract",
            }
        else:
            if args.command == "test" and args.suite:
                args.os = args.suite.removesuffix("-boot")
            if args.bridge_models:
                from .hybrid import run_hybrid

                if args.os != "zephyr":
                    raise ContractError("hybrid v1 acceptance supports Zephyr only")
                result = run_hybrid(
                    project,
                    args.generated.resolve(),
                    args.bridge_models.resolve(),
                    args.out.resolve(),
                    args.bridge_mode,
                    args.timeout,
                )
            elif args.os == "freertos":
                from .freertos import run_freertos

                result = run_freertos(
                    project, args.generated.resolve(), args.out.resolve(), args.timeout
                )
            else:
                if project.manifest["profile"] == "usp-rv64-hybrid-v1":
                    raise ContractError("hybrid profile requires explicit --bridge-models")
                result = run_custom(
                    project, args.generated.resolve(), args.out.resolve(), args.timeout, args.os
                )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["status"] == "PASSED" else 1
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ContractError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "ERROR", "code": "E_CONTRACT", "message": str(exc)}, sort_keys=True
            )
        )
        return 2
