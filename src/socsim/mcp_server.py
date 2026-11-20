"""Model Context Protocol (MCP) JSON-RPC 2.0 stdio server for the USP HW/SW Emulator.

Exposes the real SoC emulator toolchain (IP-XACT/YAML validation, dual-engine QEMU/gem5
generation, clock/reset domains, BSP synthesis, gem5 Garnet NoC topology, SystemC model
packaging & conformance execution, QEMU guest boot, multi-platform QA, and milestone audits)
to AI clients over standard MCP stdio transport without extra third-party dependencies.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

import yaml

from .domain import ContractError
from .ip_catalog import IP_CATALOG, catalog_summary
from .native_bsp_gen import synthesize_bsp_bundle
from .native_domains import derive_domains_from_ir
from .native_flow import preflight_flow
from .native_ir import read_ir
from .native_package import (
    build_package,
    generate_package_header,
    package_capabilities,
    read_package,
)
from .project import load_project
from .qa_runner import SUITES as QA_SUITES
from .qa_runner import run_qa, verify_scorecard
from .readiness_audit import audit_milestone_readiness
from .runner import run_qemu, tool_version
from .sdk import EmulatorNode
from .soc_spec import catalog as native_model_catalog
from .soc_spec import load_spec, validate_spec
from .v04_contracts import (
    CPU_ADAPTER_MATRIX,
    check_cpu_bus_adapter,
    derive_noc_topology,
    evaluate_calibration_report,
)
from .version import __version__

SUPPORTED_PROTOCOL_VERSIONS = ("2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "usp-emulator-mcp"
SERVER_INSTRUCTIONS = (
    "USP/SocSim HW/SW Emulator MCP server. All tools execute real validation, generation, "
    "compilation, and simulation flows without substituting ISS or stub hardware when an "
    "explicit RTL/SystemC/bus adapter is required. Missing local simulators return explicit "
    "BLOCKED diagnostics."
)


@dataclasses.dataclass(frozen=True)
class McpToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = False
    destructive: bool = False
    idempotent: bool = True


def _probe_environment() -> dict[str, Any]:
    tools: dict[str, Any] = {}
    for binary in (
        "qemu-system-riscv64",
        "riscv64-unknown-elf-gcc",
        "c++",
        "verilator",
        "yosys",
        "podman",
    ):
        try:
            path, version = tool_version(binary)
            tools[binary] = {"status": "available", "path": path, "version": version}
        except ContractError as exc:
            tools[binary] = {"status": "BLOCKED", "reason": str(exc)}

    if shutil.which("pkg-config") is not None:
        probe = subprocess.run(
            ["pkg-config", "--modversion", "systemc"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            tools["systemc"] = {"status": "available", "version": probe.stdout.strip()}
        else:
            tools["systemc"] = {
                "status": "BLOCKED",
                "reason": "libsystemc-dev / pkg-config systemc not found",
            }
    else:
        tools["systemc"] = {"status": "BLOCKED", "reason": "pkg-config not found"}

    return {
        "server": SERVER_NAME,
        "platform_version": __version__,
        "tools": tools,
    }


def _discover_specs(root: Path) -> dict[str, Any]:
    specs_dir = root / "specs"
    if not specs_dir.is_dir():
        return {"root": str(root), "soc_specs": [], "flows": [], "packages": []}

    soc_specs: list[str] = []
    flows: list[str] = []
    packages: list[str] = []
    for path in sorted(specs_dir.rglob("*.yaml")):
        rel = path.relative_to(root).as_posix()
        try:
            data = yaml.safe_load(path.read_text())
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        schema = data.get("schema_version")
        if schema == "native-model-package-1":
            packages.append(rel)
        elif schema == "native-flow-1":
            flows.append(rel)
        elif schema in {"1", "native-fabric-1", "multiplatform-target-audit-1"}:
            soc_specs.append(rel)
    return {
        "root": str(root),
        "soc_specs": soc_specs,
        "flows": flows,
        "packages": packages,
    }


class EmulatorMcpServer:
    """Stateless JSON-RPC 2.0 MCP server bound to a workspace root."""

    def __init__(self, root: Path, *, allow_external_paths: bool = False) -> None:
        self.root = root.resolve()
        self.allow_external_paths = allow_external_paths
        self._tool_specs: tuple[McpToolSpec, ...] = self._build_tool_specs()
        self._tool_handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "emulator_doctor": self._tool_doctor,
            "emulator_list_specs": self._tool_list_specs,
            "emulator_list_models": self._tool_list_models,
            "emulator_check_cpu_adapter": self._tool_check_cpu_adapter,
            "emulator_validate_spec": self._tool_validate_spec,
            "emulator_generate_soc": self._tool_generate_soc,
            "emulator_preflight_flow": self._tool_preflight_flow,
            "emulator_derive_domains": self._tool_derive_domains,
            "emulator_synthesize_bsp": self._tool_synthesize_bsp,
            "emulator_derive_noc_topology": self._tool_derive_noc_topology,
            "emulator_package_inspect": self._tool_package_inspect,
            "emulator_package_build": self._tool_package_build,
            "emulator_run_qa": self._tool_run_qa,
            "emulator_run_qemu": self._tool_run_qemu,
        }

    def _resolve_path(self, raw: str, *, field: str = "path", must_exist: bool = False) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise ContractError(f"E_MCP_PARAM {field} must be a non-empty path string")
        candidate = Path(raw)
        resolved = (
            candidate.resolve() if candidate.is_absolute() else (self.root / candidate).resolve()
        )
        if not self.allow_external_paths:
            try:
                resolved.relative_to(self.root)
            except ValueError as exc:
                raise ContractError(
                    f"E_MCP_SANDBOX {field} escapes workspace root {self.root}: {raw}"
                ) from exc
        if must_exist and not resolved.exists():
            raise ContractError(f"E_MCP_PATH {field} does not exist: {raw}")
        return resolved

    @staticmethod
    def _build_tool_specs() -> tuple[McpToolSpec, ...]:
        return (
            McpToolSpec(
                name="emulator_doctor",
                description=(
                    "Inspect host/container simulator and compiler readiness (QEMU, RISC-V GCC, "
                    "SystemC, Verilator, Yosys, Podman) without installing anything."
                ),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                read_only=True,
            ),
            McpToolSpec(
                name="emulator_list_specs",
                description=(
                    "Discover available SoC YAML specs, native flow manifests, and SystemC "
                    "model packages under the workspace."
                ),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                read_only=True,
            ),
            McpToolSpec(
                name="emulator_list_models",
                description=(
                    "List qualified native SoC models, CPU/bus adapters, and the 33-entry pinned "
                    "IP readiness catalog (optionally filtered by provider or readiness)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "provider": {
                            "type": "string",
                            "description": (
                                "Optional provider filter (chipyard, opentitan, external)."
                            ),
                        },
                        "readiness": {
                            "type": "string",
                            "description": (
                                "Optional readiness filter (qualified-native-or-rtl, "
                                "pinned-source-needs-adapter, missing-source-implementation)."
                            ),
                        },
                    },
                    "additionalProperties": False,
                },
                read_only=True,
            ),
            McpToolSpec(
                name="emulator_check_cpu_adapter",
                description=(
                    "Verify whether a (provider, model, protocol, engine) CPU/bus adapter is "
                    "qualified; rejects unimplemented adapters without ISS substitution."
                ),
                input_schema={
                    "type": "object",
                    "required": ["provider", "model", "protocol", "engine"],
                    "properties": {
                        "provider": {"type": "string"},
                        "model": {"type": "string"},
                        "protocol": {"type": "string"},
                        "engine": {"type": "string", "enum": ["qemu", "gem5"]},
                    },
                    "additionalProperties": False,
                },
                read_only=True,
            ),
            McpToolSpec(
                name="emulator_validate_spec",
                description=(
                    "Validate a native SoC YAML specification from a workspace file path or "
                    "inline YAML string (address map, IRQs, boot entry, qualified models)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "spec_path": {
                            "type": "string",
                            "description": "Path to a native SoC YAML spec relative to workspace.",
                        },
                        "spec_yaml": {
                            "type": "string",
                            "description": "Inline YAML content of a native SoC spec.",
                        },
                    },
                    "additionalProperties": False,
                },
                read_only=True,
            ),
            McpToolSpec(
                name="emulator_generate_soc",
                description=(
                    "Generate IP-XACT 2022 XML, canonical Native IR, DTS, headers, and QEMU/gem5 "
                    "build artifacts from a native SoC YAML spec into a fresh output directory."
                ),
                input_schema={
                    "type": "object",
                    "required": ["spec_path", "out_dir"],
                    "properties": {
                        "spec_path": {"type": "string"},
                        "out_dir": {"type": "string"},
                        "engine": {
                            "type": "string",
                            "enum": ["qemu", "gem5", "both"],
                            "default": "both",
                        },
                    },
                    "additionalProperties": False,
                },
                read_only=False,
                idempotent=False,
            ),
            McpToolSpec(
                name="emulator_preflight_flow",
                description=(
                    "Run design-agnostic ownership, clock/reset domain, and workload preflight "
                    "on a native-flow-1 YAML file."
                ),
                input_schema={
                    "type": "object",
                    "required": ["flow_path"],
                    "properties": {"flow_path": {"type": "string"}},
                    "additionalProperties": False,
                },
                read_only=True,
            ),
            McpToolSpec(
                name="emulator_derive_domains",
                description=(
                    "Derive explicit clock/reset domains and per-initiator address views from "
                    "a generated native-ir.json."
                ),
                input_schema={
                    "type": "object",
                    "required": ["ir_path"],
                    "properties": {"ir_path": {"type": "string"}},
                    "additionalProperties": False,
                },
                read_only=True,
            ),
            McpToolSpec(
                name="emulator_synthesize_bsp",
                description=(
                    "Synthesize deterministic C hardware headers (soc.h), linker script (soc.ld), "
                    "and BSP manifest from a generated native-ir.json."
                ),
                input_schema={
                    "type": "object",
                    "required": ["ir_path", "out_dir"],
                    "properties": {
                        "ir_path": {"type": "string"},
                        "out_dir": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                read_only=False,
                idempotent=False,
            ),
            McpToolSpec(
                name="emulator_derive_noc_topology",
                description=(
                    "Derive an IR-bound gem5 Ruby/Garnet mesh NoC topology (rows x cols) with "
                    "mandatory PIO bypass disclosure and optional ROI calibration check."
                ),
                input_schema={
                    "type": "object",
                    "required": ["ir_path", "rows", "cols", "pio_bypass_disclosed"],
                    "properties": {
                        "ir_path": {"type": "string"},
                        "rows": {"type": "integer", "minimum": 1, "maximum": 16},
                        "cols": {"type": "integer", "minimum": 1, "maximum": 16},
                        "pio_bypass_disclosed": {"type": "boolean"},
                        "coherence_protocol": {
                            "type": "string",
                            "enum": ["ruby-garnet-simple", "mesi-two-level", "none-functional"],
                            "default": "ruby-garnet-simple",
                        },
                        "synchronization": {
                            "type": "string",
                            "enum": [
                                "lockstep-annotated",
                                "quantum-bounded",
                                "cycle-accurate-rtl",
                            ],
                            "default": "lockstep-annotated",
                        },
                        "calibration": {
                            "type": "object",
                            "description": "Optional ROI cycle calibration check parameters.",
                            "properties": {
                                "roi_name": {"type": "string"},
                                "reference_source": {
                                    "type": "string",
                                    "enum": ["native-gem5", "native-rtl"],
                                },
                                "model_source": {"type": "string"},
                                "reference_cycles": {"type": "number"},
                                "estimated_cycles": {"type": "number"},
                                "max_relative_error": {"type": "number"},
                            },
                        },
                    },
                    "additionalProperties": False,
                },
                read_only=True,
            ),
            McpToolSpec(
                name="emulator_package_inspect",
                description=(
                    "Verify SHA-256 hashes, inspect MMIO/IRQ/reset/error capabilities, and "
                    "synthesize the C register header for a native-model-package-1 package.yaml."
                ),
                input_schema={
                    "type": "object",
                    "required": ["package_path"],
                    "properties": {
                        "package_path": {"type": "string"},
                        "header_prefix": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                read_only=True,
            ),
            McpToolSpec(
                name="emulator_package_build",
                description=(
                    "Compile a SystemC/C++17 native-model-package-1 model and execute all "
                    "declared conformance vectors over usp-mmio32-v1 Unix socket."
                ),
                input_schema={
                    "type": "object",
                    "required": ["package_path", "out_dir"],
                    "properties": {
                        "package_path": {"type": "string"},
                        "out_dir": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                read_only=False,
                idempotent=False,
            ),
            McpToolSpec(
                name="emulator_run_qa",
                description=(
                    "Run a real-execution QA/QC suite (quick, unseen, multiplatform, wheel, full) "
                    "into a fresh output directory and verify its tamper-proof SHA-256 scorecard."
                ),
                input_schema={
                    "type": "object",
                    "required": ["suite", "out_dir"],
                    "properties": {
                        "suite": {"type": "string", "enum": list(QA_SUITES)},
                        "out_dir": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                read_only=False,
                idempotent=False,
            ),
            McpToolSpec(
                name="emulator_run_qemu",
                description=(
                    "Boot an RV64 ELF firmware image on QEMU for a validated IP-XACT project "
                    "directory and return console output, return code, and artifact hashes."
                ),
                input_schema={
                    "type": "object",
                    "required": ["project_path", "firmware_path", "out_dir"],
                    "properties": {
                        "project_path": {"type": "string"},
                        "firmware_path": {"type": "string"},
                        "out_dir": {"type": "string"},
                        "timeout": {"type": "number", "default": 15.0},
                    },
                    "additionalProperties": False,
                },
                read_only=False,
                idempotent=False,
            ),
        )

    # --- Tool implementations ---

    def _tool_doctor(self, _args: dict[str, Any]) -> dict[str, Any]:
        return _probe_environment()

    def _tool_list_specs(self, _args: dict[str, Any]) -> dict[str, Any]:
        return _discover_specs(self.root)

    def _tool_list_models(self, args: dict[str, Any]) -> dict[str, Any]:
        provider = args.get("provider")
        readiness = args.get("readiness")
        entries = [
            dataclasses.asdict(e)
            for e in IP_CATALOG
            if (provider is None or e.provider == provider)
            and (readiness is None or e.readiness == readiness)
        ]
        return {
            "native_models": native_model_catalog(),
            "cpu_adapters": [dataclasses.asdict(a) for a in CPU_ADAPTER_MATRIX],
            "ip_catalog_summary": catalog_summary(),
            "ip_catalog_entries": entries,
        }

    def _tool_check_cpu_adapter(self, args: dict[str, Any]) -> dict[str, Any]:
        return check_cpu_bus_adapter(
            str(args["provider"]),
            str(args["model"]),
            str(args["protocol"]),
            str(args["engine"]),
        )

    def _tool_validate_spec(self, args: dict[str, Any]) -> dict[str, Any]:
        has_path = "spec_path" in args and args["spec_path"] is not None
        has_yaml = "spec_yaml" in args and args["spec_yaml"] is not None
        if has_path == has_yaml:
            raise ContractError("E_MCP_PARAM provide exactly one of spec_path or spec_yaml")
        if has_path:
            path = self._resolve_path(args["spec_path"], field="spec_path", must_exist=True)
            spec = load_spec(path)
        else:
            raw = yaml.safe_load(str(args["spec_yaml"]))
            spec = validate_spec(raw)
        return {
            "status": "VALID",
            "name": spec["name"],
            "cpu": spec["cpu"],
            "boot": spec["boot"],
            "device_count": len(spec["devices"]),
            "devices": [
                {
                    "name": d["name"],
                    "model": d["model"],
                    "base": hex(d["base"]),
                    "size": hex(d["size"]),
                    **({"irq": d["irq"]} if "irq" in d else {}),
                }
                for d in spec["devices"]
            ],
        }

    def _tool_generate_soc(self, args: dict[str, Any]) -> dict[str, Any]:
        spec_path = self._resolve_path(args["spec_path"], field="spec_path", must_exist=True)
        out_dir = self._resolve_path(args["out_dir"], field="out_dir", must_exist=False)
        engine = str(args.get("engine", "both"))
        with EmulatorNode("mcp_generate_soc") as node:
            art = node.generate_soc(spec_path, out_dir, engine=engine)
        return {
            "status": "PASSED" if art.success else "FAILED",
            "design": art.design,
            "engine": engine,
            "out_dir": art.output_dir,
            "ir_path": str(Path(art.output_dir) / "native-ir.json"),
            "ir_sha256": art.ir_sha256,
            "manifest_sha256": art.manifest_sha256,
        }

    def _tool_preflight_flow(self, args: dict[str, Any]) -> dict[str, Any]:
        flow_path = self._resolve_path(args["flow_path"], field="flow_path", must_exist=True)
        return preflight_flow(flow_path)

    def _tool_derive_domains(self, args: dict[str, Any]) -> dict[str, Any]:
        ir_path = self._resolve_path(args["ir_path"], field="ir_path", must_exist=True)
        graph = derive_domains_from_ir(read_ir(ir_path))
        return graph.to_dict()

    def _tool_synthesize_bsp(self, args: dict[str, Any]) -> dict[str, Any]:
        ir_path = self._resolve_path(args["ir_path"], field="ir_path", must_exist=True)
        out_dir = self._resolve_path(args["out_dir"], field="out_dir", must_exist=False)
        return synthesize_bsp_bundle(read_ir(ir_path), out_dir)

    def _tool_derive_noc_topology(self, args: dict[str, Any]) -> dict[str, Any]:
        ir_path = self._resolve_path(args["ir_path"], field="ir_path", must_exist=True)
        noc = derive_noc_topology(
            read_ir(ir_path),
            rows=int(args["rows"]),
            cols=int(args["cols"]),
            pio_bypass_disclosed=bool(args["pio_bypass_disclosed"]),
            coherence_protocol=str(args.get("coherence_protocol", "ruby-garnet-simple")),
            synchronization=str(args.get("synchronization", "lockstep-annotated")),
        )
        result: dict[str, Any] = {"noc": noc}
        if "calibration" in args and args["calibration"] is not None:
            cal = args["calibration"]
            result["calibration"] = evaluate_calibration_report(
                roi_name=str(cal["roi_name"]),
                reference_source=str(cal["reference_source"]),
                model_source=str(cal["model_source"]),
                reference_cycles=float(cal["reference_cycles"]),
                estimated_cycles=float(cal["estimated_cycles"]),
                max_relative_error=float(cal.get("max_relative_error", 0.15)),
            )
        return result

    def _tool_package_inspect(self, args: dict[str, Any]) -> dict[str, Any]:
        pkg_path = self._resolve_path(args["package_path"], field="package_path", must_exist=True)
        pkg, hashes = read_package(pkg_path)
        prefix = args.get("header_prefix")
        return {
            "status": "VALID",
            "capabilities": package_capabilities(pkg),
            "verified_inputs": hashes,
            "c_header": generate_package_header(pkg, prefix=prefix),
        }

    def _tool_package_build(self, args: dict[str, Any]) -> dict[str, Any]:
        pkg_path = self._resolve_path(args["package_path"], field="package_path", must_exist=True)
        out_dir = self._resolve_path(args["out_dir"], field="out_dir", must_exist=False)
        if shutil.which("c++") is None or shutil.which("pkg-config") is None:
            return {
                "status": "BLOCKED",
                "reason": "c++ or pkg-config not installed on this host",
            }
        probe = subprocess.run(
            ["pkg-config", "--exists", "systemc"], capture_output=True, check=False
        )
        if probe.returncode != 0:
            return {
                "status": "BLOCKED",
                "reason": (
                    "libsystemc-dev / pkg-config systemc not installed; "
                    "run inside the rootless Podman SDK container (`bash scripts/podman.sh mcp`)."
                ),
            }
        built = build_package(pkg_path, out_dir)
        qual = json.loads((out_dir / "qualification.json").read_text())
        return {
            "status": qual["status"],
            "package_id": built["package_id"],
            "vlnv": built["vlnv"],
            "systemc_version": built["systemc_version"],
            "binary_sha256": built["binary_sha256"],
            "conformance_vectors_passed": len(qual["actual"]),
            "conformance_checks": qual["checks"],
            "out_dir": str(out_dir),
        }

    def _tool_run_qa(self, args: dict[str, Any]) -> dict[str, Any]:
        suite = str(args["suite"])
        out_dir = self._resolve_path(args["out_dir"], field="out_dir", must_exist=False)
        report = run_qa(self.root, out_dir, suite=suite)
        verified = verify_scorecard(out_dir)
        return {
            "status": verified["status"],
            "suite": verified["suite"],
            "out_dir": str(out_dir),
            "gates": {k: v["status"] for k, v in verified["gates"].items()},
            "scorecard_path": str(out_dir / "scorecard.json"),
            "report": report,
        }

    def _tool_run_qemu(self, args: dict[str, Any]) -> dict[str, Any]:
        project_path = self._resolve_path(
            args["project_path"], field="project_path", must_exist=True
        )
        firmware_path = self._resolve_path(
            args["firmware_path"], field="firmware_path", must_exist=True
        )
        out_dir = self._resolve_path(args["out_dir"], field="out_dir", must_exist=False)
        timeout = float(args.get("timeout", 15.0))
        project = load_project(project_path)
        report = run_qemu(project, firmware_path, out_dir, timeout=timeout)
        console_log = out_dir / "stdout.log"
        return {
            **report,
            "console_output": console_log.read_text(errors="replace")
            if console_log.is_file()
            else "",
        }

    # --- MCP Resources & Prompts ---

    def list_resources(self) -> list[dict[str, Any]]:
        return [
            {
                "uri": "usp://doctor/environment",
                "name": "Emulator Environment & Toolchain Readiness",
                "description": "Live availability of QEMU, RISC-V GCC, SystemC, Verilator, Podman.",
                "mimeType": "application/json",
            },
            {
                "uri": "usp://catalog/models",
                "name": "Qualified Native Models & CPU/Bus Adapters",
                "description": "Native SoC device catalog and explicit CPU/bus adapter matrix.",
                "mimeType": "application/json",
            },
            {
                "uri": "usp://catalog/ip-readiness",
                "name": "33-Model Pinned IP Readiness Catalog",
                "description": (
                    "M8 readiness states separating qualified, adapter-needed, and missing IPs."
                ),
                "mimeType": "application/json",
            },
            {
                "uri": "usp://specs/list",
                "name": "Workspace SoC Specs, Flows & Model Packages",
                "description": "Discoverable YAML specs, native flows, and SystemC packages.",
                "mimeType": "application/json",
            },
            {
                "uri": "usp://milestones/status",
                "name": "Milestone M1-M9 Readiness Snapshot",
                "description": "Live ledger audit from upgrade/milestones.yaml.",
                "mimeType": "application/json",
            },
        ]

    def read_resource(self, uri: str) -> dict[str, Any]:
        payload: dict[str, Any]
        if uri == "usp://doctor/environment":
            payload = _probe_environment()
        elif uri == "usp://catalog/models":
            payload = {
                "native_models": native_model_catalog(),
                "cpu_adapters": [dataclasses.asdict(a) for a in CPU_ADAPTER_MATRIX],
            }
        elif uri == "usp://catalog/ip-readiness":
            payload = {
                "summary": catalog_summary(),
                "entries": [dataclasses.asdict(e) for e in IP_CATALOG],
            }
        elif uri == "usp://specs/list":
            payload = _discover_specs(self.root)
        elif uri == "usp://milestones/status":
            payload = audit_milestone_readiness(self.root)
        else:
            raise ContractError(f"E_MCP_RESOURCE unknown resource URI: {uri}")
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(payload, indent=2, sort_keys=True),
                }
            ]
        }

    def list_prompts(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "design_and_validate_soc",
                "description": (
                    "Guide an AI engineer through authoring, validating, generating, and "
                    "synthesizing BSP/domains/NoC for a custom RISC-V SoC."
                ),
                "arguments": [
                    {
                        "name": "design_goal",
                        "description": "High-level SoC architecture goal (CPUs, IPs, memory map).",
                        "required": True,
                    }
                ],
            },
            {
                "name": "audit_multiplatform_architecture",
                "description": (
                    "Audit a heterogeneous multi-platform SoC request (gem5 NoC, Chipyard, "
                    "OpenTitan, QEMU IO) honestly without fabricating unadapted hardware."
                ),
                "arguments": [
                    {
                        "name": "requested_ips",
                        "description": "Comma-separated list of target IP blocks.",
                        "required": True,
                    }
                ],
            },
            {
                "name": "package_and_test_systemc_ip",
                "description": (
                    "Inspect, synthesize headers, build, and execute conformance vectors for "
                    "a pinned SystemC/TLM-2.0 native-model-package-1 accelerator."
                ),
                "arguments": [
                    {
                        "name": "package_path",
                        "description": "Path to package.yaml inside the workspace.",
                        "required": True,
                    }
                ],
            },
        ]

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        if name == "design_and_validate_soc":
            goal = str(args.get("design_goal", "custom RISC-V SoC"))
            text = (
                f"Design and verify the following SoC using the USP Emulator MCP tools: {goal}.\n"
                "1. Call emulator_list_models and emulator_doctor to inspect qualified models.\n"
                "2. Validate the YAML spec with emulator_validate_spec.\n"
                "3. Generate IP-XACT, IR, DTS, and QEMU/gem5 targets with emulator_generate_soc.\n"
                "4. Derive domains (emulator_derive_domains) and synthesize BSP "
                "(emulator_synthesize_bsp)."
            )
        elif name == "audit_multiplatform_architecture":
            ips = str(args.get("requested_ips", "gem5 NoC, Chipyard CPU/NPU, OpenTitan, QEMU IO"))
            text = (
                f"Audit this multi-platform SoC architecture honestly: {ips}.\n"
                "1. Call emulator_list_models to check every requested IP against the 33-model "
                "readiness catalog.\n"
                "2. Call emulator_check_cpu_adapter for every requested CPU/bus adapter.\n"
                "3. Run emulator_run_qa with suite='multiplatform' and report qualified vs. "
                "BLOCKED components transparently without ISS substitution."
            )
        elif name == "package_and_test_systemc_ip":
            pkg = str(args.get("package_path", "specs/multiplatform/packages/mac_npu/package.yaml"))
            text = (
                f"Qualify the SystemC model package at {pkg}:\n"
                "1. Call emulator_package_inspect to verify SHA-256 hashes and generate the "
                "C header.\n"
                "2. Call emulator_package_build to compile with SystemC/C++17 and execute all "
                "usp-mmio32-v1 conformance vectors."
            )
        else:
            raise ContractError(f"E_MCP_PROMPT unknown prompt: {name}")
        return {
            "description": f"Workflow prompt for {name}",
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        }

    # --- JSON-RPC 2.0 Dispatcher ---

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "inputSchema": spec.input_schema,
                "annotations": {
                    "readOnlyHint": spec.read_only,
                    "destructiveHint": spec.destructive,
                    "idempotentHint": spec.idempotent,
                },
            }
            for spec in self._tool_specs
        ]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        handler = self._tool_handlers.get(name)
        if handler is None:
            raise ContractError(f"E_MCP_TOOL unknown tool: {name}")
        args = arguments if arguments is not None else {}
        if not isinstance(args, dict):
            raise ContractError("E_MCP_PARAM tool arguments must be a JSON object")
        try:
            with contextlib.redirect_stdout(sys.stderr):
                payload = handler(args)
            return {
                "content": [
                    {"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}
                ],
                "structuredContent": payload,
                "isError": False,
            }
        except (ContractError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            err = {"status": "ERROR", "tool": name, "message": str(exc)}
            return {
                "content": [{"type": "text", "text": json.dumps(err, indent=2, sort_keys=True)}],
                "structuredContent": err,
                "isError": True,
            }

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Process a single JSON-RPC 2.0 request or notification; return None for notifications."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _jsonrpc_error(None, -32600, "Invalid Request: expected jsonrpc '2.0' object")

        method = message.get("method")
        req_id = message.get("id")
        is_notification = "id" not in message

        if not isinstance(method, str):
            return (
                None
                if is_notification
                else _jsonrpc_error(req_id, -32600, "Invalid Request: missing method string")
            )

        if method == "notifications/initialized" or method.startswith("notifications/"):
            return None

        params = message.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _jsonrpc_error(req_id, -32602, "Invalid params: expected JSON object")

        try:
            result: dict[str, Any]
            if method == "initialize":
                requested = params.get("protocolVersion")
                negotiated = (
                    requested
                    if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS
                    else DEFAULT_PROTOCOL_VERSION
                )
                result = {
                    "protocolVersion": negotiated,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                        "prompts": {"listChanged": False},
                    },
                    "serverInfo": {"name": SERVER_NAME, "version": __version__},
                    "instructions": SERVER_INSTRUCTIONS,
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.list_tools()}
            elif method == "tools/call":
                tool_name = params.get("name")
                if not isinstance(tool_name, str) or not tool_name:
                    return _jsonrpc_error(
                        req_id, -32602, "Invalid params: tools/call requires name"
                    )
                if tool_name not in self._tool_handlers:
                    return _jsonrpc_error(req_id, -32602, f"Unknown tool: {tool_name}")
                result = self.call_tool(tool_name, params.get("arguments"))
            elif method == "resources/list":
                result = {"resources": self.list_resources()}
            elif method == "resources/read":
                uri = params.get("uri")
                if not isinstance(uri, str) or not uri:
                    return _jsonrpc_error(
                        req_id, -32602, "Invalid params: resources/read requires uri"
                    )
                result = self.read_resource(uri)
            elif method == "prompts/list":
                result = {"prompts": self.list_prompts()}
            elif method == "prompts/get":
                pname = params.get("name")
                if not isinstance(pname, str) or not pname:
                    return _jsonrpc_error(
                        req_id, -32602, "Invalid params: prompts/get requires name"
                    )
                result = self.get_prompt(pname, params.get("arguments"))
            else:
                return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")
        except ContractError as exc:
            return _jsonrpc_error(req_id, -32602, str(exc))
        except Exception as exc:  # pragma: no cover - defensive JSON-RPC boundary
            return _jsonrpc_error(req_id, -32603, f"Internal error: {exc}")

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def _read_stdio_message(stream: BinaryIO) -> tuple[dict[str, Any] | None, bool]:
    """Read one MCP JSON-RPC message (supports both newline-JSON and Content-Length framing).

    Returns (parsed_message_or_error_sentinel, use_content_length_framing).
    Returns (None, False) on clean EOF.
    """
    while True:
        raw_line = stream.readline()
        if not raw_line:
            return None, False
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith(b"content-length:"):
            length = int(stripped.split(b":", 1)[1].strip())
            while True:
                hdr = stream.readline()
                if not hdr or hdr in {b"\r\n", b"\n"}:
                    break
            body = stream.read(length)
            return json.loads(body.decode("utf-8")), True
        return json.loads(stripped.decode("utf-8")), False


def _write_stdio_message(
    stream: BinaryIO, payload: dict[str, Any], *, content_length: bool
) -> None:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if content_length:
        header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
        stream.write(header + encoded)
    else:
        stream.write(encoded + b"\n")
    stream.flush()


def serve_stdio(
    server: EmulatorMcpServer,
    *,
    in_stream: BinaryIO | None = None,
    out_stream: BinaryIO | None = None,
) -> int:
    """Run the MCP JSON-RPC 2.0 event loop over stdio until EOF."""
    reader = in_stream if in_stream is not None else sys.stdin.buffer
    writer = out_stream if out_stream is not None else sys.stdout.buffer
    while True:
        try:
            message, framed = _read_stdio_message(reader)
        except (ValueError, UnicodeDecodeError) as exc:
            _write_stdio_message(
                writer, _jsonrpc_error(None, -32700, f"Parse error: {exc}"), content_length=False
            )
            continue
        if message is None:
            return 0
        response = server.handle_message(message)
        if response is not None:
            _write_stdio_message(writer, response, content_length=framed)


def render_client_config(client: str, root: Path) -> dict[str, Any]:
    """Generate ready-to-use MCP client configuration JSON for popular AI hosts."""
    root_str = str(root.resolve())
    if client == "podman":
        entry = {
            "command": "bash",
            "args": [str(root.resolve() / "scripts/podman.sh"), "mcp"],
        }
    else:
        usp_bin = shutil.which("usp") or "usp"
        entry = {
            "command": usp_bin,
            "args": ["mcp", "serve", "--root", root_str],
        }
    if client == "vscode":
        return {"servers": {"usp-emulator": {"type": "stdio", **entry}}}
    return {"mcpServers": {"usp-emulator": entry}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="usp mcp")
    commands = parser.add_subparsers(dest="command", required=True)

    serve_cmd = commands.add_parser("serve", help="run MCP JSON-RPC 2.0 server over stdio")
    serve_cmd.add_argument("--root", type=Path, default=Path.cwd())
    serve_cmd.add_argument("--allow-external-paths", action="store_true")

    tools_cmd = commands.add_parser("tools", help="print MCP tools, resources, and prompts as JSON")
    tools_cmd.add_argument("--root", type=Path, default=Path.cwd())

    cfg_cmd = commands.add_parser("config", help="print ready-to-use AI client MCP JSON config")
    cfg_cmd.add_argument(
        "--client",
        choices=("claude", "cursor", "vscode", "podman"),
        default="claude",
    )
    cfg_cmd.add_argument("--root", type=Path, default=Path.cwd())

    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            server = EmulatorMcpServer(
                args.root, allow_external_paths=bool(args.allow_external_paths)
            )
            return serve_stdio(server)
        if args.command == "tools":
            server = EmulatorMcpServer(args.root)
            print(
                json.dumps(
                    {
                        "server": SERVER_NAME,
                        "version": __version__,
                        "protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
                        "tools": server.list_tools(),
                        "resources": server.list_resources(),
                        "prompts": server.list_prompts(),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        print(json.dumps(render_client_config(args.client, args.root), indent=2))
        return 0
    except (ContractError, OSError, ValueError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}), file=sys.stderr)
        return 2
