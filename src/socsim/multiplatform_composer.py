"""Generic Multi-Platform SoC Composer & Cross-Binding Engine (`multiplatform-soc-1`).

Data-driven, zero-SoC-name-branch composition platform capable of binding heterogeneous IPs
across Chipyard, gem5, QEMU (TCG/KVM direct CPU master), Verilator 5.x RTL (automatic
pin-level AXI4 / AXI4-Lite / OpenTitan TL-UL / MMIO32 adapters), Mixed-Signal TLM, and live
out-of-process `native-model-package-1` (`usp-bus-v2` & backward-compatible `usp-mmio32-v1`)
Unix-socket plugins with reverse bus-master DMA, 64-bit bursts, and quantum time sync.
"""

from __future__ import annotations

import argparse
import ast
import datetime
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from .domain import ContractError
from .execution import execute_build
from .ipxact import digest
from .native_package import build_package, read_package
from .project import keys, read_yaml
from .rtl_pin_adapter import (
    SUPPORTED_RTL_PROTOCOLS,
    emit_cpp_pin_traits,
    introspect_sv_rtl_interface,
)
from .runner import tool_version, write_json
from .unified_systemc_soc import has_unified_toolchain, resolve_unified_src_dir
from .v04_contracts import evaluate_calibration_report
from .version import __version__

CONTRACT = "multiplatform-soc-1"
ALLOWED_PROVIDERS = frozenset(
    {
        "chipyard",
        "gem5",
        "qemu",
        "opentitan",
        "verilator-rtl",
        "systemc-tlm",
        "mixed-signal-tlm",
        "usp-plugin",
    }
)
ALLOWED_BINDINGS = frozenset({"verilator-rtl", "usp-plugin", "systemc-tlm", "mixed-signal-tlm"})
ALLOWED_CPU_MASTERS = frozenset({"auto", "systemc-rv64", "qemu"})
ALLOWED_QEMU_ACCELS = frozenset({"auto", "tcg", "kvm"})
ALLOWED_KINDS = frozenset(
    {
        "ram",
        "rom",
        "sram",
        "sam_buffer",
        "pattern_gen_la",
        "l2_cache",
        "uart",
        "clint",
        "plic",
        "finisher",
        "serial_adapter",
        "dma_accel",
        "jtag_to_axi4",
        "hwacha_vec4",
        "tisar_adc_lut",
        "tuner_32ph",
        "fir_decim8",
        "polyphase_12t",
        "fft_128pt",
        "axi4_rtl",
        "axi4_lite_rtl",
        "tlul_rtl",
        "dma_burst_v2",
        "generic_plugin",
    }
)
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,47}$")


def _resolve_rel(spec_path: Path, rel_path: str) -> Path:
    p = Path(rel_path)
    if p.is_absolute():
        return p.resolve()
    cand = (spec_path.resolve().parent / p).resolve()
    if cand.exists():
        return cand
    for anc in spec_path.resolve().parents:
        c2 = (anc / p).resolve()
        if c2.exists():
            return c2
    cwd_cand = (Path.cwd().resolve() / p).resolve()
    if cwd_cand.exists():
        return cwd_cand
    opt_cand = (Path("/opt/socsim") / p).resolve()
    if opt_cand.exists():
        return opt_cand
    return cand


def _resolve_runtime_hpp() -> Path:
    here = Path(__file__).resolve().parent / "resources" / "composer" / "composer_runtime.hpp"
    if here.is_file():
        return here
    for base in (Path.cwd().resolve(), Path("/opt/socsim")):
        cand = base / "src/socsim/resources/composer/composer_runtime.hpp"
        if cand.is_file():
            return cand
    raise ContractError("E_COMPOSE_RUNTIME missing composer_runtime.hpp")


def _resolve_composer_resource(filename: str) -> Path:
    p = _resolve_runtime_hpp().parent / filename
    if not p.is_file():
        raise ContractError(f"E_COMPOSE_RUNTIME missing composer resource: {filename}")
    return p


def _check_name(val: Any, label: str) -> str:
    if not isinstance(val, str) or not NAME_RE.fullmatch(val):
        raise ContractError(f"E_COMPOSE_NAME invalid {label}: {val!r}")
    return val


def load_composed_spec(path: Path) -> dict[str, Any]:
    """Validate a `multiplatform-soc-1` specification and return normalized metadata."""
    path = path.resolve()
    data = read_yaml(path)
    if not isinstance(data, dict):
        raise ContractError("E_COMPOSE_SPEC composed SoC spec must be a mapping")
    keys(
        data,
        {
            "schema_version",
            "name",
            "clock_domains",
            "fabrics",
            "cdc_bridges",
            "cpu",
            "devices",
            "firmware",
        }
        | (set(data) & {"streams", "cross_engine", "description", "fidelity_disclosure"}),
        "composed SoC spec",
    )
    if data["schema_version"] != CONTRACT:
        raise ContractError(f"E_COMPOSE_SPEC unsupported schema_version: {data['schema_version']}")
    _check_name(data["name"], "design name")

    # 1. Clock domains
    clocks = data["clock_domains"]
    if not isinstance(clocks, list) or not 1 <= len(clocks) <= 16:
        raise ContractError("E_COMPOSE_CLOCK requires 1..16 clock_domains")
    clock_map: dict[str, int] = {}
    for clk in clocks:
        keys(clk, {"name", "freq_hz"} | (set(clk) & {"description"}), "clock domain")
        cname = _check_name(clk["name"], "clock domain")
        freq = int(clk["freq_hz"])
        if cname in clock_map or not 1_000_000 <= freq <= 10_000_000_000:
            raise ContractError(f"E_COMPOSE_CLOCK invalid/duplicate clock domain: {cname}")
        clock_map[cname] = freq

    # 2. Fabrics / Crossbars
    fabrics = data["fabrics"]
    if not isinstance(fabrics, list) or not 1 <= len(fabrics) <= 16:
        raise ContractError("E_COMPOSE_FABRIC requires 1..16 fabrics")
    fabric_map: dict[str, dict[str, Any]] = {}
    for fab in fabrics:
        keys(
            fab,
            {"name", "protocol", "clock_domain", "latency_ns"}
            | (set(fab) & {"provider", "masters", "description"}),
            "fabric",
        )
        fname = _check_name(fab["name"], "fabric")
        if fname in fabric_map or fab["clock_domain"] not in clock_map:
            raise ContractError(f"E_COMPOSE_FABRIC invalid/duplicate fabric or clock: {fname}")
        if int(fab["latency_ns"]) < 0 or int(fab["latency_ns"]) > 1000:
            raise ContractError(f"E_COMPOSE_FABRIC invalid latency_ns on {fname}")
        fabric_map[fname] = fab

    # 3. CDC Bridges
    bridges = data["cdc_bridges"]
    if not isinstance(bridges, list):
        raise ContractError("E_COMPOSE_CDC cdc_bridges must be a list")
    for br in bridges:
        keys(
            br,
            {"name", "from_fabric", "to_fabrics", "from_clock", "to_clock", "cdc_latency_ns"}
            | (set(br) & {"provider", "protocol", "description"}),
            "cdc bridge",
        )
        _check_name(br["name"], "cdc bridge")
        if br["from_fabric"] not in fabric_map:
            raise ContractError(f"E_COMPOSE_CDC unknown from_fabric: {br['from_fabric']}")
        for tf in br["to_fabrics"]:
            if tf not in fabric_map:
                raise ContractError(f"E_COMPOSE_CDC unknown to_fabric: {tf}")
        if br["from_clock"] not in clock_map or br["to_clock"] not in clock_map:
            raise ContractError(f"E_COMPOSE_CDC unknown clock domain in bridge {br['name']}")
        if int(br["cdc_latency_ns"]) < 0 or int(br["cdc_latency_ns"]) > 1000:
            raise ContractError(f"E_COMPOSE_CDC invalid cdc_latency_ns in {br['name']}")

    # 4. CPU
    cpu = data["cpu"]
    keys(
        cpu,
        {
            "name",
            "provider",
            "harts",
            "isa",
            "fabric",
            "clock_domain",
            "entry",
            "stack_top",
            "dma_buf_base",
        }
        | (
            set(cpu)
            & {
                "fpu",
                "icache_kb",
                "dcache_kb",
                "gem5_model",
                "qemu_model",
                "chipyard_config",
                "model_disclosure",
                "master_engine",
                "qemu_accel",
                "quantum_ns",
            }
        ),
        "composed cpu",
    )
    _check_name(cpu["name"], "cpu")
    if cpu["provider"] not in ALLOWED_PROVIDERS:
        raise ContractError(f"E_COMPOSE_CPU unsupported provider: {cpu['provider']}")
    if not 1 <= int(cpu["harts"]) <= 4:
        raise ContractError("E_COMPOSE_CPU harts must be in 1..4")
    if cpu["fabric"] not in fabric_map or cpu["clock_domain"] not in clock_map:
        raise ContractError("E_COMPOSE_CPU references undeclared fabric or clock_domain")
    if "master_engine" in cpu and str(cpu["master_engine"]) not in {"systemc-rv64", "qemu"}:
        raise ContractError(f"E_COMPOSE_CPU unsupported master_engine: {cpu['master_engine']}")
    if "qemu_accel" in cpu and str(cpu["qemu_accel"]) not in ALLOWED_QEMU_ACCELS:
        raise ContractError(f"E_COMPOSE_CPU unsupported qemu_accel: {cpu['qemu_accel']}")
    if "quantum_ns" in cpu:
        qns = int(cpu["quantum_ns"])
        if not 1_000 <= qns <= 10_000_000:
            raise ContractError(f"E_COMPOSE_CPU quantum_ns must be in 1000..10000000: {qns}")

    # 5. Devices (arbitrary count up to 64, arbitrary aligned sizes)
    devices = data["devices"]
    if not isinstance(devices, list) or not 5 <= len(devices) <= 64:
        raise ContractError("E_COMPOSE_DEV requires 5..64 mapped devices")
    seen_names: set[str] = set()
    seen_irqs: set[int] = set()
    windows: list[tuple[int, int, str]] = []
    dev_map: dict[str, dict[str, Any]] = {}
    for dev in devices:
        keys(
            dev,
            {"name", "kind", "provider", "binding", "fabric", "clock_domain", "base", "size"}
            | (
                set(dev)
                & {
                    "irq",
                    "rtl_module",
                    "rtl_source",
                    "rtl_busy_reg",
                    "rtl_protocol",
                    "rtl_port_map",
                    "package_yaml",
                    "plugin_protocol",
                    "vlnv",
                    "log_mmio",
                    "disclosure",
                    "role_tag",
                }
            ),
            "composed device",
        )
        dname = _check_name(dev["name"], "device")
        if dname in seen_names:
            raise ContractError(f"E_COMPOSE_DEV duplicate device name: {dname}")
        seen_names.add(dname)
        if dev["kind"] not in ALLOWED_KINDS:
            raise ContractError(f"E_COMPOSE_DEV unsupported kind: {dev['kind']}")
        if dev["provider"] not in ALLOWED_PROVIDERS:
            raise ContractError(f"E_COMPOSE_DEV unsupported provider: {dev['provider']}")
        if dev["binding"] not in ALLOWED_BINDINGS:
            raise ContractError(f"E_COMPOSE_DEV unsupported binding: {dev['binding']}")
        if dev["fabric"] not in fabric_map or dev["clock_domain"] not in clock_map:
            raise ContractError(f"E_COMPOSE_DEV {dname} references undeclared fabric/clock")
        base, size = int(dev["base"]), int(dev["size"])
        if base < 0 or size <= 0 or (base % 4096) != 0 or (size % 4096) != 0:
            raise ContractError(f"E_COMPOSE_DEV {dname} requires 4 KiB-aligned base/size")
        windows.append((base, base + size, dname))
        if "irq" in dev:
            irq = int(dev["irq"])
            if not 1 <= irq <= 31 or irq in seen_irqs:
                raise ContractError(f"E_COMPOSE_DEV invalid/duplicate IRQ {irq} on {dname}")
            seen_irqs.add(irq)
        if dev["binding"] == "verilator-rtl":
            if "rtl_module" not in dev or "rtl_source" not in dev:
                raise ContractError(f"E_COMPOSE_RTL {dname} requires rtl_module and rtl_source")
            rtl_p = _resolve_rel(path, str(dev["rtl_source"]))
            if not rtl_p.is_file():
                raise ContractError(f"E_COMPOSE_RTL missing SystemVerilog source: {rtl_p}")
            req_proto = str(dev.get("rtl_protocol", "auto"))
            if req_proto not in SUPPORTED_RTL_PROTOCOLS:
                raise ContractError(
                    f"E_COMPOSE_RTL_PROTO {dname} unsupported rtl_protocol: {req_proto}"
                )
            if dev["kind"] != "l2_cache":
                overrides = dev.get("rtl_port_map")
                if overrides is not None and not isinstance(overrides, dict):
                    raise ContractError(f"E_COMPOSE_RTL {dname} rtl_port_map must be a mapping")
                introspect_sv_rtl_interface(
                    rtl_p,
                    str(dev["rtl_module"]),
                    requested_protocol=req_proto,
                    port_overrides=overrides,
                )
        if dev["binding"] == "usp-plugin":
            if "package_yaml" not in dev:
                raise ContractError(f"E_COMPOSE_PLUGIN {dname} requires package_yaml")
            pkg_p = _resolve_rel(path, str(dev["package_yaml"]))
            if not pkg_p.is_file():
                raise ContractError(f"E_COMPOSE_PLUGIN missing package manifest: {pkg_p}")
            pkg, _ = read_package(pkg_p)
            if "plugin_protocol" in dev and dev["plugin_protocol"] != pkg["interface"]["protocol"]:
                raise ContractError(
                    f"E_COMPOSE_PLUGIN {dname} plugin_protocol mismatch with package.yaml"
                )
        dev_map[dname] = dev

    windows.sort()
    for (_b0, e0, n0), (b1, _e1, n1) in zip(windows, windows[1:], strict=False):
        if b1 < e0:
            raise ContractError(f"E_COMPOSE_MAP overlapping address windows: {n0} and {n1}")

    for req_kind in ("ram", "clint", "plic", "uart", "finisher"):
        if not any(d["kind"] == req_kind for d in devices):
            raise ContractError(f"E_COMPOSE_DEV missing required core device kind: {req_kind}")

    # 6. AXI4-Stream Dataflow DAG Validation (must be acyclic!)
    streams = data.get("streams", [])
    if not isinstance(streams, list):
        raise ContractError("E_COMPOSE_STREAM streams must be a list")
    adj: dict[str, list[str]] = defaultdict(list)
    indeg: dict[str, int] = defaultdict(int)
    stream_nodes: set[str] = set()
    for st in streams:
        keys(
            st,
            {"name", "from", "to", "width_bits", "clock_domain"}
            | (set(st) & {"protocol", "sam_tap", "mux_probe"}),
            "axi4 stream",
        )
        _check_name(st["name"], "stream")
        src, dst = str(st["from"]), str(st["to"])
        if src not in dev_map or dst not in dev_map or src == dst:
            raise ContractError(f"E_COMPOSE_STREAM invalid endpoints {src} -> {dst}")
        if st["clock_domain"] not in clock_map:
            raise ContractError(f"E_COMPOSE_STREAM unknown clock domain: {st['clock_domain']}")
        if "sam_tap" in st and st["sam_tap"] not in dev_map:
            raise ContractError(f"E_COMPOSE_STREAM unknown sam_tap: {st['sam_tap']}")
        if "mux_probe" in st and st["mux_probe"] not in dev_map:
            raise ContractError(f"E_COMPOSE_STREAM unknown mux_probe: {st['mux_probe']}")
        adj[src].append(dst)
        indeg[dst] += 1
        stream_nodes.update({src, dst})

    q = deque(n for n in stream_nodes if indeg[n] == 0)
    visited = 0
    while q:
        u = q.popleft()
        visited += 1
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    if visited != len(stream_nodes):
        raise ContractError("E_COMPOSE_STREAM_CYCLE AXI4-Stream dataflow graph contains a cycle")

    # 7. Firmware contract
    fw = data["firmware"]
    keys(
        fw,
        {"positive_src", "negative_src", "expected_markers"}
        | (set(fw) & {"qemu_crosscheck_src", "qemu_expected_markers", "isa"}),
        "composed firmware",
    )
    for fk in ("positive_src", "negative_src"):
        fp = _resolve_rel(path, str(fw[fk]))
        if not fp.is_file():
            raise ContractError(f"E_COMPOSE_FW missing firmware source: {fp}")
    if "qemu_crosscheck_src" in fw:
        qp = _resolve_rel(path, str(fw["qemu_crosscheck_src"]))
        if not qp.is_file():
            raise ContractError(f"E_COMPOSE_FW missing QEMU crosscheck source: {qp}")
    if not isinstance(fw["expected_markers"], list) or not fw["expected_markers"]:
        raise ContractError("E_COMPOSE_FW expected_markers must be non-empty")

    return data


