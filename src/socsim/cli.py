"""Headless development CLI."""

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .backends import CAPABILITIES, qemu_plan
from .domain import ContractError
from .project import load_project
from .runner import run_qemu, tool_version

_SUBCOMMAND_MODULES: dict[str, tuple[str, str]] = {
    "ci": ("socsim.hosted_ci", "read workflow-published Git-note evidence; use --help"),
    "mcp": (
        "socsim.mcp_server",
        "run Model Context Protocol (MCP) stdio server for AI clients; use --help",
    ),
    "compose": (
        "socsim.multiplatform_composer",
        "compose/run/calibrate/audit multi-platform SoCs (Chipyard+gem5+QEMU+RTL+plugins)",
    ),
    "unified-soc": (
        "socsim.unified_systemc_soc",
        "build/run unified SystemC/TLM-2.0 multi-platform SoC with RV64 SW; use --help",
    ),
    "registry": (
        "socsim.ip_registry",
        "publish/resolve/verify versioned SHA-256 IP model packages; use --help",
    ),
    "dse": (
        "socsim.dse_cluster",
        "run CHIA-style parallel DSE sweeps and RTL-vs-TLM calibration; use --help",
    ),
    "loop": (
        "socsim.chia_loop",
        "run CHIA-style cyclic HW/SW optimization loop with isolated oracle; use --help",
    ),
    "ip": (
        "socsim.ip_scaffold",
        "scaffold IEEE 1685-2022 + SystemC + SV RTL IP plugin packages; use --help",
    ),
    "dashboard": (
        "socsim.dashboard",
        "render self-contained HTML5 verification, coverage, DSE and loop dashboard; use --help",
    ),
    "qa": ("socsim.qa_runner", "run real QA/QC suites and verify scorecards; use --help"),
    "upgrade": ("socsim.upgrade", "validate sequential milestone status/evidence; use --help"),
    "domains": (
        "socsim.native_domains",
        "derive/validate clock-reset and address views; use --help",
    ),
    "contracts": ("socsim.v04_contracts", "check CPU/bus/NoC/calibration/IP contracts; use --help"),
    "package": ("socsim.native_package", "build/verify/inspect a pinned model package; use --help"),
    "flow": ("socsim.native_flow", "plan/preflight/bsp/build/run/verify a native SoC; use --help"),
    "soc": ("socsim.soc_native", "generate a native SoC from spec/memory map/IO; use --help"),
    "coherent": ("socsim.coherent", "two-hart coherent Garnet/DMA with source IPs; use --help"),
    "rtl-zephyr": (
        "socsim.rtl_zephyr",
        "source-built Zephyr on actual RV32 RTL masters; use --help",
    ),
    "fabric": ("socsim.fabric", "live Ruby/Garnet in unified gem5/SystemC SoC; use --help"),
    "ucos": ("socsim.ucos", "source-built µC/OS-II/III on RTL CPU masters; use --help"),
    "rtl-os": ("socsim.rtl_os", "FreeRTOS on actual RV32 RTL CPU masters; use --help"),
    "rocket": ("socsim.rocket_cpu", "source-built Rocket AXI CPU on QEMU/gem5 memory; use --help"),
    "rtlcpu": ("socsim.rtl_cpu", "actual RTL CPU/SystemC master adapters; use --help"),
    "platform": ("socsim.cpu_platform", "select CPU provider, engine and workloads; use --help"),
    "layers": ("socsim.layers", "software integration matrix and CPU metrics; use --help"),
    "unified": ("socsim.unified", "one SoC/ELF with SystemC IPs on QEMU or gem5; use --help"),
    "noc": ("socsim.noc", "build pinned Garnet network tools; use --help"),
    "chip": ("socsim.testchip", "build/run CPU-owned RTL test chips; use --help"),
    "asic": ("socsim.asic", "build/verify reference RTL-to-GDS flow; use --help"),
    "system": ("socsim.systems", "validate/plan/test AI_SOC and AIZ_SOC; use --help"),
    "workspace": ("socsim.workspace", "validate/run/verify multi-SoC acceptance; use --help"),
}
_LIFECYCLE_SUBCOMMANDS = frozenset(
    {"generate", "build", "software", "test", "report", "models", "gem5", "ipblocks"}
)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    json_errors = "--json" in argv
    argv = [argument for argument in argv if argument != "--json"]
    if argv[:2] == ["ipxact", "validate"]:
        argv = argv[1:]
    if argv and argv[0] in _SUBCOMMAND_MODULES:
        module_name = _SUBCOMMAND_MODULES[argv[0]][0]
        return int(importlib.import_module(module_name).main(argv[1:]))
    if argv and (argv[0] in _LIFECYCLE_SUBCOMMANDS or (argv[0] == "run" and "--generated" in argv)):
        from .lifecycle_cli import main as lifecycle_main

        return lifecycle_main(argv)
    parser = argparse.ArgumentParser(prog="usp (socsim compatibility)")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    for subcommand, (_, help_text) in _SUBCOMMAND_MODULES.items():
        commands.add_parser(subcommand, help=help_text)
    commands.add_parser("backends", help="show implemented and planned capabilities")
    commands.add_parser("doctor", help="inspect runtime tools without installing")
    for extra in sorted(_LIFECYCLE_SUBCOMMANDS):
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