def _render_soc_map_h(spec: dict[str, Any]) -> str:
    cpu = spec["cpu"]
    lines = [
        f"/* Auto-generated multi-platform SoC map header for {spec['name']}; DO NOT EDIT. */",
        "#ifndef USP_COMPOSED_SOC_MAP_H",
        "#define USP_COMPOSED_SOC_MAP_H",
        f"#define SOC_HART_COUNT {int(cpu['harts'])}U",
        f"#define SOC_CPU_ENTRY 0x{int(cpu['entry']):08X}ULL",
        f"#define SOC_STACK_TOP 0x{int(cpu['stack_top']):08X}ULL",
        f"#define SOC_DMA_BUF_BASE 0x{int(cpu['dma_buf_base']):08X}ULL",
    ]
    for clk in spec["clock_domains"]:
        ck = str(clk["name"]).upper()
        lines.append(f"#define SOC_CLK_{ck}_HZ {int(clk['freq_hz'])}ULL")
    for dev in spec["devices"]:
        dk = str(dev["name"]).upper()
        lines.append(f"#define SOC_{dk}_BASE 0x{int(dev['base']):08X}ULL")
        lines.append(f"#define SOC_{dk}_SIZE 0x{int(dev['size']):08X}ULL")
        if "irq" in dev:
            lines.append(f"#define SOC_{dk}_IRQ {int(dev['irq'])}U")
        kind = str(dev["kind"])
        if kind == "uart":
            lines.append(f"#define SOC_PRIMARY_UART_BASE 0x{int(dev['base']):08X}ULL")
            lines.append(f"#define SOC_PRIMARY_UART_IRQ {int(dev.get('irq', 10))}U")
        elif kind == "finisher":
            lines.append(f"#define SOC_FINISHER_BASE 0x{int(dev['base']):08X}ULL")
        elif kind == "clint":
            lines.append(f"#define SOC_CLINT_BASE 0x{int(dev['base']):08X}ULL")
        elif kind == "plic":
            lines.append(f"#define SOC_PLIC_BASE 0x{int(dev['base']):08X}ULL")
        elif kind == "ram":
            lines.append(f"#define SOC_MAIN_RAM_BASE 0x{int(dev['base']):08X}ULL")
            lines.append(f"#define SOC_MAIN_RAM_SIZE 0x{int(dev['size']):08X}ULL")
    lines.append("#endif /* USP_COMPOSED_SOC_MAP_H */")
    return "\n".join(lines) + "\n"


def _render_linker_script(spec: dict[str, Any]) -> str:
    cpu = spec["cpu"]
    ram = next(d for d in spec["devices"] if d["kind"] == "ram")
    return (
        "OUTPUT_ARCH(riscv)\n"
        "ENTRY(_start)\n"
        "MEMORY {\n"
        f"  RAM (rwx) : ORIGIN = 0x{int(ram['base']):08X}, LENGTH = 0x{int(ram['size']):08X}\n"
        "}\n"
        "SECTIONS {\n"
        f"  . = 0x{int(cpu['entry']):08X};\n"
        "  .text : { *(.text.init) *(.text*) }\n"
        "  .rodata : ALIGN(8) { *(.rodata*) }\n"
        "  .data : ALIGN(8) { *(.data*) }\n"
        "  .bss : ALIGN(8) {\n"
        "    _bss_start = .;\n"
        "    *(.bss*) *(COMMON)\n"
        "    . = ALIGN(8);\n"
        "    _bss_end = .;\n"
        "  }\n"
        f"  PROVIDE(_stack_top = 0x{int(cpu['stack_top']):08X});\n"
        f"  PROVIDE(_hart1_stack_top = 0x{int(cpu['stack_top']) - 0x10000:08X});\n"
        f"  PROVIDE(_hart2_stack_top = 0x{int(cpu['stack_top']) - 0x20000:08X});\n"
        f"  PROVIDE(_hart3_stack_top = 0x{int(cpu['stack_top']) - 0x30000:08X});\n"
        "}\n"
    )


def _render_qemu_linker_script(spec: dict[str, Any]) -> str:
    """Emit linker script placing guest in SoC RAM and QEMU M-mode bridge in PMP-isolated RAM."""
    cpu = spec["cpu"]
    ram = next(d for d in spec["devices"] if d["kind"] == "ram")
    ram_base = int(ram["base"])
    ram_size = int(ram["size"])
    bridge_base = ram_base + ram_size
    bridge_size = 0x00100000  # 1 MiB isolated M-mode PMP/VirtIO bridge region
    return (
        "OUTPUT_ARCH(riscv)\n"
        "ENTRY(_usp_qemu_entry)\n"
        "MEMORY {\n"
        f"  RAM        (rwx) : ORIGIN = 0x{ram_base:08X}, LENGTH = 0x{ram_size:08X}\n"
        f"  BRIDGE_RAM (rwx) : ORIGIN = 0x{bridge_base:08X}, LENGTH = 0x{bridge_size:08X}\n"
        "}\n"
        "SECTIONS {\n"
        f"  . = 0x{int(cpu['entry']):08X};\n"
        "  .text : {\n"
        "    *(.text.init)\n"
        "    *(EXCLUDE_FILE(*qemu_usp_bridge*.o) .text*)\n"
        "  } > RAM\n"
        "  .rodata : ALIGN(8) {\n"
        "    *(EXCLUDE_FILE(*qemu_usp_bridge*.o) .rodata*)\n"
        "    *(EXCLUDE_FILE(*qemu_usp_bridge*.o) .srodata*)\n"
        "  } > RAM\n"
        "  .data : ALIGN(8) {\n"
        "    *(EXCLUDE_FILE(*qemu_usp_bridge*.o) .data*)\n"
        "    *(EXCLUDE_FILE(*qemu_usp_bridge*.o) .sdata*)\n"
        "  } > RAM\n"
        "  .bss : ALIGN(8) {\n"
        "    _bss_start = .;\n"
        "    *(EXCLUDE_FILE(*qemu_usp_bridge*.o) .bss*)\n"
        "    *(EXCLUDE_FILE(*qemu_usp_bridge*.o) .sbss*)\n"
        "    *(EXCLUDE_FILE(*qemu_usp_bridge*.o) COMMON)\n"
        "    . = ALIGN(8);\n"
        "    _bss_end = .;\n"
        "  } > RAM\n"
        "  .usp_bridge.text : ALIGN(16) {\n"
        "    *(.usp_bridge.init)\n"
        "    *(.usp_bridge.text)\n"
        "    *qemu_usp_bridge*.o(.text* .rodata* .srodata*)\n"
        "  } > BRIDGE_RAM\n"
        "  .usp_bridge.data : ALIGN(16) {\n"
        "    *qemu_usp_bridge*.o(.data* .sdata*)\n"
        "  } > BRIDGE_RAM\n"
        "  .usp_bridge.bss : ALIGN(16) {\n"
        "    _usp_bridge_bss_start = .;\n"
        "    *qemu_usp_bridge*.o(.bss* .sbss* COMMON)\n"
        "    . = ALIGN(16);\n"
        "    _usp_bridge_bss_end = .;\n"
        "  } > BRIDGE_RAM\n"
        f"  PROVIDE(_usp_bridge_stack_top = 0x{bridge_base + bridge_size - 0x1000:08X});\n"
        f"  PROVIDE(_stack_top = 0x{int(cpu['stack_top']):08X});\n"
        f"  PROVIDE(_hart1_stack_top = 0x{int(cpu['stack_top']) - 0x10000:08X});\n"
        f"  PROVIDE(_hart2_stack_top = 0x{int(cpu['stack_top']) - 0x20000:08X});\n"
        f"  PROVIDE(_hart3_stack_top = 0x{int(cpu['stack_top']) - 0x30000:08X});\n"
        "}\n"
    )


def _device_rtl_meta(dev: dict[str, Any], spec_path: Path) -> dict[str, Any] | None:
    if dev["binding"] != "verilator-rtl" or dev["kind"] == "l2_cache":
        return None
    rtl_p = _resolve_rel(spec_path, str(dev["rtl_source"]))
    return introspect_sv_rtl_interface(
        rtl_p,
        str(dev["rtl_module"]),
        requested_protocol=str(dev.get("rtl_protocol", "auto")),
        port_overrides=dev.get("rtl_port_map"),
    )


def _device_plugin_info(
    dev: dict[str, Any], spec_path: Path
) -> tuple[dict[str, Any], dict[str, str]]:
    pkg_p = _resolve_rel(spec_path, str(dev["package_yaml"]))
    return read_package(pkg_p)


def _render_binding_lock(spec: dict[str, Any], spec_path: Path) -> dict[str, Any]:
    entries = []
    for dev in spec["devices"]:
        prov = str(dev["provider"])
        bind = str(dev["binding"])
        vlnv = str(dev.get("vlnv", f"socsim.{prov}:{dev['kind']}:{dev['name']}:1.0"))
        entry: dict[str, Any] = {
            "instance": dev["name"],
            "kind": dev["kind"],
            "vlnv": vlnv,
            "provider": prov,
            "binding": bind,
            "fabric": dev["fabric"],
            "clock_domain": dev["clock_domain"],
            "base": hex(int(dev["base"])),
            "size": hex(int(dev["size"])),
            "irq": int(dev["irq"]) if "irq" in dev else None,
            "disclosure": dev.get("disclosure", ""),
        }
        if bind == "verilator-rtl":
            rtl_p = _resolve_rel(spec_path, str(dev["rtl_source"]))
            entry["rtl_module"] = dev["rtl_module"]
            entry["rtl_sha256"] = digest(rtl_p)
            rmeta = _device_rtl_meta(dev, spec_path)
            if rmeta is not None:
                entry["rtl_protocol"] = rmeta["protocol"]
                entry["rtl_data_bits"] = rmeta["data_bits"]
                entry["rtl_reset_active_low"] = rmeta["reset_active_low"]
                entry["rtl_ports"] = rmeta["ports"]
        elif bind == "usp-plugin":
            pkg_p = _resolve_rel(spec_path, str(dev["package_yaml"]))
            pkg, hashes = read_package(pkg_p)
            entry["package_id"] = pkg["id"]
            entry["package_vlnv"] = pkg["vlnv"]
            entry["package_protocol"] = pkg["interface"]["protocol"]
            entry["package_sha256"] = digest(pkg_p)
            entry["package_source_hashes"] = hashes
        entries.append(entry)

    provider_counts: dict[str, int] = defaultdict(int)
    for e in entries:
        provider_counts[e["provider"]] += 1
    provider_counts[str(spec["cpu"]["provider"])] += 1

    return {
        "contract": "usp-multiplatform-binding-lock-1",
        "platform_version": __version__,
        "design": spec["name"],
        "spec_sha256": digest(spec_path),
        "clock_domains": spec["clock_domains"],
        "fabrics": [
            {
                "name": f["name"],
                "protocol": f["protocol"],
                "clock_domain": f["clock_domain"],
                "latency_ns": int(f["latency_ns"]),
                "provider": f.get("provider", "systemc-tlm"),
            }
            for f in spec["fabrics"]
        ],
        "cdc_bridges": spec["cdc_bridges"],
        "cpu_binding": spec["cpu"],
        "provider_summary": dict(sorted(provider_counts.items())),
        "device_bindings": entries,
        "axi4_streams": spec.get("streams", []),
    }


def _render_ipxact_design_xml(spec: dict[str, Any]) -> str:
    """Emit IEEE 1685-2022 IP-XACT design metadata for all composed heterogeneous instances."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<ipxact:design xmlns:ipxact="http://www.accellera.org/XMLSchema/IPXACT/1685-2022">',
        "  <ipxact:vendor>socsim.composed</ipxact:vendor>",
        "  <ipxact:library>multiplatform</ipxact:library>",
        f"  <ipxact:name>{spec['name']}_design</ipxact:name>",
        "  <ipxact:version>1.0</ipxact:version>",
        "  <ipxact:componentInstances>",
        "    <ipxact:componentInstance>",
        f"      <ipxact:instanceName>{spec['cpu']['name']}</ipxact:instanceName>",
        f'      <ipxact:componentRef vendor="socsim.{spec["cpu"]["provider"]}" '
        f'library="cpu" name="{spec["cpu"]["name"]}" version="1.0"/>',
        "    </ipxact:componentInstance>",
    ]
    for dev in spec["devices"]:
        lines.extend(
            [
                "    <ipxact:componentInstance>",
                f"      <ipxact:instanceName>{dev['name']}</ipxact:instanceName>",
                f'      <ipxact:componentRef vendor="socsim.{dev["provider"]}" '
                f'library="{dev["kind"]}" name="{dev["name"]}" version="1.0"/>',
                "    </ipxact:componentInstance>",
            ]
        )
    lines.extend(["  </ipxact:componentInstances>", "</ipxact:design>"])
    return "\n".join(lines) + "\n"


def _render_qemu_dts(spec: dict[str, Any]) -> str:
    cpu = spec["cpu"]
    ram = next(d for d in spec["devices"] if d["kind"] == "ram")
    clint = next(d for d in spec["devices"] if d["kind"] == "clint")
    plic = next(d for d in spec["devices"] if d["kind"] == "plic")
    uart = next(d for d in spec["devices"] if d["kind"] == "uart")
    lines = [
        "/dts-v1/;",
        "",
        "/ {",
        "    #address-cells = <2>;",
        "    #size-cells = <2>;",
        f'    compatible = "socsim,{spec["name"].lower()}";',
        f'    model = "USP Composed Multi-Platform {spec["name"]}";',
        "",
        "    chosen {",
        f'        stdout-path = "/soc/serial@{int(uart["base"]):x}";',
        "    };",
        "",
        "    cpus {",
        "        #address-cells = <1>;",
        "        #size-cells = <0>;",
        "        timebase-frequency = <10000000>;",
        "        cpu0: cpu@0 {",
        '            device_type = "cpu";',
        '            compatible = "riscv";',
        "            reg = <0>;",
        f'            riscv,isa = "{cpu["isa"]}";',
        '            mmu-type = "riscv,sv39";',
        "            cpu0_intc: interrupt-controller {",
        "                #interrupt-cells = <1>;",
        "                interrupt-controller;",
        '                compatible = "riscv,cpu-intc";',
        "            };",
        "        };",
        "    };",
        "",
        f"    memory@{int(ram['base']):x} {{",
        '        device_type = "memory";',
        f"        reg = <0x0 0x{int(ram['base']):x} 0x0 0x{int(ram['size']):x}>;",
        "    };",
        "",
        "    soc {",
        "        #address-cells = <2>;",
        "        #size-cells = <2>;",
        '        compatible = "simple-bus";',
        "        ranges;",
        f"        clint@{int(clint['base']):x} {{",
        '            compatible = "sifive,clint0";',
        f"            reg = <0x0 0x{int(clint['base']):x} 0x0 0x{int(clint['size']):x}>;",
        "            interrupts-extended = <&cpu0_intc 3 &cpu0_intc 7>;",
        "        };",
        f"        plic: interrupt-controller@{int(plic['base']):x} {{",
        "            #interrupt-cells = <1>;",
        "            interrupt-controller;",
        '            compatible = "sifive,plic-1.0.0";',
        f"            reg = <0x0 0x{int(plic['base']):x} 0x0 0x{int(plic['size']):x}>;",
        "            riscv,ndev = <31>;",
        "            interrupts-extended = <&cpu0_intc 11 &cpu0_intc 9>;",
        "        };",
        f"        serial@{int(uart['base']):x} {{",
        '            compatible = "ns16550a";',
        f"            reg = <0x0 0x{int(uart['base']):x} 0x0 0x{int(uart['size']):x}>;",
        f"            interrupts = <{int(uart.get('irq', 10))}>;",
        "            interrupt-parent = <&plic>;",
        "        };",
    ]
    for dev in spec["devices"]:
        if dev["kind"] in {"ram", "rom", "clint", "plic", "uart"}:
            continue
        lines.extend(
            [
                f"        {dev['name']}@{int(dev['base']):x} {{",
                f'            compatible = "socsim,{dev["kind"]}";',
                f"            reg = <0x0 0x{int(dev['base']):x} 0x0 0x{int(dev['size']):x}>;",
                *(
                    [
                        f"            interrupts = <{int(dev['irq'])}>;",
                        "            interrupt-parent = <&plic>;",
                    ]
                    if "irq" in dev
                    else []
                ),
                "        };",
            ]
        )
    lines.extend(["    };", "};"])
    return "\n".join(lines) + "\n"


def _render_gem5_system_py(spec: dict[str, Any], spec_path: Path | None = None) -> str:
    """Emit a gem5 architectural system script binding CPU/L2/SRAM + usp-bus-v2/v1 IPs."""
    cpu = spec["cpu"]
    ram = next(d for d in spec["devices"] if d["kind"] == "ram")
    gem5_cpu = str(cpu.get("gem5_model", "RiscvTimingSimpleCPU")).removeprefix("gem5.")
    quantum_ns = int(cpu.get("quantum_ns", 100_000))
    core_clk = next(
        (int(c["freq_hz"]) for c in spec["clock_domains"] if c["name"] == cpu["clock_domain"]),
        800_000_000,
    )
    plugin_devs = [
        d
        for d in spec["devices"]
        if d["binding"] in {"verilator-rtl", "usp-plugin", "mixed-signal-tlm"}
        and d["kind"] != "l2_cache"
    ]
    lines = [
        f'"""Auto-generated gem5 multi-platform system graph for {spec["name"]}.',
        "Binds gem5 CPU / 512KB L2$ / On-Chip SRAM with out-of-process SystemC/Verilator",
        'peripherals via `NativeSocDevice` (`usp-bus-v2` + `usp-mmio32-v1` quantum sync)."""',
        "",
        "import argparse",
        "import json",
        "import m5",
        "from m5.objects import (",
        "    AddrRange,",
        "    Cache,",
        "    Clint,",
        "    HiFive,",
        "    NativeSocDevice,",
        "    Plic,",
        f"    {gem5_cpu},",
        "    RiscvUart8250,",
        "    Root,",
        "    SimpleMemory,",
        "    SrcClockDomain,",
        "    System,",
        "    SystemXBar,",
        "    VoltageDomain,",
        ")",
        "",
        f'DESIGN_NAME = "{spec["name"]}"',
        f"CORE_CLOCK_MHZ = {core_clk // 1_000_000}",
        f"QUANTUM_SYNC_NS = {quantum_ns}",
        f"MAIN_RAM_BASE = 0x{int(ram['base']):08X}",
        f"MAIN_RAM_SIZE = 0x{int(ram['size']):08X}",
        "PLUGIN_MMIO_DEVICES = [",
    ]
    for d in plugin_devs:
        proto = "usp-mmio32-v1"
        dma_en = False
        if d["binding"] == "usp-plugin" and spec_path is not None:
            pkg, _ = _device_plugin_info(d, spec_path)
            proto = str(pkg["interface"]["protocol"])
            dma_en = bool(pkg["interface"].get("supports_dma_master", False))
        elif d["binding"] == "verilator-rtl" and spec_path is not None:
            rmeta = _device_rtl_meta(d, spec_path)
            if rmeta and rmeta["protocol"] in {"axi4", "axi4-lite", "tl-ul"}:
                proto = "usp-bus-v2"
        lines.append(
            f'    {{"name": "{d["name"]}", "kind": "{d["kind"]}", '
            f'"provider": "{d["provider"]}", "protocol": "{proto}", '
            f'"dma_enabled": {dma_en}, "base": 0x{int(d["base"]):08X}, '
            f'"size": 0x{int(d["size"]):08X}, "irq": {int(d.get("irq", 0))}}},'
        )
    lines.extend(
        [
            "]",
            "",
            "",
            "def build_system(firmware_elf: str, sockets: dict[str, str]) -> System:",
            "    system = System()",
            "    system.clk_domain = SrcClockDomain(",
            '        clock=f"{CORE_CLOCK_MHZ}MHz", voltage_domain=VoltageDomain()',
            "    )",
            '    system.mem_mode = "timing"',
            "    system.mem_ranges = [AddrRange(MAIN_RAM_BASE, size=MAIN_RAM_SIZE)]",
            "    system.membus = SystemXBar()",
            f"    system.cpu = {gem5_cpu}()",
            "    system.cpu.createInterruptController()",
            "    system.l2cache = Cache(",
            '        size="512kB", assoc=8, tag_latency=8, data_latency=8, response_latency=8,',
            "        mshrs=16, tgts_per_mshr=8,",
            "    )",
            "    system.l2bus = SystemXBar()",
            "    system.cpu.icache_port = system.l2bus.cpu_side_ports",
            "    system.cpu.dcache_port = system.l2bus.cpu_side_ports",
            "    system.l2cache.cpu_side = system.l2bus.mem_side_ports",
            "    system.l2cache.mem_side = system.membus.cpu_side_ports",
            "    system.main_mem = SimpleMemory(range=system.mem_ranges[0])",
            "    system.main_mem.port = system.membus.mem_side_ports",
            "    system.platform = HiFive()",
            "    system.platform.clint = Clint()",
            "    system.platform.plic = Plic()",
            "    system.platform.uart = RiscvUart8250()",
            "    ext_devs = []",
            "    for dev in PLUGIN_MMIO_DEVICES:",
            '        sock = sockets.get(dev["name"], "")',
            "        if sock:",
            "            ext_devs.append(",
            "                NativeSocDevice(",
            '                    platform=system.platform, base=dev["base"], span=dev["size"],',
            '                    kind=0, irq=dev["irq"], socket_path=sock,',
            '                    protocol=dev["protocol"],',
            (
                "                    quantum_ns=QUANTUM_SYNC_NS "
                'if dev["protocol"] == "usp-bus-v2" else 0,'
            ),
            '                    dma_enabled=bool(dev["dma_enabled"]),',
            "                )",
            "            )",
            "    system.usp_plugins = ext_devs",
            "    for obj in system.usp_plugins:",
            "        obj.pio = system.membus.mem_side_ports",
            "    return system",
            "",
        ]
    )
    code = "\n".join(lines)
    ast.parse(code)  # Fail-fast syntax check during generation!
    return code


def _render_chipyard_scala(spec: dict[str, Any]) -> str:
    """Emit a Chipyard/Rocket-Chip Scala configuration fragment matching the composed SoC."""
    cpu = spec["cpu"]
    ram = next(d for d in spec["devices"] if d["kind"] == "ram")
    has_hwacha = any(d["kind"] == "hwacha_vec4" for d in spec["devices"])
    has_jtag = any(d["kind"] == "jtag_to_axi4" for d in spec["devices"])
    core_mhz = next(
        (
            int(c["freq_hz"]) // 1_000_000
            for c in spec["clock_domains"]
            if c["name"] == cpu["clock_domain"]
        ),
        800,
    )
    dsp_clk = next(
        (
            int(c["freq_hz"]) // 1_000_000
            for c in spec["clock_domains"]
            if c["name"] != cpu["clock_domain"]
        ),
        250,
    )
    cfg_name = f"{spec['name']}ChipyardConfig"
    fragments = [
        f"  new freechips.rocketchip.subsystem.WithNRocketCores({int(cpu['harts'])}) ++",
        *(["  new hwacha.DefaultHwachaConfig ++"] if has_hwacha else []),
        "  new freechips.rocketchip.subsystem.WithInclusiveCache(nWays = 8, capacityKB = 512) ++",
        f"  new freechips.rocketchip.subsystem.WithExtMemSize(0x{int(ram['size']):X}L) ++",
        *(["  new chipyard.config.WithJTAGToAXI4Bridge ++"] if has_jtag else []),
        (
            "  new chipyard.clocking.WithClockGroupsCombinedByName("
            f'("core_clk", {core_mhz}), ("dsp_clk", {dsp_clk})) ++'
        ),
        "  new chipyard.config.AbstractConfig",
    ]
    return (
        f"// Auto-generated Chipyard configuration fragment for {spec['name']}.\n"
        "// NOTE: Source-level Chisel/Scala configuration targeting pinned Chipyard generators;\n"
        "// requires upstream sbt/Mill + FIRRTL/CIRCT elaboration to synthesize full Chisel RTL.\n"
        "package chipyard\n\n"
        "import org.chipsalliance.cde.config.Config\n\n"
        f"class {cfg_name} extends Config(\n" + "\n".join(fragments) + "\n)\n"
    )


def _render_generated_cpp(
    spec: dict[str, Any],
    *,
    spec_path: Path | None = None,
    use_verilator: bool,
    use_live_plugins: bool,
) -> str:
    """Emit a data-driven SystemC/TLM-2.0 + QEMU Master + Verilator AXI4/TL-UL + v2 Plugin top."""
    clock_idx = {str(c["name"]): i for i, c in enumerate(spec["clock_domains"])}
    fabric_idx = {str(f["name"]): i for i, f in enumerate(spec["fabrics"])}
    cdc_lat = max((int(b["cdc_latency_ns"]) for b in spec["cdc_bridges"]), default=12)
    default_quantum_ns = int(spec["cpu"].get("quantum_ns", 100_000))

    lines = [
        "// Auto-generated multi-platform SystemC/TLM-2.0 top-level; DO NOT EDIT BY HAND.",
        "#include <systemc>",
        "#include <tlm>",
    ]
    rtl_devices = [d for d in spec["devices"] if d["binding"] == "verilator-rtl"]
    has_l2_rtl = use_verilator and any(d["kind"] == "l2_cache" for d in rtl_devices)
    rtl_meta_by_dev: dict[str, dict[str, Any]] = {}
    if spec_path is not None:
        for d in rtl_devices:
            rmeta = _device_rtl_meta(d, spec_path)
            if rmeta is not None:
                rtl_meta_by_dev[str(d["name"])] = rmeta

    plugin_proto_by_dev: dict[str, str] = {}
    if spec_path is not None:
        for d in spec["devices"]:
            if d["binding"] == "usp-plugin":
                pkg, _ = _device_plugin_info(d, spec_path)
                plugin_proto_by_dev[str(d["name"])] = str(pkg["interface"]["protocol"])

    if use_verilator and rtl_devices:
        lines.append('#include "verilated.h"')
        for d in rtl_devices:
            lines.append(f'#include "V{d["rtl_module"]}.h"')
        if has_l2_rtl:
            lines.append("#define USP_HAS_VL2_CACHE_RTL 1")

    lines.extend(['#include "composer_runtime.hpp"', '#include "soc_map.h"', ""])

    # Auto-emit C++ PinTraits for every AXI4 / AXI4-Lite / OpenTitan TL-UL RTL block!
    if use_verilator:
        for d in rtl_devices:
            dname = str(d["name"])
            rmeta = rtl_meta_by_dev.get(dname)
            if rmeta and rmeta["protocol"] in {"axi4", "axi4-lite", "tl-ul"}:
                lines.extend(emit_cpp_pin_traits(f"Traits_{dname}", rmeta))
                lines.append("")

    lines.extend(
        [
            "int sc_main(int argc, char **argv) {",
            "    if (argc < 5) {",
            '        std::cerr << "Usage: composed-soc-emu <elf> <console> <trace> <summary> '
            "[max_steps] [--vcd-out PATH] [--plugin name=sock ...] "
            "[--cpu-master systemc-rv64|qemu] [--qemu-sock PATH] [--shared-ram PATH] "
            '[--quantum-ns N]\\n";',
            "        return 2;",
            "    }",
        ]
    )
    if use_verilator and rtl_devices:
        lines.append("    Verilated::commandArgs(1, argv);")
    lines.extend(
        [
            "    const std::string elf_path = argv[1];",
            "    const std::string console_path = argv[2];",
            "    const std::string trace_path = argv[3];",
            "    const std::string summary_path = argv[4];",
            "    uint64_t max_steps = 800000ULL;",
            '    std::string cpu_master = "systemc-rv64";',
            "    std::string qemu_sock_path;",
            "    std::string shared_ram_path;",
            f"    uint64_t quantum_ns = {default_quantum_ns}ULL;",
            "    std::string vcd_path;",
            "    std::vector<std::pair<std::string, std::string>> plugin_socks;",
            "    for (int i = 5; i < argc; ++i) {",
            "        std::string arg = argv[i];",
            '        if (arg == "--vcd-out" && i + 1 < argc) vcd_path = argv[++i];',
            '        else if (arg == "--cpu-master" && i + 1 < argc) cpu_master = argv[++i];',
            '        else if (arg == "--qemu-sock" && i + 1 < argc) qemu_sock_path = argv[++i];',
            '        else if (arg == "--shared-ram" && i + 1 < argc) shared_ram_path = argv[++i];',
            '        else if (arg == "--quantum-ns" && i + 1 < argc) '
            "quantum_ns = std::strtoull(argv[++i], nullptr, 10);",
            '        else if (arg == "--plugin" && i + 1 < argc) {',
            "            std::string kv = argv[++i];",
            "            size_t eq = kv.find('=');",
            "            if (eq != std::string::npos) {",
            "                plugin_socks.emplace_back(kv.substr(0, eq), kv.substr(eq + 1));",
            "            }",
            "        } else if (i == 5) max_steps = std::strtoull(arg.c_str(), nullptr, 10);",
            "    }",
            "    auto get_sock = [&](const std::string &n) -> std::string {",
            "        for (const auto &kv : plugin_socks) if (kv.first == n) return kv.second;",
            '        return "";',
            "    };",
            "    (void)get_sock;",
            "    (void)quantum_ns;",
            "",
            "    sc_core::sc_set_time_resolution(1, sc_core::SC_PS);",
            "    usp::VcdWriter vcd;",
            "    vcd.open(vcd_path);",
            f'    usp::MultiFabricInterconnect xbar("composed_xbar", trace_path, {cdc_lat}u);',
            "    xbar.vcd = &vcd;",
        ]
    )
    for fab in spec["fabrics"]:
        cid = clock_idx[str(fab["clock_domain"])]
        lines.append(
            f'    xbar.fabrics.push_back({{"{fab["name"]}", "{fab["protocol"]}", {cid}, '
            f"{int(fab['latency_ns'])}u, 0ULL}});"
        )

    cpu = spec["cpu"]
    cpu_fid = fabric_idx[str(cpu["fabric"])]
    cpu_cid = clock_idx[str(cpu["clock_domain"])]
    lines.extend(
        [
            "    std::unique_ptr<usp::Rv64CpuMaster> cpu_rv64;",
            "    std::unique_ptr<usp::QemuCpuMaster> cpu_qemu;",
            '    const bool use_qemu_master = (cpu_master == "qemu");',
            f'    xbar.masters.push_back({{"{cpu["name"]}", {cpu_fid}, {cpu_cid}, false, 0ULL}});',
            "    if (use_qemu_master) {",
            f'        cpu_qemu = std::make_unique<usp::QemuCpuMaster>("{cpu["name"]}_qemu", '
            "qemu_sock_path, quantum_ns);",
            "        cpu_qemu->bus.bind(xbar.in);",
            "    } else {",
            f'        cpu_rv64 = std::make_unique<usp::Rv64CpuMaster>("{cpu["name"]}", 0u);',
            "        cpu_rv64->bus.bind(xbar.in);",
            "    }",
        ]
    )

    # Instantiate every declared device purely from IR!
    ram_var = ""
    finisher_var = ""
    clint_var = ""
    plic_var = ""
    uart_var = ""
    l2_var = ""
    adc_var = ""
    dma_vars: list[str] = []
    jtag_vars: list[str] = []
    v2_plugin_vars: list[tuple[dict[str, Any], str]] = []
    dev_vars: list[tuple[dict[str, Any], str]] = []

    for idx, dev in enumerate(spec["devices"]):
        dname = str(dev["name"])
        kind = str(dev["kind"])
        binding = str(dev["binding"])
        var = f"dev_{dname}"
        dev_vars.append((dev, var))
        if kind == "ram":
            ram_var = var
            lines.append(
                f'    usp::TlmMemory {var}("{dname}", 0x{int(dev["size"]):X}ULL, false, 8u);'
            )
        elif kind == "rom":
            lines.append(
                f'    usp::TlmMemory {var}("{dname}", 0x{int(dev["size"]):X}ULL, true, 6u);'
            )
        elif kind in {"sram", "sam_buffer", "pattern_gen_la"}:
            lines.append(
                f'    usp::TlmMemory {var}("{dname}", 0x{int(dev["size"]):X}ULL, false, 6u);'
            )
        elif kind == "finisher":
            finisher_var = var
            lines.append(f'    usp::FinisherTlm {var}("{dname}");')
        elif kind == "uart":
            uart_var = var
            lines.append(f'    usp::Uart16550Tlm {var}("{dname}", console_path);')
        elif kind == "clint":
            clint_var = var
            lines.append(f'    usp::ClintTlm {var}("{dname}");')
        elif kind == "plic":
            plic_var = var
            lines.append(f'    usp::PlicTlm {var}("{dname}");')
        elif kind == "serial_adapter":
            lines.append(f'    usp::SerialAdapterTlm {var}("{dname}");')
        elif kind == "l2_cache":
            l2_var = var
            if use_verilator and binding == "verilator-rtl":
                lines.append(f'    usp::L2CacheRtl {var}("{dname}");')
            else:
                lines.append(f'    usp::L2CacheTlm {var}("{dname}");')
        elif kind == "dma_accel":
            dma_vars.append(var)
            lines.append(f'    usp::DmaAccelTlm {var}("{dname}");')
        elif kind == "jtag_to_axi4":
            jtag_vars.append(var)
            lines.append(f'    usp::JtagToAxi4Master {var}("{dname}");')
        elif kind == "tisar_adc_lut":
            adc_var = var
            lines.append(f'    usp::TisarAdcStreamTlm {var}("{dname}");')
        elif binding == "usp-plugin" and use_live_plugins:
            pproto = plugin_proto_by_dev.get(dname, "usp-mmio32-v1")
            if pproto == "usp-bus-v2":
                v2_plugin_vars.append((dev, var))
                lines.append(
                    f'    usp::UspBusV2ProxyTlm {var}("{dname}", '
                    f'get_sock("{dname}"), 15u, quantum_ns);'
                )
            else:
                lines.append(
                    f'    usp::UspMmio32ProxyTlm {var}("{dname}", get_sock("{dname}"), 15u);'
                )
        elif binding == "verilator-rtl" and use_verilator:
            rmeta = rtl_meta_by_dev.get(dname)
            rproto = rmeta["protocol"] if rmeta else "mmio32"
            if rproto == "axi4":
                lines.append(
                    f"    usp::VerilatorAxi4FullAdapter<V{dev['rtl_module']}, Traits_{dname}> "
                    f'{var}("{dname}", 4u);'
                )
            elif rproto == "axi4-lite":
                lines.append(
                    f"    usp::VerilatorAxi4LiteAdapter<V{dev['rtl_module']}, Traits_{dname}> "
                    f'{var}("{dname}", 4u);'
                )
            elif rproto == "tl-ul":
                lines.append(
                    f"    usp::VerilatorTlulAdapter<V{dev['rtl_module']}, Traits_{dname}> "
                    f'{var}("{dname}", 4u);'
                )
            else:
                busy_reg = int(dev.get("rtl_busy_reg", -1))
                lines.append(
                    f"    usp::VerilatorMmioWrapper<V{dev['rtl_module']}> {var}"
                    f'("{dname}", 4u, {busy_reg}, 2048u);'
                )
        elif kind == "hwacha_vec4":
            lines.append(f'    usp::HwachaVec4Tlm {var}("{dname}");')
        elif kind == "tuner_32ph":
            lines.append(f'    usp::Tuner32PhTlm {var}("{dname}");')
        elif kind == "fir_decim8":
            lines.append(f'    usp::FirDecim8Tlm {var}("{dname}");')
        elif kind == "polyphase_12t":
            lines.append(f'    usp::Polyphase12tTlm {var}("{dname}");')
        elif kind == "fft_128pt":
            lines.append(f'    usp::Fft128PtTlm {var}("{dname}");')
        else:
            lines.append(
                f'    usp::TlmMemory {var}("{dname}", 0x{int(dev["size"]):X}ULL, false, 10u);'
            )

        fid = fabric_idx[str(dev["fabric"])]
        cid = clock_idx[str(dev["clock_domain"])]
        is_ram = "true" if kind == "ram" else "false"
        log_mmio = (
            "true"
            if dev.get("log_mmio", kind not in {"ram", "rom", "sam_buffer", "pattern_gen_la"})
            else "false"
        )
        lines.append(f"    xbar.out.bind({var}.target);")
        lines.append(
            f'    xbar.routes.push_back({{"{dname}", 0x{int(dev["base"]):08X}ULL, '
            f"0x{int(dev['size']):08X}ULL, {fid}, {cid}, {idx}, {is_ram}, {log_mmio}, 0ULL}});"
        )

    # Bind additional bus masters (DMA, JTAG-to-AXI4, TISAR AXI4-Stream, usp-bus-v2 Reverse DMA!)
    for dev, var in dev_vars:
        fid = fabric_idx[str(dev["fabric"])]
        cid = clock_idx[str(dev["clock_domain"])]
        if dev["kind"] == "dma_accel":
            lines.append(
                f'    xbar.masters.push_back({{"{dev["name"]}", {fid}, {cid}, true, 0ULL}});'
            )
            lines.append(f"    {var}.dma_master.bind(xbar.in);")
        elif dev["kind"] == "jtag_to_axi4":
            lines.append(
                f'    xbar.masters.push_back({{"{dev["name"]}", {fid}, {cid}, false, 0ULL}});'
            )
            lines.append(f"    {var}.axi4_master.bind(xbar.in);")
        elif dev["kind"] == "tisar_adc_lut":
            lines.append(
                f'    xbar.masters.push_back({{"{dev["name"]}_stream", '
                f"{fid}, {cid}, false, 0ULL}});"
            )
            lines.append(f"    {var}.stream_bus.bind(xbar.in);")
    for dev, var in v2_plugin_vars:
        fid = fabric_idx[str(dev["fabric"])]
        cid = clock_idx[str(dev["clock_domain"])]
        lines.append(
            f'    xbar.masters.push_back({{"{dev["name"]}_dma", {fid}, {cid}, true, 0ULL}});'
        )
        lines.append(f"    {var}.dma_master.bind(xbar.in);")

    # Wire AXI4-Stream pipeline endpoints on TISAR ADC Stream Engine from IR
    if adc_var:
        by_kind = {str(d["kind"]): d for d in spec["devices"]}
        by_role = {str(d.get("role_tag", "")): d for d in spec["devices"] if d.get("role_tag")}
        if "pattern_gen_la" in by_kind:
            pg = by_kind["pattern_gen_la"]
            pg_base = int(pg["base"])
            la_off = int(pg["size"]) // 2
            lines.append(f"    {adc_var}.patgen_base = 0x{pg_base:08X}ULL;")
            lines.append(f"    {adc_var}.la_base = 0x{pg_base + la_off:08X}ULL;")
        for tag, field in (
            ("sam_adc", "sam_adc_base"),
            ("sam_tuner", "sam_tuner_base"),
            ("sam_fir", "sam_fir_base"),
            ("sam_poly", "sam_poly_base"),
            ("sam_fft", "sam_fft_base"),
        ):
            if tag in by_role:
                lines.append(f"    {adc_var}.{field} = 0x{int(by_role[tag]['base']):08X}ULL;")
        for kind_k, field in (
            ("tuner_32ph", "tuner_base"),
            ("fir_decim8", "fir_base"),
            ("polyphase_12t", "poly_base"),
            ("fft_128pt", "fft_base"),
        ):
            if kind_k in by_kind:
                lines.append(f"    {adc_var}.{field} = 0x{int(by_kind[kind_k]['base']):08X}ULL;")

    # Bind PLIC IRQs, L2 cache, and CPU pointers
    for dev, var in dev_vars:
        if "irq" in dev:
            lines.append(f"    {plic_var}.bind_irq({int(dev['irq'])}u, &{var});")
    if l2_var:
        lines.append(f"    xbar.l2 = &{l2_var};")
    mode_lbl = "hybrid-rtl-plugin" if (use_verilator or use_live_plugins) else "pure-systemc-tlm"
    lines.extend(
        [
            f"    xbar.plic = &{plic_var};",
            "    if (use_qemu_master) {",
            (
                f"        if (!shared_ram_path.empty() && "
                f"!{ram_var}.map_shared_file(shared_ram_path)) {{"
            ),
            (
                '            std::cerr << "Failed to mmap shared QEMU RAM: " '
                '<< shared_ram_path << "\\n";'
            ),
            "            return 2;",
            "        }",
            f"        cpu_qemu->finisher = &{finisher_var};",
            f"        cpu_qemu->clint = &{clint_var};",
            f"        cpu_qemu->plic = &{plic_var};",
        ]
    )
    for _, var in v2_plugin_vars:
        lines.append(f"        cpu_qemu->quantum_peers.push_back(&{var});")
    for dev, var in dev_vars:
        if use_verilator and dev["binding"] == "verilator-rtl" and dev["kind"] != "l2_cache":
            rmeta = rtl_meta_by_dev.get(str(dev["name"]))
            if rmeta is not None and rmeta["protocol"] in {"axi4", "axi4-lite", "tl-ul"}:
                lines.append(f"        cpu_qemu->quantum_peers.push_back(&{var});")
    lines.extend(
        [
            "    } else {",
            f"        cpu_rv64->finisher = &{finisher_var};",
            f"        cpu_rv64->clint = &{clint_var};",
            f"        cpu_rv64->plic = &{plic_var};",
            "        cpu_rv64->max_steps = max_steps;",
            "        uint64_t entry = SOC_CPU_ENTRY;",
            f"        if (!usp::load_elf64(elf_path, {ram_var}, SOC_MAIN_RAM_BASE, entry)) {{",
            '            std::cerr << "Failed to load RV64 ELF: " << elf_path << "\\n";',
            "            return 2;",
            "        }",
            "        cpu_rv64->pc = entry;",
            "    }",
            "    sc_core::sc_start();",
            "",
            (
                "    const bool timed_out = "
                "use_qemu_master ? cpu_qemu->timed_out : cpu_rv64->timed_out;"
            ),
            (
                "    const uint64_t insns_exec = "
                "use_qemu_master ? cpu_qemu->instructions : cpu_rv64->instructions;"
            ),
            (
                "    const uint64_t traps_exec = "
                "use_qemu_master ? cpu_qemu->traps_taken : cpu_rv64->traps_taken;"
            ),
            (
                "    const uint64_t q_barriers = "
                "use_qemu_master ? cpu_qemu->quantum_sync_barriers : 0ULL;"
            ),
            "    const uint64_t q_time_ps = use_qemu_master ? cpu_qemu->qemu_time_ps : 0ULL;",
            "    const uint64_t q_drift_ps = use_qemu_master ? cpu_qemu->max_time_drift_ps : 0ULL;",
            "    uint64_t total_rev_dma_bytes = 0ULL;",
        ]
    )
    for _, var in v2_plugin_vars:
        lines.append(f"    total_rev_dma_bytes += {var}.reverse_dma_bytes();")
    lines.extend(
        [
            (
                f"    const bool passed = {finisher_var}.halted && "
                f"{finisher_var}.exit_code == 0 && !timed_out;"
            ),
            "    std::ofstream summary(summary_path);",
            '    summary << "{\\n"',
            '            << "  \\"status\\": \\"" << (passed ? "PASSED" : "FAILED") << "\\",\\n"',
            f'            << "  \\"design\\": \\"{spec["name"]}\\",\\n"',
            f'            << "  \\"mode\\": \\"{mode_lbl}\\",\\n"',
            '            << "  \\"cpu_master\\": \\"" << cpu_master << "\\",\\n"',
            (
                '            << "  \\"halted\\": " << '
                f'({finisher_var}.halted ? "true" : "false") << ",\\n"'
            ),
            '            << "  \\"timed_out\\": " << (timed_out ? "true" : "false") << ",\\n"',
            (
                '            << "  \\"exit_code\\": " << '
                f'(timed_out ? 124 : {finisher_var}.exit_code) << ",\\n"'
            ),
            '            << "  \\"instructions_executed\\": " << insns_exec << ",\\n"',
            '            << "  \\"traps_taken\\": " << traps_exec << ",\\n"',
            (
                '            << "  \\"systemc_time_ps\\": " << '
                'sc_core::sc_time_stamp().value() << ",\\n"'
            ),
            '            << "  \\"quantum_ns\\": " << quantum_ns << ",\\n"',
            '            << "  \\"quantum_sync_barriers\\": " << q_barriers << ",\\n"',
            '            << "  \\"qemu_time_ps\\": " << q_time_ps << ",\\n"',
            '            << "  \\"max_time_drift_ps\\": " << q_drift_ps << ",\\n"',
            '            << "  \\"total_transactions\\": " << xbar.total_transactions << ",\\n"',
            '            << "  \\"mmio_transactions\\": " << xbar.mmio_transactions << ",\\n"',
            '            << "  \\"cdc_crossings\\": " << xbar.cdc_crossings << ",\\n"',
            (
                '            << "  \\"burst64_transactions\\": " << '
                'xbar.burst64_transactions << ",\\n"'
            ),
            '            << "  \\"burst64_bytes\\": " << xbar.burst64_bytes << ",\\n"',
            '            << "  \\"reverse_dma_bytes\\": " << total_rev_dma_bytes << ",\\n"',
            f'            << "  \\"uart_tx_bytes\\": " << {uart_var}.tx_bytes << ",\\n"',
        ]
    )
    if l2_var:
        lines.extend(
            [
                f'            << "  \\"l2_hits\\": " << {l2_var}.hits << ",\\n"',
                f'            << "  \\"l2_misses\\": " << {l2_var}.misses << ",\\n"',
                f'            << "  \\"l2_dma_snoops\\": " << {l2_var}.snoops << ",\\n"',
            ]
        )
    if dma_vars:
        lines.append(
            '            << "  \\"dma_bytes_transferred\\": " << '
            f'{dma_vars[0]}.bytes_transferred << ",\\n"'
        )
    if jtag_vars:
        lines.append(f'            << "  \\"jtag_axi4_tx\\": " << {jtag_vars[0]}.tx_done << ",\\n"')
    if adc_var:
        lines.extend(
            [
                (
                    '            << "  \\"axi4_stream_packets_total\\": " << '
                    f'{adc_var}.stream_packets_total << ",\\n"'
                ),
                (
                    '            << "  \\"axi4_stream_stages\\": [" << '
                    f'{adc_var}.stage_packets[0] << ", " << {adc_var}.stage_packets[1] << ", "'
                    f' << {adc_var}.stage_packets[2] << ", " << {adc_var}.stage_packets[3] << ", "'
                    f' << {adc_var}.stage_packets[4] << "],\\n"'
                ),
            ]
        )
    # Emit fabric hits, master hits, device hits, RTL cycles, pin handshakes, burst64/DMA, backends
    lines.append('            << "  \\"fabric_hits\\": {\\n";')
    lines.append("    for (size_t i = 0; i < xbar.fabrics.size(); ++i) {")
    lines.append(
        '        summary << "    \\"" << xbar.fabrics[i].name << "\\": " << xbar.fabrics[i].hits'
        ' << (i + 1 < xbar.fabrics.size() ? ",\\n" : "\\n");'
    )
    lines.append("    }")
    lines.append('    summary << "  },\\n  \\"master_hits\\": {\\n";')
    lines.append("    for (size_t i = 0; i < xbar.masters.size(); ++i) {")
    lines.append(
        '        summary << "    \\"" << xbar.masters[i].name << "\\": " << xbar.masters[i].hits'
        ' << (i + 1 < xbar.masters.size() ? ",\\n" : "\\n");'
    )
    lines.append("    }")
    lines.append('    summary << "  },\\n  \\"device_hits\\": {\\n";')
    lines.append("    for (size_t i = 0; i < xbar.routes.size(); ++i) {")
    lines.append(
        '        summary << "    \\"" << xbar.routes[i].name << "\\": " << xbar.routes[i].hits'
        ' << (i + 1 < xbar.routes.size() ? ",\\n" : "\\n");'
    )
    lines.append("    }")
    lines.append('    summary << "  },\\n  \\"rtl_cycles\\": {\\n";')
    for i, (dev, var) in enumerate(dev_vars):
        comma = "," if i + 1 < len(dev_vars) else ""
        lines.append(
            f'    summary << "    \\"{dev["name"]}\\": " << {var}.rtl_cycles() << "{comma}\\n";'
        )
    lines.append('    summary << "  },\\n  \\"bus_handshakes\\": {\\n";')
    for i, (dev, var) in enumerate(dev_vars):
        comma = "," if i + 1 < len(dev_vars) else ""
        lines.append(
            f'    summary << "    \\"{dev["name"]}\\": " << {var}.bus_handshakes() << "{comma}\\n";'
        )
    lines.append('    summary << "  },\\n  \\"device_burst64_tx\\": {\\n";')
    for i, (dev, var) in enumerate(dev_vars):
        comma = "," if i + 1 < len(dev_vars) else ""
        lines.append(
            f'    summary << "    \\"{dev["name"]}\\": " << {var}.burst64_tx() << "{comma}\\n";'
        )
    lines.append('    summary << "  },\\n  \\"device_reverse_dma_bytes\\": {\\n";')
    for i, (dev, var) in enumerate(dev_vars):
        comma = "," if i + 1 < len(dev_vars) else ""
        lines.append(
            f'    summary << "    \\"{dev["name"]}\\": " << '
            f'{var}.reverse_dma_bytes() << "{comma}\\n";'
        )
    lines.append('    summary << "  },\\n  \\"device_quantum_syncs\\": {\\n";')
    for i, (dev, var) in enumerate(dev_vars):
        comma = "," if i + 1 < len(dev_vars) else ""
        lines.append(
            f'    summary << "    \\"{dev["name"]}\\": " << '
            f'{var}.quantum_sync_count() << "{comma}\\n";'
        )
    lines.append('    summary << "  },\\n  \\"device_backends\\": {\\n";')
    for i, (dev, var) in enumerate(dev_vars):
        comma = "," if i + 1 < len(dev_vars) else ""
        lines.append(
            f'    summary << "    \\"{dev["name"]}\\": \\"" << '
            f'{var}.runtime_backend() << "\\"{comma}\\n";'
        )
    lines.extend(
        [
            '    summary << "  }\\n}\\n";',
            "    summary.flush();",
            f"    return passed ? 0 : (timed_out ? 124 : {finisher_var}.exit_code);",
            "}",
        ]
    )
    return "\n".join(lines) + "\n"


def plan_composed_soc(spec_path: Path, out_dir: Path) -> dict[str, Any]:
    """Validate `multiplatform-soc-1` and emit IR, lockfile, C++, IP-XACT, QEMU, gem5 & Chipyard."""
    spec_path = spec_path.resolve()
    out_dir = out_dir.resolve()
    spec = load_composed_spec(spec_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "soc_map.h").write_text(_render_soc_map_h(spec))
    (out_dir / "link.ld").write_text(_render_linker_script(spec))
    (out_dir / "link_qemu.ld").write_text(_render_qemu_linker_script(spec))
    (out_dir / "generated_soc_top.cpp").write_text(
        _render_generated_cpp(spec, spec_path=spec_path, use_verilator=True, use_live_plugins=True)
    )

    ipxact_dir = out_dir / "ipxact"
    ipxact_dir.mkdir(exist_ok=True)
    (ipxact_dir / "design.xml").write_text(_render_ipxact_design_xml(spec))

    qemu_dir = out_dir / "qemu"
    qemu_dir.mkdir(exist_ok=True)
    dts_path = qemu_dir / f"{spec['name'].lower()}.dts"
    dts_path.write_text(_render_qemu_dts(spec))
    dtb_built = False
    if shutil.which("dtc") is not None:
        dtb_path = qemu_dir / f"{spec['name'].lower()}.dtb"
        proc = subprocess.run(
            ["dtc", "-I", "dts", "-O", "dtb", "-o", str(dtb_path), str(dts_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        dtb_built = proc.returncode == 0 and dtb_path.is_file()

    gem5_dir = out_dir / "gem5"
    gem5_dir.mkdir(exist_ok=True)
    gem5_py = gem5_dir / f"{spec['name'].lower()}_gem5_system.py"
    gem5_py.write_text(_render_gem5_system_py(spec, spec_path=spec_path))

    cy_dir = out_dir / "chipyard"
    cy_dir.mkdir(exist_ok=True)
    cy_scala = cy_dir / f"{spec['name']}ChipyardConfig.scala"
    cy_scala.write_text(_render_chipyard_scala(spec))

    lock = _render_binding_lock(spec, spec_path)
    write_json(out_dir / "binding-lock.json", lock)

    ir = {
        "contract": CONTRACT,
        "platform_version": __version__,
        "name": spec["name"],
        "spec_path": str(spec_path),
        "spec_sha256": digest(spec_path),
        "clock_domains": len(spec["clock_domains"]),
        "fabrics": len(spec["fabrics"]),
        "cdc_bridges": len(spec["cdc_bridges"]),
        "devices": len(spec["devices"]),
        "streams": len(spec.get("streams", [])),
        "providers": lock["provider_summary"],
        "exports": {
            "soc_map_h": digest(out_dir / "soc_map.h"),
            "link_ld": digest(out_dir / "link.ld"),
            "link_qemu_ld": digest(out_dir / "link_qemu.ld"),
            "generated_soc_top_cpp": digest(out_dir / "generated_soc_top.cpp"),
            "binding_lock_json": digest(out_dir / "binding-lock.json"),
            "ipxact_design_xml": digest(ipxact_dir / "design.xml"),
            "qemu_dts": digest(dts_path),
            "qemu_dtb_compiled": dtb_built,
            "gem5_system_py": digest(gem5_py),
            "chipyard_scala": digest(cy_scala),
        },
    }
    write_json(out_dir / "composed-ir.json", ir)
    return ir


def _build_verilator_modules(
    spec: dict[str, Any], spec_path: Path, out_dir: Path
) -> tuple[list[str], list[str], str]:
    verilator_bin, verilator_ver = tool_version("verilator")
    vroot = subprocess.check_output(
        [verilator_bin, "--getenv", "VERILATOR_ROOT"], text=True, timeout=15
    ).strip()
    includes = ["-isystem", f"{vroot}/include", "-isystem", f"{vroot}/include/vltstd"]
    links: list[str] = []
    runtime_links: list[str] = []
    for dev in spec["devices"]:
        if dev["binding"] != "verilator-rtl":
            continue
        mod = str(dev["rtl_module"])
        sv = _resolve_rel(spec_path, str(dev["rtl_source"]))
        mdir = out_dir / f"verilator_{mod}"
        mdir.mkdir(parents=True, exist_ok=True)
        prefix = f"V{mod}"
        cmd = [
            verilator_bin,
            "--cc",
            "--Mdir",
            str(mdir),
            "--prefix",
            prefix,
            "--build",
            "-j",
            "2",
            "-Wall",
            "-Wno-UNUSEDSIGNAL",
            str(sv),
        ]
        execute_build(cmd, out_dir, out_dir / f"verilator-{mod}.log", 120)
        includes += ["-isystem", str(mdir)]
        lib_model = mdir / f"lib{prefix}.a"
        alt_model = mdir / f"{prefix}__ALL.a"
        links.append(str(lib_model if lib_model.is_file() else alt_model))
        if not runtime_links:
            lib_v = mdir / "libverilated.a"
            if lib_v.is_file():
                runtime_links.append(str(lib_v))
            else:
                runtime_links.append(f"{vroot}/include/verilated.cpp")
                vt = Path(f"{vroot}/include/verilated_threads.cpp")
                if vt.is_file():
                    runtime_links.append(str(vt))
    return includes, [*links, *runtime_links], verilator_ver


def _compile_rv64_sw(
    gcc: str,
    crt0_path: Path,
    c_src: Path,
    inc_dir: Path,
    out_elf: Path,
    log_path: Path,
    isa: str = "rv64ima_zicsr",
) -> str:
    composer_inc = _resolve_runtime_hpp().parent
    cmd = [
        gcc,
        f"-march={isa}",
        "-mabi=lp64",
        "-mcmodel=medany",
        "-O2",
        "-nostdlib",
        "-nostartfiles",
        "-ffreestanding",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wl,--build-id=none",
        f"-I{inc_dir}",
        f"-I{composer_inc}",
        "-T",
        str(inc_dir / "link.ld"),
        str(crt0_path),
        str(c_src),
        "-o",
        str(out_elf),
    ]
    execute_build(cmd, inc_dir, log_path, 60)
    return digest(out_elf)


def _resolve_qemu_accel(requested: str) -> str:
    if requested not in ALLOWED_QEMU_ACCELS:
        raise ContractError(f"E_COMPOSE_QEMU_ACCEL unsupported accel: {requested}")
    arch = platform.machine().lower()
    kvm_ready = arch == "riscv64" and os.access("/dev/kvm", os.R_OK | os.W_OK)
    if requested == "kvm":
        if not kvm_ready:
            raise ContractError(
                f"E_COMPOSE_QEMU_KVM KVM acceleration requested (--qemu-accel kvm) "
                f"but host architecture is '{arch}' and /dev/kvm is not usable for riscv64 KVM"
            )
        return "kvm"
    if requested == "auto":
        return "kvm" if kvm_ready else "tcg"
    return "tcg"


def _compile_rv64_qemu_sw(
    gcc: str,
    crt0_path: Path,
    c_src: Path,
    inc_dir: Path,
    out_elf: Path,
    log_path: Path,
    isa: str = "rv64ima_zicsr",
) -> str:
    """Compile guest RV64 firmware linked with M-mode QEMU PMP + VirtIO `usp-bus-v2` bridge."""
    composer_inc = _resolve_runtime_hpp().parent
    bridge_s = _resolve_composer_resource("qemu_usp_bridge.S")
    bridge_c = _resolve_composer_resource("qemu_usp_bridge.c")
    bridge_s_obj = inc_dir / "qemu_usp_bridge_asm.o"
    bridge_c_obj = inc_dir / "qemu_usp_bridge_c.o"
    base_flags = [
        gcc,
        f"-march={isa}",
        "-mabi=lp64",
        "-mcmodel=medany",
        "-O2",
        "-ffreestanding",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-DUSP_QEMU_CPU_MASTER=1",
        f"-I{inc_dir}",
        f"-I{composer_inc}",
    ]
    if not bridge_s_obj.is_file():
        execute_build(
            [*base_flags, "-c", str(bridge_s), "-o", str(bridge_s_obj)],
            inc_dir,
            inc_dir / "bridge-asm-build.log",
            60,
        )
    if not bridge_c_obj.is_file():
        execute_build(
            [*base_flags, "-c", str(bridge_c), "-o", str(bridge_c_obj)],
            inc_dir,
            inc_dir / "bridge-c-build.log",
            60,
        )
    cmd = [
        *base_flags,
        "-nostdlib",
        "-nostartfiles",
        "-Wl,--build-id=none",
        "-T",
        str(inc_dir / "link_qemu.ld"),
        str(bridge_s_obj),
        str(bridge_c_obj),
        str(crt0_path),
        str(c_src),
        "-o",
        str(out_elf),
    ]
    execute_build(cmd, inc_dir, log_path, 60)
    return digest(out_elf)


def _run_qemu_crosscheck(
    spec: dict[str, Any],
    spec_path: Path,
    out_dir: Path,
    gcc: str,
    crt0_path: Path,
) -> dict[str, Any] | None:
    fw = spec["firmware"]
    if "qemu_crosscheck_src" not in fw:
        return None
    qemu = shutil.which("qemu-system-riscv64")
    if qemu is None:
        return {"status": "SKIPPED", "reason": "qemu-system-riscv64 not installed"}
    qdir = out_dir / "qemu-crosscheck"
    qdir.mkdir(parents=True, exist_ok=True)
    # Minimal QEMU virt linker script at 0x80000000
    (qdir / "link.ld").write_text(
        "OUTPUT_ARCH(riscv)\nENTRY(_start)\n"
        "MEMORY { RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 0x00800000 }\n"
        "SECTIONS {\n"
        "  . = 0x80000000;\n"
        "  .text : { *(.text.init) *(.text*) }\n"
        "  .rodata : ALIGN(8) { *(.rodata*) }\n"
        "  .data : ALIGN(8) { *(.data*) }\n"
        "  .bss : ALIGN(8) { _bss_start = .; *(.bss*) *(COMMON) . = ALIGN(8); _bss_end = .; }\n"
        "  PROVIDE(_stack_top = 0x800F0000);\n"
        "  PROVIDE(_hart1_stack_top = 0x800E0000);\n"
        "  PROVIDE(_hart2_stack_top = 0x800D0000);\n"
        "  PROVIDE(_hart3_stack_top = 0x800C0000);\n"
        "}\n"
    )
    qelf = qdir / "qemu_crosscheck.elf"
    qsrc = _resolve_rel(spec_path, str(fw["qemu_crosscheck_src"]))
    qsha = _compile_rv64_sw(gcc, crt0_path, qsrc, qdir, qelf, qdir / "build.log")
    _, qver = tool_version("qemu-system-riscv64")
    proc = subprocess.run(
        [
            qemu,
            "-machine",
            "virt",
            "-cpu",
            "rv64",
            "-m",
            "64M",
            "-bios",
            "none",
            "-nographic",
            "-serial",
            "stdio",
            "-monitor",
            "none",
            "-kernel",
            str(qelf),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    (qdir / "console.log").write_text(proc.stdout)
    expected = list(fw.get("qemu_expected_markers", []))
    markers_ok = all(m in proc.stdout for m in expected)
    ok = proc.returncode == 0 and markers_ok
    return {
        "status": "PASSED" if ok else "FAILED",
        "qemu_version": qver,
        "elf_sha256": qsha,
        "returncode": proc.returncode,
        "markers_matched": sum(1 for m in expected if m in proc.stdout),
        "markers_total": len(expected),
        "console_output": proc.stdout,
    }


def _compute_composed_coverage(
    spec: dict[str, Any],
    spec_path: Path,
    summary: dict[str, Any],
    console_text: str,
    vcd_path: Path,
    neg_ok: bool,
    plugin_traces: dict[str, dict[str, Any]],
    qemu_res: dict[str, Any] | None,
    use_verilator: bool,
    use_live_plugins: bool,
    cpu_master_used: str = "systemc-rv64",
) -> dict[str, Any]:
    fab_hits = summary.get("fabric_hits", {})
    master_hits = summary.get("master_hits", {})
    dev_hits = summary.get("device_hits", {})
    rtl_cyc = summary.get("rtl_cycles", {})
    bus_hs = summary.get("bus_handshakes", {})
    dev_burst64 = summary.get("device_burst64_tx", {})
    dev_rev_dma = summary.get("device_reverse_dma_bytes", {})
    dev_qsync = summary.get("device_quantum_syncs", {})
    streams = summary.get("axi4_stream_stages", [])
    markers = list(spec["firmware"]["expected_markers"])

    has_l2 = any(d["kind"] == "l2_cache" for d in spec["devices"])
    has_dma_accel = any(d["kind"] == "dma_accel" for d in spec["devices"])
    has_jtag = any(d["kind"] == "jtag_to_axi4" for d in spec["devices"])
    has_sam = any(d["kind"] in {"sam_buffer", "pattern_gen_la"} for d in spec["devices"])

    bins: dict[str, bool] = {
        "all_firmware_markers_passed": all(m in console_text for m in markers),
        "all_fabrics_active": all(fab_hits.get(f["name"], 0) > 0 for f in spec["fabrics"]),
        "cdc_clock_domain_crossings_verified": (
            summary.get("cdc_crossings", 0) > 0 if len(spec["clock_domains"]) > 1 else True
        ),
        "multi_master_bus_arbitration": sum(1 for v in master_hits.values() if v > 0)
        >= min(3, len(master_hits)),
        "vcd_waveform_captured": vcd_path.is_file()
        and "$enddefinitions" in vcd_path.read_text(errors="replace"),
        "negative_guest_failure_enforced": neg_ok,
    }
    if has_l2:
        bins["l2_cache_hits_misses_snoops"] = (
            summary.get("l2_hits", 0) > 0
            and summary.get("l2_misses", 0) > 0
            and summary.get("l2_dma_snoops", 0) > 0
        )
    if has_dma_accel:
        bins["dma_cross_bridge_transfer"] = summary.get("dma_bytes_transferred", 0) >= 64
    if has_jtag:
        bins["jtag_to_axi4_independent_master"] = summary.get("jtag_axi4_tx", 0) >= 2
    if spec.get("streams"):
        bins["axi4_stream_5_stages_active"] = len(streams) == 5 and all(int(x) > 0 for x in streams)
    if has_sam:
        bins["all_sam_buffers_populated"] = all(
            dev_hits.get(d["name"], 0) > 0
            for d in spec["devices"]
            if d["kind"] in {"sam_buffer", "pattern_gen_la"}
        )

    if cpu_master_used == "qemu":
        bins["qemu_cpu_master_active"] = (
            summary.get("cpu_master") == "qemu" and summary.get("instructions_executed", 0) > 0
        )
        bins["quantum_time_sync_verified"] = summary.get("quantum_sync_barriers", 0) > 0

    for dev in spec["devices"]:
        if dev["binding"] == "verilator-rtl":
            bins[f"rtl_{dev['name']}_active"] = dev_hits.get(dev["name"], 0) > 0 and (
                not use_verilator or rtl_cyc.get(dev["name"], 0) > 0
            )
            if use_verilator:
                rmeta = _device_rtl_meta(dev, spec_path)
                if rmeta is not None and rmeta["protocol"] in {"axi4", "axi4-lite", "tl-ul"}:
                    ptag = rmeta["protocol"].replace("-", "_")
                    bins[f"rtl_pin_{dev['name']}_{ptag}_handshakes"] = (
                        bus_hs.get(dev["name"], 0) > 0
                    )
        elif dev["binding"] == "usp-plugin":
            tr = plugin_traces.get(dev["name"], {})
            bins[f"live_plugin_{dev['name']}_ipc_ok"] = (
                (tr.get("status") == "PASSED" and tr.get("tx_count", 0) > 0)
                if use_live_plugins
                else dev_hits.get(dev["name"], 0) > 0
            )
            if use_live_plugins:
                pkg, _ = _device_plugin_info(dev, spec_path)
                if pkg["interface"]["protocol"] == "usp-bus-v2":
                    bins[f"plugin_v2_{dev['name']}_reverse_dma_ok"] = (
                        dev_rev_dma.get(dev["name"], 0) > 0
                    )
                    bins[f"plugin_v2_{dev['name']}_burst64_ok"] = (
                        dev_burst64.get(dev["name"], 0) > 0
                    )
                    if cpu_master_used == "qemu":
                        bins[f"plugin_v2_{dev['name']}_quantum_sync_ok"] = (
                            dev_qsync.get(dev["name"], 0) > 0
                        )
    if qemu_res is not None and qemu_res.get("status") != "SKIPPED":
        bins["qemu_cross_engine_rv64_passed"] = qemu_res.get("status") == "PASSED"

    hit = sum(1 for v in bins.values() if v)
    total = len(bins)
    return {
        "contract": "usp-composed-coverage-1",
        "bins_hit": hit,
        "bins_total": total,
        "coverage_percent": round(100.0 * hit / total, 2),
        "status": "PASSED" if hit == total else "FAILED",
        "bins": bins,
    }


def _render_composed_report_md(report: dict[str, Any]) -> str:
    pos = report.get("positive_run", {})
    summ = pos.get("summary", {})
    cov = report.get("coverage", {})
    lines = [
        f"# Multi-Platform Composed SoC Report ({report['design']})",
        "",
        f"- **Contract:** `{report['contract']}`",
        f"- **Status:** **{report['status']}**",
        f"- **Execution Mode:** `{report['execution_mode']}`",
        f"- **CPU Master Engine:** `{report.get('cpu_master_engine', 'systemc-rv64')}`"
        + (f" (`accel={report['qemu_accel']}`)" if report.get("qemu_accel") else ""),
        f"- **Functional Coverage:** **{cov.get('coverage_percent', 0)}%** "
        f"(`{cov.get('bins_hit', 0)}/{cov.get('bins_total', 0)}` bins)",
        f"- **Timestamp (UTC):** `{report['created_utc']}`",
        "",
        "## System-Level Multi-Clock, Quantum Sync & Interconnect Metrics",
        "",
        f"- Instructions retired/executed: `{summ.get('instructions_executed')}` | "
        f"Traps/IRQs: `{summ.get('traps_taken')}`",
        f"- Wall-clock run time (ms): `{pos.get('wall_time_ms')}` | "
        f"Wall-time emulation throughput (MIPS, host wall clock): `{pos.get('wall_time_mips')}`",
        f"- SystemC simulated time (ps): `{summ.get('systemc_time_ps')}` | "
        f"Quantum (`ns`): `{summ.get('quantum_ns')}` | "
        f"Quantum sync barriers: `{summ.get('quantum_sync_barriers', 0)}` | "
        f"Max time drift (ps): `{summ.get('max_time_drift_ps', 0)}`",
        f"- Total bus transactions: `{summ.get('total_transactions')}` | "
        f"Async CDC crossings: `{summ.get('cdc_crossings')}`",
        f"- 64-bit Burst transactions: `{summ.get('burst64_transactions', 0)}` "
        f"(`{summ.get('burst64_bytes', 0)}` bytes) | "
        f"Plugin Reverse Bus-Master DMA bytes: `{summ.get('reverse_dma_bytes', 0)}`",
        f"- SoC DMA transferred bytes: `{summ.get('dma_bytes_transferred', 0)}` | "
        f"JTAG-to-AXI4 host transactions: `{summ.get('jtag_axi4_tx', 0)}`",
        f"- L2 Cache: `hits={summ.get('l2_hits', 0)}`, `misses={summ.get('l2_misses', 0)}`, "
        f"`dma_snoops={summ.get('l2_dma_snoops', 0)}`",
    ]
    if "axi4_stream_packets_total" in summ:
        lines.append(
            f"- AXI4-Stream Pipeline Packets: total=`{summ.get('axi4_stream_packets_total')}` "
            f"stages=`{summ.get('axi4_stream_stages')}`"
        )
    lines.extend(
        [
            "",
            "### Fabric Traffic",
            "",
            "| Fabric / Crossbar | Transactions |",
            "|---|---:|",
        ]
    )
    for fn, cnt in summ.get("fabric_hits", {}).items():
        lines.append(f"| `{fn}` | `{cnt}` |")
    lines.extend(["", "### Bus Master Traffic", "", "| Master | Transactions |", "|---|---:|"])
    for mn, cnt in summ.get("master_hits", {}).items():
        lines.append(f"| `{mn}` | `{cnt}` |")
    lines.extend(
        [
            "",
            "### Heterogeneous Device Bindings, Pin-Level Handshakes & Verilator RTL Cycles",
            "",
            (
                "| Device Instance | Runtime Backend | Bus Hits | "
                "Pin Handshakes | Verilator RTL Cycles |"
            ),
            "|---|---|---:|---:|---:|",
        ]
    )
    backends = summ.get("device_backends", {})
    rtl_cyc = summ.get("rtl_cycles", {})
    bus_hs = summ.get("bus_handshakes", {})
    for dn, cnt in summ.get("device_hits", {}).items():
        lines.append(
            f"| `{dn}` | `{backends.get(dn, 'systemc-tlm')}` | `{cnt}` | "
            f"`{bus_hs.get(dn, 0)}` | `{rtl_cyc.get(dn, 0)}` |"
        )
    lines.append("")
    return "\n".join(lines)


def run_composed_soc(
    spec_path: Path,
    out_dir: Path,
    *,
    mode: str = "auto",
    cpu_master: str = "auto",
    qemu_accel: str = "auto",
    quantum_ns: int | None = None,
    verify_qemu_crosscheck: bool = True,
    max_steps: int = 800_000,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Build and run a `multiplatform-soc-1` SoC with QEMU/SystemC CPU, Verilator RTL & plugins."""
    if mode not in {"auto", "hybrid", "tlm"}:
        raise ContractError(f"E_COMPOSE_MODE unsupported mode: {mode}")
    if cpu_master not in ALLOWED_CPU_MASTERS:
        raise ContractError(f"E_COMPOSE_CPU_MASTER unsupported cpu_master: {cpu_master}")
    if qemu_accel not in ALLOWED_QEMU_ACCELS:
        raise ContractError(f"E_COMPOSE_QEMU_ACCEL unsupported qemu_accel: {qemu_accel}")
    spec_path = spec_path.resolve()
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ContractError("E_COMPOSE_OUT choose a fresh output directory")

    ir = plan_composed_soc(spec_path, out_dir)
    spec = load_composed_spec(spec_path)
    resolved_cpu_master = (
        str(spec["cpu"].get("master_engine", "systemc-rv64"))
        if cpu_master == "auto"
        else cpu_master
    )
    resolved_quantum_ns = (
        int(quantum_ns) if quantum_ns is not None else int(spec["cpu"].get("quantum_ns", 100_000))
    )
    if resolved_quantum_ns <= 0:
        raise ContractError(f"E_COMPOSE_QUANTUM quantum_ns must be > 0, got {resolved_quantum_ns}")

    resolved_accel: str | None = None
    qemu_bin: str | None = None
    qemu_ver: str | None = None
    if resolved_cpu_master == "qemu":
        resolved_accel = _resolve_qemu_accel(qemu_accel)

    want_hybrid = mode in {"auto", "hybrid"}
    ready, missing = has_unified_toolchain(require_verilator=(mode == "hybrid"))
    if resolved_cpu_master == "qemu" and shutil.which("qemu-system-riscv64") is None:
        ready = False
        missing = [*missing, "qemu-system-riscv64"]
    if not ready:
        blocked: dict[str, Any] = {
            "contract": CONTRACT,
            "platform_version": __version__,
            "design": ir["name"],
            "status": "BLOCKED",
            "reason": f"Missing required native build/simulation tools: {', '.join(missing)}",
            "missing_tools": missing,
            "generated_ir": str(out_dir / "composed-ir.json"),
            "binding_lock": str(out_dir / "binding-lock.json"),
            "hint": "Run inside Rootless Podman SDK (`bash scripts/podman.sh smoke`).",
        }
        write_json(out_dir / "report.json", blocked)
        return blocked

    if resolved_cpu_master == "qemu":
        qemu_bin, qemu_ver = tool_version("qemu-system-riscv64")

    use_verilator = want_hybrid and shutil.which("verilator") is not None
    use_live_plugins = want_hybrid

    cpp_path = out_dir / "generated_soc_top.cpp"
    cpp_path.write_text(
        _render_generated_cpp(
            spec,
            spec_path=spec_path,
            use_verilator=use_verilator,
            use_live_plugins=use_live_plugins,
        )
    )

    cxx, cxx_ver = tool_version("c++")
    gcc, gcc_ver = tool_version("riscv64-unknown-elf-gcc")
    sc_ver = subprocess.check_output(
        ["pkg-config", "--modversion", "systemc"], text=True, timeout=15
    ).strip()
    sc_flags = shlex.split(
        subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", "systemc"], text=True, timeout=15
        )
    )
    sc_inc = [arg for f in sc_flags if f.startswith("-I") for arg in ("-isystem", f[2:])]
    sc_lib = [f for f in sc_flags if not f.startswith("-I")]

    v_inc: list[str] = []
    v_lib: list[str] = []
    verilator_ver: str | None = None
    if use_verilator and any(d["binding"] == "verilator-rtl" for d in spec["devices"]):
        v_inc, v_lib, verilator_ver = _build_verilator_modules(spec, spec_path, out_dir)

    runtime_hpp = _resolve_runtime_hpp()
    emu_bin = out_dir / "composed-soc-emu"
    compile_cmd = [
        cxx,
        "-std=c++17",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        f"-I{out_dir}",
        f"-I{runtime_hpp.parent}",
        *v_inc,
        *sc_inc,
        str(cpp_path),
        *v_lib,
        *sc_lib,
        "-o",
        str(emu_bin),
    ]
    execute_build(compile_cmd, out_dir, out_dir / "emu-build.log", 120)

    # Build any `usp-plugin` native-model-package-1 binaries via usp package build!
    plugin_bins: dict[str, Path] = {}
    if use_live_plugins:
        for dev in spec["devices"]:
            if dev["binding"] != "usp-plugin":
                continue
            pkg_yaml = _resolve_rel(spec_path, str(dev["package_yaml"]))
            pbuild_dir = out_dir / f"plugin-build-{dev['name']}"
            build_package(pkg_yaml, pbuild_dir)
            plugin_bins[str(dev["name"])] = pbuild_dir / "model"

    crt0_path = resolve_unified_src_dir(spec_path) / "crt0.S"
    fw = spec["firmware"]
    pos_elf = out_dir / "positive_sw.elf"
    neg_elf = out_dir / "negative_sw.elf"
    compile_fn = _compile_rv64_qemu_sw if resolved_cpu_master == "qemu" else _compile_rv64_sw
    pos_sha = compile_fn(
        gcc,
        crt0_path,
        _resolve_rel(spec_path, str(fw["positive_src"])),
        out_dir,
        pos_elf,
        out_dir / "sw-pos-build.log",
    )
    neg_sha = compile_fn(
        gcc,
        crt0_path,
        _resolve_rel(spec_path, str(fw["negative_src"])),
        out_dir,
        neg_elf,
        out_dir / "sw-neg-build.log",
    )

    ram_dev = next(d for d in spec["devices"] if d["kind"] == "ram")
    ram_size_bytes = int(ram_dev["size"])
    # Round up (guest RAM + 2 MiB bridge headroom) to 2 MiB boundary, min 16 MiB for QEMU virt
    mb2 = 2 * 1024 * 1024
    shared_ram_bytes = max(((ram_size_bytes + mb2 + mb2 - 1) // mb2) * mb2, 16 * 1024 * 1024)

    def _exec_with_plugins(
        elf: Path, tag: str, vcd: Path | None
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, dict[str, Any]], float]:
        procs: list[tuple[str, subprocess.Popen[str], Path, Path]] = []
        plugin_args: list[str] = []
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="usp-p-") as sock_tmp:
            sock_dir = Path(sock_tmp)
            try:
                for idx, (pname, pbin) in enumerate(plugin_bins.items()):
                    # Keep AF_UNIX sun_path well under Linux's 108-byte sockaddr_un limit.
                    sock_p = sock_dir / f"p{idx}.sock"
                    trace_p = out_dir / f"{tag}_{pname}_trace.jsonl"
                    p = subprocess.Popen(
                        [str(pbin), str(sock_p), str(trace_p)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    procs.append((pname, p, sock_p, trace_p))
                    plugin_args += ["--plugin", f"{pname}={sock_p}"]
                # Wait briefly for plugin sockets to bind
                deadline = time.monotonic() + 5.0
                for pname, p, sock_p, _ in procs:
                    while not sock_p.exists():
                        if p.poll() is not None or time.monotonic() > deadline:
                            err = p.stderr.read() if p.stderr else ""
                            raise ContractError(
                                f"E_COMPOSE_PLUGIN plugin {pname} failed to bind {sock_p}: {err}"
                            )
                        time.sleep(0.02)

                if resolved_cpu_master == "qemu":
                    assert qemu_bin is not None and resolved_accel is not None
                    qemu_sock = sock_dir / "q.sock"
                    shared_ram = sock_dir / f"{tag}_ram.bin"
                    with shared_ram.open("wb") as rf:
                        rf.truncate(shared_ram_bytes)
                    emu_cmd = [
                        str(emu_bin),
                        str(elf),
                        str(out_dir / f"{tag}-console.log"),
                        str(out_dir / f"{tag}-trace.jsonl"),
                        str(out_dir / f"{tag}-summary.json"),
                        str(max_steps),
                        "--cpu-master",
                        "qemu",
                        "--qemu-sock",
                        str(qemu_sock),
                        "--shared-ram",
                        str(shared_ram),
                        "--quantum-ns",
                        str(resolved_quantum_ns),
                        *(["--vcd-out", str(vcd)] if vcd is not None else []),
                        *plugin_args,
                    ]
                    emu_proc = subprocess.Popen(
                        emu_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    qemu_proc: subprocess.Popen[str] | None = None
                    try:
                        sock_deadline = time.monotonic() + 10.0
                        while not qemu_sock.exists():
                            if emu_proc.poll() is not None or time.monotonic() > sock_deadline:
                                e_out, e_err = emu_proc.communicate(timeout=2)
                                raise ContractError(
                                    f"E_COMPOSE_QEMU_SOCK composed-soc-emu failed to bind "
                                    f"{qemu_sock}: {e_err or e_out}"
                                )
                            time.sleep(0.02)

                        ram_mb = shared_ram_bytes // (1024 * 1024)
                        qemu_cmd = [
                            qemu_bin,
                            "-machine",
                            "virt,memory-backend=uspram",
                            "-accel",
                            resolved_accel,
                            *(["-icount", "shift=3,sleep=off"] if resolved_accel == "tcg" else []),
                            "-cpu",
                            "rv64",
                            "-m",
                            f"{ram_mb}M",
                            "-object",
                            (
                                f"memory-backend-file,id=uspram,size={ram_mb}M,"
                                f"mem-path={shared_ram},share=on"
                            ),
                            "-chardev",
                            f"socket,id=uspbus,path={qemu_sock},server=off",
                            "-global",
                            "virtio-mmio.force-legacy=false",
                            "-device",
                            "virtio-serial-device,max_ports=1",
                            "-device",
                            "virtconsole,chardev=uspbus",
                            "-bios",
                            "none",
                            "-nographic",
                            "-serial",
                            "none",
                            "-monitor",
                            "none",
                            "-device",
                            f"loader,file={elf},cpu-num=0",
                        ]
                        t0 = time.monotonic()
                        qemu_proc = subprocess.Popen(
                            qemu_cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )
                        try:
                            emu_out, emu_err = emu_proc.communicate(timeout=timeout)
                        except subprocess.TimeoutExpired:
                            emu_proc.kill()
                            emu_out, emu_err = emu_proc.communicate()
                        try:
                            q_out, q_err = qemu_proc.communicate(timeout=5)
                        except subprocess.TimeoutExpired:
                            qemu_proc.kill()
                            q_out, q_err = qemu_proc.communicate()
                        if q_err.strip() or q_out.strip():
                            (out_dir / f"{tag}-qemu.log").write_text(q_out + q_err)
                        res = subprocess.CompletedProcess(
                            emu_cmd, emu_proc.returncode, emu_out, emu_err
                        )
                    finally:
                        if qemu_proc is not None and qemu_proc.poll() is None:
                            qemu_proc.kill()
                            qemu_proc.wait()
                        if emu_proc.poll() is None:
                            emu_proc.kill()
                            emu_proc.wait()
                else:
                    cmd = [
                        str(emu_bin),
                        str(elf),
                        str(out_dir / f"{tag}-console.log"),
                        str(out_dir / f"{tag}-trace.jsonl"),
                        str(out_dir / f"{tag}-summary.json"),
                        str(max_steps),
                        *(["--vcd-out", str(vcd)] if vcd is not None else []),
                        *plugin_args,
                    ]
                    t0 = time.monotonic()
                    res = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=timeout, check=False
                    )
            finally:
                for _, p, _, _ in procs:
                    try:
                        p.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        p.wait()

        wall_ms = round((time.monotonic() - t0) * 1000.0, 2)
        traces: dict[str, dict[str, Any]] = {}
        for pname, _, _, trace_p in procs:
            txs = (
                [json.loads(ln) for ln in trace_p.read_text().splitlines() if ln.strip()]
                if trace_p.is_file()
                else []
            )
            traces[pname] = {
                "status": "PASSED"
                if (txs and all(t.get("status") == 0 for t in txs))
                else "FAILED",
                "tx_count": len(txs),
                "trace_sha256": digest(trace_p) if trace_p.is_file() else None,
            }
        return res, traces, wall_ms

    pos_vcd = out_dir / "composed-soc.vcd"
    pos_proc, plugin_traces, pos_wall_ms = _exec_with_plugins(pos_elf, "pos", pos_vcd)
    neg_proc, _, _ = _exec_with_plugins(neg_elf, "neg", None)

    console_path = out_dir / "pos-console.log"
    summary_path = out_dir / "pos-summary.json"
    neg_summary_path = out_dir / "neg-summary.json"
    console_text = console_path.read_text(errors="replace") if console_path.is_file() else ""
    summary = json.loads(summary_path.read_text()) if summary_path.is_file() else {}
    neg_summary = json.loads(neg_summary_path.read_text()) if neg_summary_path.is_file() else {}
    neg_ok = neg_proc.returncode != 0 and neg_summary.get("status") == "FAILED"

    insns_exec = int(summary.get("instructions_executed", 0))
    wall_mips = round(insns_exec / (pos_wall_ms * 1000.0), 3) if pos_wall_ms > 0.0 else 0.0

    qemu_res = (
        _run_qemu_crosscheck(spec, spec_path, out_dir, gcc, crt0_path)
        if verify_qemu_crosscheck
        else None
    )

    coverage = _compute_composed_coverage(
        spec,
        spec_path,
        summary,
        console_text,
        pos_vcd,
        neg_ok,
        plugin_traces,
        qemu_res,
        use_verilator,
        use_live_plugins,
        cpu_master_used=resolved_cpu_master,
    )
    write_json(out_dir / "coverage.json", coverage)

    expected_markers = list(fw["expected_markers"])
    checks = {
        **{m: (m in console_text) for m in expected_markers},
        "exit_zero": pos_proc.returncode == 0 and summary.get("status") == "PASSED",
        "coverage_100_percent": coverage["status"] == "PASSED",
        "negative_failure_detected": neg_ok,
    }
    overall = "PASSED" if all(checks.values()) else "FAILED"
    if resolved_cpu_master == "qemu":
        mode_tag = (
            f"qemu-{resolved_accel}-cpu-master-systemc-verilator-and-usp-bus-v2"
            if (use_verilator or use_live_plugins)
            else f"qemu-{resolved_accel}-cpu-master-systemc-tlm"
        )
    else:
        mode_tag = (
            "hybrid-systemc-verilator-and-live-usp-mmio32-plugins"
            if (use_verilator or use_live_plugins)
            else "pure-systemc-tlm-behavioral"
        )
    toolchain_info: dict[str, Any] = {
        "cxx": {"path": cxx, "version": cxx_ver},
        "systemc_version": sc_ver,
        "verilator_version": verilator_ver,
        "riscv_gcc": {"path": gcc, "version": gcc_ver},
    }
    if qemu_bin is not None:
        toolchain_info["qemu_cpu_master"] = {
            "path": qemu_bin,
            "version": qemu_ver,
            "accel": resolved_accel,
        }
    report: dict[str, Any] = {
        "contract": CONTRACT,
        "platform_version": __version__,
        "design": spec["name"],
        "created_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "status": overall,
        "execution_mode": mode_tag,
        "cpu_master_engine": resolved_cpu_master,
        **({"qemu_accel": resolved_accel} if resolved_accel is not None else {}),
        "quantum_ns": resolved_quantum_ns,
        "toolchain": toolchain_info,
        "provenance": {
            "spec_sha256": ir["spec_sha256"],
            "generated_cpp_sha256": digest(cpp_path),
            "emulator_binary_sha256": digest(emu_bin),
            "positive_elf_sha256": pos_sha,
            "negative_elf_sha256": neg_sha,
            "console_sha256": digest(console_path) if console_path.is_file() else None,
            "vcd_sha256": digest(pos_vcd) if pos_vcd.is_file() else None,
        },
        "exports": ir["exports"],
        "live_plugins": plugin_traces,
        "positive_run": {
            "returncode": pos_proc.returncode,
            "wall_time_ms": pos_wall_ms,
            "wall_time_mips": wall_mips,
            "console_output": console_text,
            "summary": summary,
            "checks": checks,
        },
        "negative_run": {
            "status": neg_summary.get("status", "UNKNOWN"),
            "returncode": neg_proc.returncode,
            "expected_failure_detected": neg_ok,
        },
        "coverage": coverage,
        **({"qemu_crosscheck": qemu_res} if qemu_res is not None else {}),
    }
    write_json(out_dir / "report.json", report)
    (out_dir / "report.md").write_text(_render_composed_report_md(report))
    return report


def calibrate_composed_soc(spec_path: Path, out_dir: Path) -> dict[str, Any]:
    """Compare Hybrid (Verilator RTL + Live Plugin) vs Pure SystemC-TLM."""
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ContractError("E_COMPOSE_OUT choose a fresh calibration output directory")
    ready, missing = has_unified_toolchain(require_verilator=True)
    if not ready:
        out_dir.mkdir(parents=True)
        blocked = {
            "contract": "usp-composed-calibration-1",
            "status": "BLOCKED",
            "missing_tools": missing,
        }
        write_json(out_dir / "calibration.json", blocked)
        return blocked

    spec = load_composed_spec(spec_path)
    hyb = run_composed_soc(
        spec_path,
        out_dir / "hybrid-rtl-plugin",
        mode="hybrid",
        cpu_master="systemc-rv64",
        verify_qemu_crosscheck=False,
    )
    tlm = run_composed_soc(
        spec_path,
        out_dir / "pure-tlm",
        mode="tlm",
        cpu_master="systemc-rv64",
        verify_qemu_crosscheck=False,
    )
    hs = hyb["positive_run"]["summary"]
    ts = tlm["positive_run"]["summary"]
    h_time = float(hs["systemc_time_ps"])
    t_time = float(ts["systemc_time_ps"])
    cal = evaluate_calibration_report(
        roi_name=f"{spec['name'].lower()}_hybrid_rtl_plugin_vs_tlm",
        reference_source="native-rtl",
        model_source="qemu-analytical-estimate",
        reference_cycles=h_time / 4_000.0,
        estimated_cycles=t_time / 4_000.0,
        max_relative_error=0.10,
    )
    parity = (
        hyb["status"] == "PASSED"
        and tlm["status"] == "PASSED"
        and hyb["positive_run"]["console_output"] == tlm["positive_run"]["console_output"]
        and hs.get("axi4_stream_packets_total") == ts.get("axi4_stream_packets_total")
        and hs.get("cdc_crossings") == ts.get("cdc_crossings")
        and hs.get("dma_bytes_transferred") == ts.get("dma_bytes_transferred")
        and hs.get("jtag_axi4_tx") == ts.get("jtag_axi4_tx")
        and hs.get("l2_dma_snoops") == ts.get("l2_dma_snoops")
    )
    res: dict[str, Any] = {
        "contract": "usp-composed-calibration-1",
        "design": spec["name"],
        "status": "PASSED" if parity and cal["within_budget"] else "FAILED",
        "functional_parity": parity,
        "hybrid_systemc_time_ps": h_time,
        "tlm_systemc_time_ps": t_time,
        "rtl_cycles": {k: v for k, v in hs.get("rtl_cycles", {}).items() if v > 0},
        "live_plugins": hyb.get("live_plugins", {}),
        "calibration": cal,
    }
    write_json(out_dir / "calibration.json", res)
    return res


def audit_composed_soc(spec_path: Path) -> dict[str, Any]:
    """Return a structured multi-platform composition & binding capability audit."""
    spec = load_composed_spec(spec_path)
    lock = _render_binding_lock(spec, spec_path)
    rtl_protocols: dict[str, str] = {}
    plugin_protocols: dict[str, str] = {}
    for dev in spec["devices"]:
        if dev["binding"] == "verilator-rtl":
            rmeta = _device_rtl_meta(dev, spec_path)
            rtl_protocols[str(dev["name"])] = rmeta["protocol"] if rmeta else "mmio32"
        elif dev["binding"] == "usp-plugin":
            pkg, _ = _device_plugin_info(dev, spec_path)
            plugin_protocols[str(dev["name"])] = str(pkg["interface"]["protocol"])
    return {
        "contract": "usp-composed-audit-1",
        "design": spec["name"],
        "cpu_master_engine": str(spec["cpu"].get("master_engine", "systemc-rv64")),
        "quantum_ns": int(spec["cpu"].get("quantum_ns", 100_000)),
        "clock_domains": len(spec["clock_domains"]),
        "fabrics": len(spec["fabrics"]),
        "cdc_bridges": len(spec["cdc_bridges"]),
        "devices": len(spec["devices"]),
        "axi4_streams": len(spec.get("streams", [])),
        "provider_summary": lock["provider_summary"],
        "verilator_rtl_blocks": [
            d["name"] for d in spec["devices"] if d["binding"] == "verilator-rtl"
        ],
        "rtl_protocols": rtl_protocols,
        "live_usp_plugins": [d["name"] for d in spec["devices"] if d["binding"] == "usp-plugin"],
        "plugin_protocols": plugin_protocols,
        "mixed_signal_blocks": [
            d["name"] for d in spec["devices"] if d["binding"] == "mixed-signal-tlm"
        ],
        "cross_engine_exports": [
            "ipxact-2022",
            "qemu-dts-and-live-rv64",
            "gem5-system-py",
            "chipyard-scala",
        ],
        "honest_boundaries": [
            (
                "QEMU CPU master mode executes guest RV64 instructions natively via stock "
                "qemu-system-riscv64 (TCG on x86_64/arm64 hosts, KVM only on riscv64 hosts with "
                "/dev/kvm) over zero-copy mmap RAM and VirtIO-MMIO usp-bus-v2 quantum sync."
            ),
            (
                "Verilator RTL blocks auto-adapt pin-level AXI4, AXI4-Lite, OpenTitan TL-UL, "
                "and MMIO32 SystemVerilog modules cycle-by-cycle; upstream Chisel/Scala "
                "elaboration to SystemVerilog requires sbt/firtool."
            ),
            (
                "gem5 system script binds NativeSocDevice over usp-bus-v2 (backward-compatible "
                "with usp-mmio32-v1), supporting reverse DMA, 64-bit bursts, and quantum time "
                "sync; live gem5 execution requires building gem5.opt via scripts/setup_gem5.sh."
            ),
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="usp compose")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        (
            "plan",
            "validate multiplatform-soc-1 and emit IR, lockfile, C++, IP-XACT, QEMU/gem5/Chipyard",
        ),
        (
            "run",
            "build & run composed SoC with QEMU/SystemC CPU, Verilator RTL & live socket plugins",
        ),
        ("calibrate", "calibrate Hybrid (Verilator RTL + Live Plugin) vs Pure SystemC-TLM"),
        ("audit", "audit providers, clock domains, crossbars, streams, plugins & scope boundaries"),
    ):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("spec", type=Path)
        if name != "audit":
            sub.add_argument("--out", type=Path, required=True)
        if name == "run":
            sub.add_argument("--mode", choices=("auto", "hybrid", "tlm"), default="auto")
            sub.add_argument(
                "--cpu-master",
                choices=("auto", "systemc-rv64", "qemu"),
                default="auto",
            )
            sub.add_argument(
                "--qemu-accel",
                choices=("auto", "tcg", "kvm"),
                default="auto",
            )
            sub.add_argument("--quantum-ns", type=int, default=None)
            sub.add_argument("--no-qemu-crosscheck", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "audit":
            print(json.dumps(audit_composed_soc(args.spec), indent=2, sort_keys=True))
            return 0
        if args.command == "plan":
            ir = plan_composed_soc(args.spec, args.out)
            print(
                json.dumps(
                    {
                        "status": "PASSED",
                        "design": ir["name"],
                        "devices": ir["devices"],
                        "out": str(args.out),
                    },
                    indent=2,
                )
            )
            return 0
        if args.command == "calibrate":
            cal = calibrate_composed_soc(args.spec, args.out)
            print(json.dumps(cal, indent=2, sort_keys=True))
            return 0 if cal["status"] == "PASSED" else 1
        rep = run_composed_soc(
            args.spec,
            args.out,
            mode=args.mode,
            cpu_master=args.cpu_master,
            qemu_accel=args.qemu_accel,
            quantum_ns=args.quantum_ns,
            verify_qemu_crosscheck=not args.no_qemu_crosscheck,
        )
        print(
            json.dumps(
                {
                    "status": rep["status"],
                    "design": rep["design"],
                    "execution_mode": rep.get("execution_mode"),
                    "cpu_master_engine": rep.get("cpu_master_engine"),
                    "coverage_percent": rep.get("coverage", {}).get("coverage_percent"),
                    "report": str(args.out / "report.json"),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if rep["status"] == "PASSED" else 1
    except (ContractError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}))
        return 2
