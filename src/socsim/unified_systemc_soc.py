"""Single Unified Multi-Platform Dual-Hart SMP SystemC/TLM-2.0 + 6x Verilator RTL SoC runner.

Compiles one heterogeneous SMP SoC spec (`unified-systemc-soc-1`) into a shared address/IRQ/NoC
header (`unified_soc_map.h`), an IR-generated C++ NoC routing table (`unified_soc_routes.h`), and
a dual-hart linker script (`link.ld`); compiles SIX synthesizable SystemVerilog RTL blocks via
Verilator 5.032 (`mac_npu_rtl.sv`, `sha256_rtl.sv`, `aes128_rtl.sv`, `spi_ctrl_rtl.sv`,
`i2c_ctrl_rtl.sv`, `l2_cache_rtl.sv`); links them into one SystemC/TLM-2.0 2x2 Mesh NoC top with
Dual-Hart SMP RV64IMA_Zicsr cores, 3rd-master DMA, PMP Bus Guard, Chipyard GCD, OpenTitan GPIO,
QEMU-compatible UART/CLINT/PLIC, GDB RSP debug stub, and IEEE 1364 VCD waveform dump; executes
real RV64IMA_Zicsr bare-metal SMP software; computes 24-bin Functional & Assertion Coverage
(`coverage.json`); and runs M7 6x RTL-vs-TLM calibration.
"""

from __future__ import annotations

import argparse
import datetime
import json
import shlex
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from .domain import ContractError
from .execution import execute_build
from .ipxact import digest
from .project import keys, read_yaml
from .runner import tool_version, write_json
from .v04_contracts import evaluate_calibration_report
from .version import __version__

CONTRACT = "unified-systemc-soc-1"
REQUIRED_MODELS = frozenset(
    {
        "rom",
        "finisher",
        "ram",
        "clint",
        "plic",
        "uart",
        "chipyard.gcd",
        "opentitan.gpio",
        "user.dma_engine",
        "user.sha256_crypto",
        "user.aes128_crypto",
        "user.spi_ctrl",
        "user.i2c_ctrl",
        "user.l2_cache",
        "user.mac_npu",
        "user.pmp_guard",
    }
)
MODEL_TO_TARGET_ENUM = {
    "ram": ("USOC_TGT_RAM", False),
    "rom": ("USOC_TGT_ROM", False),
    "finisher": ("USOC_TGT_FINISHER", True),
    "user.l2_cache": ("USOC_TGT_L2", True),
    "clint": ("USOC_TGT_CLINT", True),
    "plic": ("USOC_TGT_PLIC", True),
    "uart": ("USOC_TGT_UART", True),
    "uart_aux": ("USOC_TGT_UART_AUX", True),
    "user.spi_ctrl": ("USOC_TGT_SPI", True),
    "user.i2c_ctrl": ("USOC_TGT_I2C", True),
    "chipyard.gcd": ("USOC_TGT_GCD", True),
    "opentitan.gpio": ("USOC_TGT_GPIO", True),
    "user.dma_engine": ("USOC_TGT_DMA", True),
    "user.sha256_crypto": ("USOC_TGT_SHA256", True),
    "user.aes128_crypto": ("USOC_TGT_AES128", True),
    "user.mac_npu": ("USOC_TGT_NPU", True),
    "user.lpu_engine": ("USOC_TGT_LPU", True),
    "user.cluster_mailbox": ("USOC_TGT_MAILBOX", True),
    "sram": ("USOC_TGT_AI_SRAM", False),
    "user.pmp_guard": ("USOC_TGT_PMP_GUARD", True),
}
BRIDGE_ENUM_MAP = {
    "noc_backbone": "USOC_BRIDGE_NOC",
    "axi_ai_bridge": "USOC_BRIDGE_AXI_AI",
    "axi_sec_bridge": "USOC_BRIDGE_AXI_SEC",
    "apb_periph_bridge": "USOC_BRIDGE_APB_PERIPH",
}
VERILATOR_RTL_MODULES = (
    ("mac_npu_rtl", "rtl/mac_npu_rtl.sv"),
    ("sha256_rtl", "rtl/sha256_rtl.sv"),
    ("aes128_rtl", "rtl/aes128_rtl.sv"),
    ("spi_ctrl_rtl", "rtl/spi_ctrl_rtl.sv"),
    ("i2c_ctrl_rtl", "rtl/i2c_ctrl_rtl.sv"),
    ("l2_cache_rtl", "rtl/l2_cache_rtl.sv"),
)
LPU_RTL_MODULE = ("lpu_rtl", "rtl/lpu_rtl.sv")
EXPECTED_POSITIVE_MARKERS = (
    "UNIFIED_SOC_BOOT_OK",
    "RISCV_PMP_CSR_OK",
    "COREMARK_CRC32_ROI_OK",
    "SMP_HART1_IPI_WAKE_OK",
    "SMP_ATOMIC_BARRIER_OK",
    "CHIPYARD_GCD_IRQ_OK",
    "OPENTITAN_GPIO_IRQ_OK",
    "MAC_NPU_DOT_IRQ_OK",
    "NPU_MATMUL_2X2_IRQ_OK",
    "SHA256_FIPS_KAT_IRQ_OK",
    "AES128_FIPS197_IRQ_OK",
    "SPI_CTRL_RTL_IRQ_OK",
    "I2C_CTRL_RTL_IRQ_OK",
    "PMP_GUARD_FAULT_IRQ_OK",
    "NOC_DMA_MASTER_IRQ_OK",
    "DMA_PMP_BLOCK_OK",
    "L2_CACHE_COHERENCY_OK",
    "UNIFIED_SOC_ALL_PASS",
)


def expected_markers_for_spec(spec: dict[str, Any]) -> tuple[str, ...]:
    """Return all mandatory guest console markers for a validated unified SoC spec."""
    markers = list(EXPECTED_POSITIVE_MARKERS)
    models = {str(d["model"]) for d in spec["devices"]}
    if int(spec["cpu"]["harts"]) >= 4:
        markers.extend(["CLUSTER1_IPI_WAKE_OK", "MULTI_CLUSTER_ATOMIC_OK"])
    if "user.cluster_mailbox" in models:
        markers.append("CLUSTER_MAILBOX_IPC_OK")
    if "user.lpu_engine" in models:
        markers.extend(["LPU_LINEAR_RELU_IRQ_OK", "LPU_TOKEN_ARGMAX_IRQ_OK"])
    if "sram" in models:
        markers.append("AI_SCRATCH_DMA_OK")
    if "uart_aux" in models:
        markers.append("UART_DEBUG_TELEMETRY_OK")
    return tuple(markers)


def resolve_unified_src_dir(spec_path: Path) -> Path:
    """Resolve the unified_systemc C++/RTL/SW source directory for a unified spec."""
    local = spec_path.resolve().parent / "unified_systemc"
    if (local / "unified_systemc_soc.cpp").is_file():
        return local.resolve()
    repo_fallback = Path(__file__).resolve().parents[2] / "specs/multiplatform/unified_systemc"
    if (repo_fallback / "unified_systemc_soc.cpp").is_file():
        return repo_fallback.resolve()
    raise ContractError(f"E_UNIFIED_SRC missing SystemC emulator source near {spec_path}")


def load_unified_spec(path: Path) -> dict[str, Any]:
    """Validate a unified-systemc-soc-1 specification and return normalized metadata."""
    path = path.resolve()
    data = read_yaml(path)
    if not isinstance(data, dict):
        raise ContractError("E_UNIFIED_SPEC unified SoC spec must be a mapping")
    keys(data, {"schema_version", "name", "noc", "cpu", "devices"}, "unified SoC spec")
    if data["schema_version"] != CONTRACT:
        raise ContractError(f"E_UNIFIED_SPEC unsupported schema_version: {data['schema_version']}")

    noc = data["noc"]
    allowed_noc_keys = {
        "topology",
        "rows",
        "cols",
        "hop_latency_ns",
        "link_latency_ns",
        "cpu_router",
        "hart1_router",
    } | (set(noc) & {"hart2_router", "hart3_router", "bridges"})
    keys(noc, allowed_noc_keys, "unified noc")
    rows, cols = int(noc["rows"]), int(noc["cols"])
    if rows != 2 or cols != 2:
        raise ContractError("E_UNIFIED_NOC unified SystemC emulator requires a 2x2 mesh topology")

    cpu = data["cpu"]
    harts = int(cpu.get("harts", 0))
    if harts not in {2, 4}:
        raise ContractError("E_UNIFIED_CPU unified SMP emulator expects harts in {2, 4}")
    allowed_cpu_keys = {
        "name",
        "harts",
        "isa",
        "privilege",
        "pmp_regions",
        "clock_hz",
        "timebase_hz",
        "entry",
        "stack_top",
        "hart1_stack_top",
        "protected_scratch",
        "protected_size",
        "dma_src_buf",
        "dma_dst_buf",
        "smp_shared_mem",
        "model_disclosure",
    } | (set(cpu) & {"hart2_stack_top", "hart3_stack_top", "clusters"})
    keys(cpu, allowed_cpu_keys, "unified cpu")

    required_routers = ["cpu_router", "hart1_router"]
    if harts == 4:
        required_routers.extend(["hart2_router", "hart3_router"])
        for req_rk in ("hart2_router", "hart3_router"):
            if req_rk not in noc:
                raise ContractError(f"E_UNIFIED_NOC 4-hart spec requires {req_rk}")
        for req_sk in ("hart2_stack_top", "hart3_stack_top"):
            if req_sk not in cpu:
                raise ContractError(f"E_UNIFIED_CPU 4-hart spec requires {req_sk}")
        clusters = cpu.get("clusters")
        if clusters is not None:
            if not isinstance(clusters, list) or len(clusters) < 2:
                raise ContractError("E_UNIFIED_CPU multi-cluster spec requires >= 2 clusters")
            seen_harts: set[int] = set()
            for cl in clusters:
                if not isinstance(cl, dict) or "name" not in cl or "harts" not in cl:
                    raise ContractError("E_UNIFIED_CPU cluster entry requires name and harts")
                for hid in cl["harts"]:
                    h_int = int(hid)
                    if h_int in seen_harts or not (0 <= h_int < harts):
                        raise ContractError(f"E_UNIFIED_CPU invalid/duplicate cluster hart {h_int}")
                    seen_harts.add(h_int)
            if len(seen_harts) != harts:
                raise ContractError("E_UNIFIED_CPU clusters must partition all declared harts")

    for rkey in required_routers:
        rx, ry = int(noc[rkey][0]), int(noc[rkey][1])
        if not (0 <= rx < cols and 0 <= ry < rows):
            raise ContractError(f"E_UNIFIED_NOC {rkey} out of mesh bounds")

    declared_bridges: dict[str, int] = {"noc_backbone": 0}
    if "bridges" in noc:
        if not isinstance(noc["bridges"], list):
            raise ContractError("E_UNIFIED_NOC bridges must be a list")
        for br in noc["bridges"]:
            if not isinstance(br, dict):
                raise ContractError("E_UNIFIED_NOC bridge entry must be a mapping")
            for req_bk in ("name", "protocol", "latency_ns", "router"):
                if req_bk not in br:
                    raise ContractError(f"E_UNIFIED_NOC bridge missing required field: {req_bk}")
            bname = str(br["name"])
            if bname not in BRIDGE_ENUM_MAP or bname == "noc_backbone":
                raise ContractError(f"E_UNIFIED_NOC unsupported bridge name: {bname}")
            if bname in declared_bridges:
                raise ContractError(f"E_UNIFIED_NOC duplicate bridge name: {bname}")
            lat = int(br["latency_ns"])
            if lat < 0 or lat > 1000:
                raise ContractError(f"E_UNIFIED_NOC bridge {bname} invalid latency_ns: {lat}")
            bx, by = int(br["router"][0]), int(br["router"][1])
            if not (0 <= bx < cols and 0 <= by < rows):
                raise ContractError(f"E_UNIFIED_NOC bridge {bname} router out of bounds")
            declared_bridges[bname] = lat

    devices = data["devices"]
    if not isinstance(devices, list) or not devices:
        raise ContractError("E_UNIFIED_SPEC devices must be a non-empty list")

    seen_names: set[str] = set()
    seen_models: set[str] = set()
    seen_irqs: set[int] = set()
    windows: list[tuple[int, int, str]] = []
    for dev in devices:
        if not isinstance(dev, dict):
            raise ContractError("E_UNIFIED_SPEC device must be a mapping")
        for req in ("name", "platform", "model", "base", "size", "router"):
            if req not in dev:
                raise ContractError(f"E_UNIFIED_SPEC device missing required field: {req}")
        name = str(dev["name"])
        model = str(dev["model"])
        if model not in MODEL_TO_TARGET_ENUM:
            raise ContractError(f"E_UNIFIED_SPEC {name}: unsupported device model {model}")
        base = int(dev["base"])
        size = int(dev["size"])
        rx, ry = int(dev["router"][0]), int(dev["router"][1])
        backend = str(dev.get("backend", "systemc-tlm"))
        if backend not in {"systemc-tlm", "verilator-rtl"}:
            raise ContractError(f"E_UNIFIED_SPEC {name}: unsupported backend {backend}")
        bridge = str(dev.get("bridge", "noc_backbone"))
        if bridge not in declared_bridges:
            raise ContractError(f"E_UNIFIED_SPEC {name}: undeclared bus bridge {bridge}")
        if name in seen_names:
            raise ContractError(f"E_UNIFIED_SPEC duplicate device name: {name}")
        if base % 4096 != 0 or size <= 0 or size % 4096 != 0:
            raise ContractError(f"E_UNIFIED_SPEC {name}: base/size must be 4 KiB aligned")
        if not (0 <= rx < cols and 0 <= ry < rows):
            raise ContractError(f"E_UNIFIED_SPEC {name}: router [{rx},{ry}] out of 2x2 bounds")
        if "irq" in dev:
            irq = int(dev["irq"])
            if not 1 <= irq <= 31 or irq in seen_irqs:
                raise ContractError(f"E_UNIFIED_SPEC {name}: duplicate or invalid PLIC IRQ {irq}")
            seen_irqs.add(irq)
        seen_names.add(name)
        seen_models.add(model)
        windows.append((base, base + size, name))

    missing = REQUIRED_MODELS - seen_models
    if missing:
        raise ContractError(f"E_UNIFIED_SPEC missing required unified IP models: {sorted(missing)}")

    windows.sort()
    for (_a0, a1, aname), (b0, _b1, bname) in zip(windows, windows[1:], strict=False):
        if b0 < a1:
            raise ContractError(f"E_UNIFIED_SPEC overlapping address windows: {aname} and {bname}")

    dram = next(d for d in devices if d["model"] == "ram")
    dram_base, dram_end = int(dram["base"]), int(dram["base"]) + int(dram["size"])
    entry = int(cpu["entry"])
    stack_top = int(cpu["stack_top"])
    h1_stack = int(cpu["hart1_stack_top"])
    scratch = int(cpu["protected_scratch"])
    scratch_size = int(cpu["protected_size"])
    dma_src = int(cpu["dma_src_buf"])
    dma_dst = int(cpu["dma_dst_buf"])
    smp_mem = int(cpu["smp_shared_mem"])
    if harts == 4:
        h2_stack = int(cpu["hart2_stack_top"])
        h3_stack = int(cpu["hart3_stack_top"])
        if not (dram_base <= entry < h3_stack < h2_stack < h1_stack < stack_top <= dram_end):
            raise ContractError("E_UNIFIED_SPEC 4-hart stacks outside main DRAM or overlapping")
    elif not (dram_base <= entry < h1_stack < stack_top <= dram_end):
        raise ContractError("E_UNIFIED_SPEC entry/stacks outside main DRAM or overlapping")
    if not (dram_base <= scratch and scratch + scratch_size <= dram_end):
        raise ContractError("E_UNIFIED_SPEC protected_scratch outside main DRAM")
    if scratch < stack_top:
        raise ContractError("E_UNIFIED_SPEC protected_scratch overlaps stack/code region")
    if not (
        dram_base <= dma_src < dram_end
        and dram_base <= dma_dst < dram_end
        and dram_base <= smp_mem < dram_end
    ):
        raise ContractError("E_UNIFIED_SPEC DMA/SMP buffers outside main DRAM")

    return data


def _render_map_header(spec: dict[str, Any]) -> str:
    noc = spec["noc"]
    cpu = spec["cpu"]
    harts = int(cpu["harts"])
    cluster_count = len(cpu.get("clusters", [])) if cpu.get("clusters") else 1
    lines = [
        "/* Generated from unified-systemc-soc-1 specification; DO NOT EDIT BY HAND. */",
        "#ifndef USOC_UNIFIED_SOC_MAP_H",
        "#define USOC_UNIFIED_SOC_MAP_H",
        f"#define USOC_HART_COUNT {harts}U",
        f"#define USOC_CLUSTER_COUNT {cluster_count}U",
        f"#define USOC_NOC_HOP_NS {int(noc['hop_latency_ns'])}U",
        f"#define USOC_NOC_LINK_NS {int(noc['link_latency_ns'])}U",
        f"#define USOC_CPU_RX {int(noc['cpu_router'][0])}",
        f"#define USOC_CPU_RY {int(noc['cpu_router'][1])}",
        f"#define USOC_HART1_RX {int(noc['hart1_router'][0])}",
        f"#define USOC_HART1_RY {int(noc['hart1_router'][1])}",
    ]
    if harts >= 4:
        lines.extend(
            [
                f"#define USOC_HART2_RX {int(noc['hart2_router'][0])}",
                f"#define USOC_HART2_RY {int(noc['hart2_router'][1])}",
                f"#define USOC_HART3_RX {int(noc['hart3_router'][0])}",
                f"#define USOC_HART3_RY {int(noc['hart3_router'][1])}",
            ]
        )
    lines.extend(
        [
            f"#define USOC_CPU_ENTRY 0x{int(cpu['entry']):08X}ULL",
            f"#define USOC_STACK_TOP 0x{int(cpu['stack_top']):08X}ULL",
            f"#define USOC_HART1_STACK_TOP 0x{int(cpu['hart1_stack_top']):08X}ULL",
            f"#define USOC_PROTECTED_SCRATCH_BASE 0x{int(cpu['protected_scratch']):08X}ULL",
            f"#define USOC_PROTECTED_SCRATCH_SIZE 0x{int(cpu['protected_size']):08X}ULL",
            f"#define USOC_DMA_SRC_BUF 0x{int(cpu['dma_src_buf']):08X}ULL",
            f"#define USOC_DMA_DST_BUF 0x{int(cpu['dma_dst_buf']):08X}ULL",
            f"#define USOC_SMP_SHARED_MEM 0x{int(cpu['smp_shared_mem']):08X}ULL",
        ]
    )
    for dev in spec["devices"]:
        key = str(dev["name"]).upper()
        model = str(dev["model"])
        lines.append(f"#define USOC_{key}_BASE 0x{int(dev['base']):08X}ULL")
        lines.append(f"#define USOC_{key}_SIZE 0x{int(dev['size']):08X}ULL")
        lines.append(f"#define USOC_{key}_RX {int(dev['router'][0])}")
        lines.append(f"#define USOC_{key}_RY {int(dev['router'][1])}")
        if "irq" in dev:
            lines.append(f"#define USOC_{key}_IRQ {int(dev['irq'])}U")
        if model == "uart":
            lines.append(f"#define USOC_PRIMARY_UART_BASE 0x{int(dev['base']):08X}ULL")
            lines.append(f"#define USOC_PRIMARY_UART_IRQ {int(dev.get('irq', 10))}U")
        elif model == "uart_aux":
            lines.append("#define USOC_HAS_UART_AUX 1")
            lines.append(f"#define USOC_UART_AUX_BASE 0x{int(dev['base']):08X}ULL")
            lines.append(f"#define USOC_UART_AUX_IRQ {int(dev.get('irq', 11))}U")
        elif model == "user.lpu_engine":
            lines.append("#define USOC_HAS_LPU 1")
            lines.append(f"#define USOC_LPU_BASE 0x{int(dev['base']):08X}ULL")
            lines.append(f"#define USOC_LPU_IRQ {int(dev.get('irq', 22))}U")
        elif model == "user.cluster_mailbox":
            lines.append("#define USOC_HAS_MAILBOX 1")
            lines.append(f"#define USOC_MAILBOX_BASE 0x{int(dev['base']):08X}ULL")
            lines.append(f"#define USOC_MAILBOX_IRQ {int(dev.get('irq', 23))}U")
        elif model == "sram":
            lines.append("#define USOC_HAS_AI_SRAM 1")
            lines.append(f"#define USOC_AI_SRAM_BASE 0x{int(dev['base']):08X}ULL")
            lines.append(f"#define USOC_AI_SRAM_SIZE 0x{int(dev['size']):08X}ULL")
    lines.append("#endif /* USOC_UNIFIED_SOC_MAP_H */")
    return "\n".join(lines) + "\n"


def _render_routes_header(spec: dict[str, Any]) -> str:
    """Emit IR-generated C++ NoC + Hierarchical Bus Bridge routing table (M6)."""
    bridge_lat = {"noc_backbone": 0}
    for br in spec["noc"].get("bridges", []):
        bridge_lat[str(br["name"])] = int(br["latency_ns"])
    lines = [
        "/* IR-generated 2x2 Mesh NoC + Bus Bridge routing table; DO NOT EDIT BY HAND. */",
        "#ifndef USOC_UNIFIED_SOC_ROUTES_H",
        "#define USOC_UNIFIED_SOC_ROUTES_H",
        "#include <cstdint>",
        "enum UsocTargetId {",
        "    USOC_TGT_RAM = 0,",
        "    USOC_TGT_ROM,",
        "    USOC_TGT_FINISHER,",
        "    USOC_TGT_L2,",
        "    USOC_TGT_CLINT,",
        "    USOC_TGT_PLIC,",
        "    USOC_TGT_UART,",
        "    USOC_TGT_UART_AUX,",
        "    USOC_TGT_SPI,",
        "    USOC_TGT_I2C,",
        "    USOC_TGT_GCD,",
        "    USOC_TGT_GPIO,",
        "    USOC_TGT_DMA,",
        "    USOC_TGT_SHA256,",
        "    USOC_TGT_AES128,",
        "    USOC_TGT_NPU,",
        "    USOC_TGT_LPU,",
        "    USOC_TGT_MAILBOX,",
        "    USOC_TGT_AI_SRAM,",
        "    USOC_TGT_PMP_GUARD",
        "};",
        "enum UsocBridgeId {",
        "    USOC_BRIDGE_NOC = 0,",
        "    USOC_BRIDGE_AXI_AI = 1,",
        "    USOC_BRIDGE_AXI_SEC = 2,",
        "    USOC_BRIDGE_APB_PERIPH = 3",
        "};",
        "struct UsocRouteEntry {",
        "    const char *name;",
        "    uint64_t base;",
        "    uint64_t size;",
        "    int rx;",
        "    int ry;",
        "    UsocTargetId target;",
        "    bool log_mmio;",
        "    UsocBridgeId bridge;",
        "    uint32_t bridge_latency_ns;",
        "};",
        "static const UsocRouteEntry USOC_ROUTE_TABLE[] = {",
    ]
    for dev in spec["devices"]:
        enum_name, log_mmio = MODEL_TO_TARGET_ENUM[str(dev["model"])]
        bname = str(dev.get("bridge", "noc_backbone"))
        benum = BRIDGE_ENUM_MAP[bname]
        blat = bridge_lat.get(bname, 0)
        lines.append(
            f'    {{"{dev["name"]}", 0x{int(dev["base"]):08X}ULL, 0x{int(dev["size"]):08X}ULL, '
            f"{int(dev['router'][0])}, {int(dev['router'][1])}, {enum_name}, "
            f"{'true' if log_mmio else 'false'}, {benum}, {blat}U}},"
        )
    lines.append("};")
    lines.append(f"static const unsigned USOC_ROUTE_COUNT = {len(spec['devices'])}U;")
    lines.append("#endif /* USOC_UNIFIED_SOC_ROUTES_H */")
    return "\n".join(lines) + "\n"


def _render_linker_script(spec: dict[str, Any]) -> str:
    cpu = spec["cpu"]
    dram = next(d for d in spec["devices"] if d["model"] == "ram")
    h1_stack = int(cpu["hart1_stack_top"])
    h2_stack = int(cpu.get("hart2_stack_top", h1_stack))
    h3_stack = int(cpu.get("hart3_stack_top", h1_stack))
    return (
        "OUTPUT_ARCH(riscv)\n"
        "ENTRY(_start)\n"
        "MEMORY {\n"
        f"  RAM (rwx) : ORIGIN = 0x{int(dram['base']):08X}, LENGTH = 0x{int(dram['size']):08X}\n"
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
        f"  PROVIDE(_hart1_stack_top = 0x{h1_stack:08X});\n"
        f"  PROVIDE(_hart2_stack_top = 0x{h2_stack:08X});\n"
        f"  PROVIDE(_hart3_stack_top = 0x{h3_stack:08X});\n"
        "}\n"
    )


def generate_unified_artifacts(spec_path: Path, out_dir: Path) -> dict[str, Any]:
    """Validate spec and emit map header, IR routing table, linker script, and IR JSON."""
    spec_path = spec_path.resolve()
    out_dir = out_dir.resolve()
    spec = load_unified_spec(spec_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    header_path = out_dir / "unified_soc_map.h"
    routes_path = out_dir / "unified_soc_routes.h"
    linker_path = out_dir / "link.ld"
    header_path.write_text(_render_map_header(spec))
    routes_path.write_text(_render_routes_header(spec))
    linker_path.write_text(_render_linker_script(spec))

    ir = {
        "contract": CONTRACT,
        "name": spec["name"],
        "spec_path": str(spec_path),
        "spec_sha256": digest(spec_path),
        "noc": spec["noc"],
        "cpu": spec["cpu"],
        "devices": spec["devices"],
        "generated_files": {
            "unified_soc_map.h": digest(header_path),
            "unified_soc_routes.h": digest(routes_path),
            "link.ld": digest(linker_path),
        },
    }
    write_json(out_dir / "unified-soc-ir.json", ir)
    return ir


def has_unified_toolchain(*, require_verilator: bool = False) -> tuple[bool, list[str]]:
    """Return (ready, missing_reasons) for live SystemC + RV64 GCC (+ optional Verilator)."""
    missing: list[str] = []
    tools = ["c++", "pkg-config", "riscv64-unknown-elf-gcc"]
    if require_verilator:
        tools.append("verilator")
    for tool in tools:
        if shutil.which(tool) is None:
            missing.append(tool)
    if shutil.which("pkg-config") is not None:
        probe = subprocess.run(
            ["pkg-config", "--exists", "systemc"], capture_output=True, check=False
        )
        if probe.returncode != 0:
            missing.append("libsystemc-dev (pkg-config systemc)")
    return (len(missing) == 0), missing


def _compile_firmware(
    gcc: str,
    src_dir: Path,
    inc_dir: Path,
    c_file: str,
    out_elf: Path,
    log_path: Path,
) -> str:
    cmd = [
        gcc,
        "-march=rv64ima_zicsr",
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
        "-T",
        str(inc_dir / "link.ld"),
        str(src_dir / "crt0.S"),
        str(src_dir / c_file),
        "-o",
        str(out_elf),
    ]
    execute_build(cmd, inc_dir, log_path, 60)
    return digest(out_elf)


def _build_verilator_rtl_blocks(
    src_dir: Path, out_dir: Path, *, include_lpu: bool = False
) -> tuple[list[str], list[str], str]:
    """Compile all 6 (or 7 with LPU) synthesizable SystemVerilog RTL blocks with Verilator 5.x."""
    verilator_bin, verilator_ver = tool_version("verilator")
    vroot = subprocess.check_output(
        [verilator_bin, "--getenv", "VERILATOR_ROOT"], text=True, timeout=15
    ).strip()

    includes = [
        "-DUSOC_ENABLE_VERILATOR_RTL",
        *(["-DUSOC_ENABLE_LPU_RTL"] if include_lpu else []),
        "-isystem",
        f"{vroot}/include",
        "-isystem",
        f"{vroot}/include/vltstd",
    ]
    link_objs: list[str] = []
    verilated_runtime_added = False

    modules = list(VERILATOR_RTL_MODULES)
    if include_lpu:
        modules.append(LPU_RTL_MODULE)

    for mod_name, rel_sv in modules:
        rtl_sv = src_dir / rel_sv
        if not rtl_sv.is_file():
            raise ContractError(f"E_UNIFIED_RTL missing SystemVerilog source: {rtl_sv}")
        mdir = out_dir / f"verilator_{mod_name}"
        mdir.mkdir(parents=True, exist_ok=True)
        prefix = f"V{mod_name}"
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
            str(rtl_sv),
        ]
        execute_build(cmd, out_dir, out_dir / f"verilator-{mod_name}.log", 120)
        includes += ["-isystem", str(mdir)]

        lib_model = mdir / f"lib{prefix}.a"
        alt_model = mdir / f"{prefix}__ALL.a"
        if lib_model.is_file():
            link_objs.append(str(lib_model))
        elif alt_model.is_file():
            link_objs.append(str(alt_model))
        else:
            raise ContractError(f"E_UNIFIED_RTL Verilator produced no archive for {mod_name}")

        if not verilated_runtime_added:
            lib_verilated = mdir / "libverilated.a"
            if lib_verilated.is_file():
                link_objs.append(str(lib_verilated))
            else:
                link_objs.append(f"{vroot}/include/verilated.cpp")
                threads_cpp = Path(f"{vroot}/include/verilated_threads.cpp")
                if threads_cpp.is_file():
                    link_objs.append(str(threads_cpp))
            verilated_runtime_added = True

    return includes, link_objs, verilator_ver


def _gdb_send(sock: socket.socket, payload: str) -> str:
    csum = sum(payload.encode("ascii")) & 0xFF
    sock.sendall(f"${payload}#{csum:02x}".encode("ascii"))
    buf = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        if b"#" in buf:
            idx = buf.index(b"#")
            if len(buf) >= idx + 3:
                start = buf.index(b"$") + 1
                sock.sendall(b"+")
                return buf[start:idx].decode("ascii")
    return ""


def verify_gdb_rsp(emu_bin: Path, elf_path: Path, out_dir: Path) -> dict[str, Any]:
    """Attach a real TCP GDB RSP client to unified-soc-emu, inspect PC, single-step, continue."""
    gdb_dir = out_dir / "gdb-rsp"
    gdb_dir.mkdir(parents=True, exist_ok=True)
    port_file = gdb_dir / "port.txt"
    proc = subprocess.Popen(
        [
            str(emu_bin),
            str(elf_path),
            str(gdb_dir / "console.log"),
            str(gdb_dir / "trace.jsonl"),
            str(gdb_dir / "summary.json"),
            "500000",
            "--gdb-port",
            "0",
            "--gdb-port-file",
            str(port_file),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10.0
        while not port_file.is_file() or not port_file.read_text().strip():
            if proc.poll() is not None or time.monotonic() > deadline:
                raise ContractError("E_UNIFIED_GDB GDB stub failed to publish port")
            time.sleep(0.02)
        port = int(port_file.read_text().strip())
        with socket.create_connection(("127.0.0.1", port), timeout=10.0) as sock:
            halt_reply = _gdb_send(sock, "?")
            regs0 = _gdb_send(sock, "g")
            step_reply = _gdb_send(sock, "s")
            regs1 = _gdb_send(sock, "g")
            cont_reply = _gdb_send(sock, "c")
        rc = proc.wait(timeout=15)
        pc0 = int.from_bytes(bytes.fromhex(regs0[-16:]), "little")
        pc1 = int.from_bytes(bytes.fromhex(regs1[-16:]), "little")
        ok = (
            halt_reply == "S05"
            and step_reply == "S05"
            and pc1 == pc0 + 4
            and cont_reply == "W00"
            and rc == 0
        )
        return {
            "status": "PASSED" if ok else "FAILED",
            "port": port,
            "pc_before_step": hex(pc0),
            "pc_after_step": hex(pc1),
            "continue_reply": cont_reply,
            "returncode": rc,
        }
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def _compute_coverage(
    summary: dict[str, Any],
    console_text: str,
    vcd_path: Path,
    neg_ok: bool,
    gdb_result: dict[str, Any] | None,
    use_verilator: bool,
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute a 24-bin (dual-hart) or 30-bin (ASoC multi-cluster/LPU/bridge) coverage report."""
    ip_hits = summary.get("ip_hits", {})
    ip_res = summary.get("ip_results", {})
    bridges = summary.get("bus_bridge_hits", {})
    routers = summary.get("router_hits", [0, 0, 0, 0])
    expected_routes = len(spec["devices"]) if spec is not None else 16
    models = {str(d["model"]) for d in spec["devices"]} if spec is not None else set()
    harts = int(spec["cpu"]["harts"]) if spec is not None else 2
    has_bridges = bool(spec is not None and spec["noc"].get("bridges"))

    vcd_ok = vcd_path.is_file() and "$enddefinitions" in vcd_path.read_text(errors="replace")
    bins: dict[str, bool] = {
        "noc_all_4_routers_active": len(routers) == 4 and all(h > 0 for h in routers),
        "noc_ir_route_table_16_entries": summary.get("ir_route_entries", 0) == expected_routes,
        "noc_cpu_and_dma_3_masters": summary.get("noc_dma_master_transactions", 0) >= 9,
        "noc_vcd_waveform_captured": vcd_ok,
        "smp_hart1_clint_ipi_wake": (
            "SMP_HART1_IPI_WAKE_OK" in console_text and summary.get("smp_ipi_delivered", 0) >= 1
        ),
        "smp_rv64a_atomic_coherency": (
            "SMP_ATOMIC_BARRIER_OK" in console_text
            and summary.get("hart1_instructions", 0) > 0
            and summary.get("atomic_ops_executed", 0) >= 24
        ),
        "irq_chipyard_gcd": "CHIPYARD_GCD_IRQ_OK" in console_text,
        "irq_opentitan_gpio": "OPENTITAN_GPIO_IRQ_OK" in console_text,
        "irq_dma_engine": "NOC_DMA_MASTER_IRQ_OK" in console_text,
        "irq_sha256_crypto": "SHA256_FIPS_KAT_IRQ_OK" in console_text,
        "irq_aes128_crypto": "AES128_FIPS197_IRQ_OK" in console_text,
        "irq_spi_controller": "SPI_CTRL_RTL_IRQ_OK" in console_text,
        "irq_i2c_controller": "I2C_CTRL_RTL_IRQ_OK" in console_text,
        "irq_mac_npu": "MAC_NPU_DOT_IRQ_OK" in console_text,
        "irq_pmp_bus_guard": "PMP_GUARD_FAULT_IRQ_OK" in console_text,
        "rtl_npu_scalar_and_matmul": (
            ip_res.get("npu_acc_final") == 126
            and ip_res.get("npu_matmul_ops") == 1
            and (not use_verilator or ip_res.get("npu_rtl_cycles", 0) > 0)
        ),
        "rtl_sha256_fips_kat_64_rounds": (
            ip_res.get("sha256_blocks_hashed") == 1
            and (not use_verilator or ip_res.get("sha256_rtl_cycles", 0) >= 64)
        ),
        "rtl_aes128_fips197_10_rounds": (
            ip_res.get("aes_blocks_encrypted") == 1
            and (not use_verilator or ip_res.get("aes_rtl_cycles", 0) >= 10)
        ),
        "rtl_spi_32bit_shift_loopback": (
            ip_res.get("spi_xfers_completed") == 1
            and (not use_verilator or ip_res.get("spi_rtl_cycles", 0) >= 32)
        ),
        "rtl_i2c_master_loopback_fsm": (
            ip_res.get("i2c_xfers_completed") == 1
            and (not use_verilator or ip_res.get("i2c_rtl_cycles", 0) >= 16)
        ),
        "rtl_l2_cache_hits_misses_snoops": (
            ip_res.get("l2_hits", 0) > 0
            and ip_res.get("l2_misses", 0) > 0
            and ip_res.get("l2_dma_snoops", 0) >= 8
            and (not use_verilator or ip_res.get("l2_rtl_cycles", 0) > 0)
        ),
        "security_cpu_pmp_csr_and_bus_guard": (
            ip_res.get("cpu_pmpcfg0") == 31
            and ip_hits.get("pmp_guard_blocked_writes") == 2
            and ip_res.get("pmp_guard_fault_count") == 2
            and "DMA_PMP_BLOCK_OK" in console_text
        ),
        "benchmark_coremark_crc32_and_gdb": (
            "COREMARK_CRC32_ROI_OK" in console_text
            and (True if gdb_result is None else gdb_result["status"] == "PASSED")
        ),
        "negative_guest_failure_detected": neg_ok,
    }
    if harts >= 4:
        bins["multi_cluster_hart2_hart3_ipi"] = (
            "CLUSTER1_IPI_WAKE_OK" in console_text
            and summary.get("hart2_instructions", 0) > 0
            and summary.get("hart3_instructions", 0) > 0
            and summary.get("smp_ipi_delivered", 0) >= 3
        )
        bins["multi_cluster_4hart_atomics_48"] = (
            "MULTI_CLUSTER_ATOMIC_OK" in console_text
            and summary.get("atomic_ops_executed", 0) >= 48
        )
    if "user.cluster_mailbox" in models:
        bins["multi_cluster_mailbox_ipc_irq"] = (
            "CLUSTER_MAILBOX_IPC_OK" in console_text and ip_res.get("mailbox_messages", 0) >= 1
        )
    if "user.lpu_engine" in models:
        bins["rtl_lpu_linear_relu_and_argmax"] = (
            "LPU_LINEAR_RELU_IRQ_OK" in console_text
            and "LPU_TOKEN_ARGMAX_IRQ_OK" in console_text
            and ip_res.get("lpu_linear_ops") == 2
            and ip_res.get("lpu_tokens_decoded") == 1
            and ip_res.get("lpu_argmax_idx") == 1
            and ip_res.get("lpu_max_logit") == 485
            and (not use_verilator or ip_res.get("lpu_rtl_cycles", 0) > 0)
        )
    if has_bridges:
        bins["bus_hierarchical_3_bridges_active"] = (
            bridges.get("noc_backbone", 0) > 0
            and bridges.get("axi_ai_bridge", 0) > 0
            and bridges.get("axi_sec_bridge", 0) > 0
            and bridges.get("apb_periph_bridge", 0) > 0
        )
    if "sram" in models and "uart_aux" in models:
        bins["ai_scratch_sram_dma_and_aux_uart"] = (
            "AI_SCRATCH_DMA_OK" in console_text
            and "UART_DEBUG_TELEMETRY_OK" in console_text
            and ip_hits.get("ai_scratch_sram", 0) > 0
            and ip_res.get("uart_aux_tx_bytes", 0) > 0
        )

    hit_count = sum(1 for v in bins.values() if v)
    total = len(bins)
    return {
        "contract": "usp-functional-coverage-1",
        "bins_hit": hit_count,
        "bins_total": total,
        "coverage_percent": round(100.0 * hit_count / total, 2),
        "status": "PASSED" if hit_count == total else "FAILED",
        "bins": bins,
    }


def _render_report_md(report: dict[str, Any]) -> str:
    pos = report.get("positive_run", {})
    summary = pos.get("summary", {})
    ip_hits = summary.get("ip_hits", {})
    ip_res = summary.get("ip_results", {})
    bridges = summary.get("bus_bridge_hits", {})
    cov = report.get("coverage", {})
    rtl_count = 7 if ip_res.get("lpu_ops_done", 0) > 0 else 6
    harts = summary.get("harts", 2)
    clusters = summary.get("clusters", 1)
    lines = [
        f"# Unified Multi-Platform SystemC/TLM-2.0 + {rtl_count}× Verilator RTL Report "
        f"({report['design']})",
        "",
        f"- **Contract:** `{report['contract']}`",
        f"- **Status:** **{report['status']}**",
        f"- **Fidelity:** `{report['fidelity']}`",
        f"- **CPU Topology:** `{clusters}` cluster(s) / `{harts}` RV64IMA_Zicsr hart(s)",
        f"- **RTL Backend:** `{summary.get('rtl_backend')}`",
        f"- **Functional Coverage:** **{cov.get('coverage_percent', 0)}%** "
        f"(`{cov.get('bins_hit', 0)}/{cov.get('bins_total', 0)}` bins)",
        f"- **Timestamp (UTC):** `{report['created_utc']}`",
        "",
        f"## Multi-Cluster SMP & {rtl_count}× Verilator RTL Metrics (`unified_sw.elf`)",
        "",
        f"- Instructions executed: `{summary.get('instructions_executed')}` "
        f"(hart0=`{summary.get('hart0_instructions')}`, "
        f"hart1=`{summary.get('hart1_instructions')}`, "
        f"hart2=`{summary.get('hart2_instructions', 0)}`, "
        f"hart3=`{summary.get('hart3_instructions', 0)}`)",
        f"- RV64A Atomic ops / CLINT IPIs: `{summary.get('atomic_ops_executed')}` / "
        f"`{summary.get('smp_ipi_delivered')}`",
        f"- Traps / IRQs taken: `{summary.get('traps_taken')}`",
        f"- SystemC simulated time (ps): `{summary.get('systemc_time_ps')}`",
        f"- NoC 2×2 tx / hops / backpressure stalls: `{summary.get('noc_total_transactions')}`"
        f" / `{summary.get('noc_total_hops')}` / `{summary.get('noc_backpressure_stalls')}`",
        f"- IR routing table entries: `{summary.get('ir_route_entries')}` | "
        f"DMA master tx: `{summary.get('noc_dma_master_transactions')}`",
        f"- Bus Bridge Traffic: `noc_backbone={bridges.get('noc_backbone', 0)}`, "
        f"`axi_ai_bridge={bridges.get('axi_ai_bridge', 0)}`, "
        f"`axi_sec_bridge={bridges.get('axi_sec_bridge', 0)}`, "
        f"`apb_periph_bridge={bridges.get('apb_periph_bridge', 0)}`",
        f"- Verilator RTL cycles: `mac_npu={ip_res.get('npu_rtl_cycles')}`, "
        f"`lpu={ip_res.get('lpu_rtl_cycles', 0)}`, "
        f"`sha256={ip_res.get('sha256_rtl_cycles')}`, `aes128={ip_res.get('aes_rtl_cycles')}`, "
        f"`spi={ip_res.get('spi_rtl_cycles')}`, `i2c={ip_res.get('i2c_rtl_cycles')}`, "
        f"`l2_cache={ip_res.get('l2_rtl_cycles')}`",
        f"- L2 Cache: `hits={ip_res.get('l2_hits')}`, `misses={ip_res.get('l2_misses')}`, "
        f"`dma_snoops={ip_res.get('l2_dma_snoops')}`",
        "",
        "| Mapped IP | Transactions |",
        "|---|---|",
    ]
    for name, count in ip_hits.items():
        if count or name in {"pmp_guard_blocked_writes"}:
            lines.append(f"| `{name}` | `{count}` |")
    lines.append("")
    return "\n".join(lines)


def run_unified_soc(
    spec_path: Path,
    out_dir: Path,
    *,
    backend: str = "auto",
    verify_gdb: bool = True,
    max_steps: int = 500_000,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Compile and execute the unified Multi-Cluster/SMP SystemC (+ Verilator RTL) SoC."""
    if backend not in {"auto", "verilator-rtl", "systemc-tlm"}:
        raise ContractError(f"E_UNIFIED_BACKEND unsupported backend: {backend}")
    spec_path = spec_path.resolve()
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ContractError("E_UNIFIED_OUT choose a fresh output directory to preserve evidence")

    ir = generate_unified_artifacts(spec_path, out_dir)
    spec = load_unified_spec(spec_path)
    has_lpu = any(str(d["model"]) == "user.lpu_engine" for d in spec["devices"])
    use_verilator = backend == "verilator-rtl" or (
        backend == "auto" and shutil.which("verilator") is not None
    )
    ready, missing = has_unified_toolchain(require_verilator=(backend == "verilator-rtl"))
    if not ready:
        blocked: dict[str, Any] = {
            "contract": CONTRACT,
            "platform_version": __version__,
            "design": ir["name"],
            "status": "BLOCKED",
            "reason": f"Missing required build/simulation tools on this host: {', '.join(missing)}",
            "missing_tools": missing,
            "generated_ir": str(out_dir / "unified-soc-ir.json"),
            "hint": (
                "Run inside the rootless Podman SDK container (`bash scripts/podman.sh smoke`)."
            ),
        }
        write_json(out_dir / "report.json", blocked)
        return blocked

    src_dir = resolve_unified_src_dir(spec_path)
    cpp_src = src_dir / "unified_systemc_soc.cpp"

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
    includes = [arg for f in sc_flags if f.startswith("-I") for arg in ("-isystem", f[2:])]
    link_flags = [f for f in sc_flags if not f.startswith("-I")]

    v_includes: list[str] = []
    v_links: list[str] = []
    verilator_ver: str | None = None
    if use_verilator:
        v_includes, v_links, verilator_ver = _build_verilator_rtl_blocks(
            src_dir, out_dir, include_lpu=has_lpu
        )

    emu_bin = out_dir / "unified-soc-emu"
    compile_cmd = [
        cxx,
        "-std=c++17",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        f"-I{out_dir}",
        *v_includes,
        *includes,
        str(cpp_src),
        *v_links,
        *link_flags,
        "-o",
        str(emu_bin),
    ]
    execute_build(compile_cmd, out_dir, out_dir / "emu-build.log", 120)

    pos_elf = out_dir / "unified_sw.elf"
    neg_elf = out_dir / "fail_sw.elf"
    pos_elf_sha = _compile_firmware(
        gcc, src_dir, out_dir, "unified_sw.c", pos_elf, out_dir / "sw-pos-build.log"
    )
    neg_elf_sha = _compile_firmware(
        gcc, src_dir, out_dir, "fail_sw.c", neg_elf, out_dir / "sw-neg-build.log"
    )

    pos_console = out_dir / "console.log"
    pos_trace = out_dir / "noc-trace.jsonl"
    pos_summary_path = out_dir / "summary.json"
    pos_vcd = out_dir / "unified-soc.vcd"
    pos_proc = subprocess.run(
        [
            str(emu_bin),
            str(pos_elf),
            str(pos_console),
            str(pos_trace),
            str(pos_summary_path),
            str(max_steps),
            "--vcd-out",
            str(pos_vcd),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    console_text = pos_console.read_text(errors="replace") if pos_console.is_file() else ""
    summary = json.loads(pos_summary_path.read_text()) if pos_summary_path.is_file() else {}
    trace_lines = (
        [json.loads(line) for line in pos_trace.read_text().splitlines() if line.strip()]
        if pos_trace.is_file()
        else []
    )

    neg_console = out_dir / "neg-console.log"
    neg_trace = out_dir / "neg-noc-trace.jsonl"
    neg_summary_path = out_dir / "neg-summary.json"
    neg_proc = subprocess.run(
        [
            str(emu_bin),
            str(neg_elf),
            str(neg_console),
            str(neg_trace),
            str(neg_summary_path),
            str(max_steps),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    neg_summary = json.loads(neg_summary_path.read_text()) if neg_summary_path.is_file() else {}
    neg_ok = neg_proc.returncode != 0 and neg_summary.get("status") == "FAILED"

    gdb_result = verify_gdb_rsp(emu_bin, pos_elf, out_dir) if verify_gdb else None
    coverage = _compute_coverage(
        summary, console_text, pos_vcd, neg_ok, gdb_result, use_verilator, spec=spec
    )
    write_json(out_dir / "coverage.json", coverage)

    expected_markers = expected_markers_for_spec(spec)
    marker_checks = {m: (m in console_text) for m in expected_markers}
    pos_checks = {
        **marker_checks,
        "exit_zero": pos_proc.returncode == 0 and summary.get("status") == "PASSED",
        "coverage_100_percent": coverage["status"] == "PASSED",
        "trace_non_empty": len(trace_lines) > 35,
    }

    overall = "PASSED" if all(pos_checks.values()) else "FAILED"
    rtl_tag = "7x-verilator-rtl" if has_lpu else "6x-verilator-rtl"
    cluster_tag = "multicluster-smp" if int(spec["cpu"]["harts"]) >= 4 else "smp"
    fidelity = (
        f"hybrid-systemc-tlm2-{cluster_tag}-and-{rtl_tag}-annotated-noc"
        if use_verilator
        else f"systemc-tlm2-{cluster_tag}-functional-annotated-noc"
    )
    report: dict[str, Any] = {
        "contract": CONTRACT,
        "platform_version": __version__,
        "design": ir["name"],
        "created_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "status": overall,
        "fidelity": fidelity,
        "toolchain": {
            "cxx": {"path": cxx, "version": cxx_ver},
            "systemc_version": sc_ver,
            "verilator_version": verilator_ver,
            "riscv_gcc": {"path": gcc, "version": gcc_ver},
        },
        "provenance": {
            "spec_sha256": ir["spec_sha256"],
            "emulator_cpp_sha256": digest(cpp_src),
            "emulator_binary_sha256": digest(emu_bin),
            "positive_elf_sha256": pos_elf_sha,
            "negative_elf_sha256": neg_elf_sha,
            "console_sha256": digest(pos_console) if pos_console.is_file() else None,
            "noc_trace_sha256": digest(pos_trace) if pos_trace.is_file() else None,
            "vcd_sha256": digest(pos_vcd) if pos_vcd.is_file() else None,
        },
        "positive_run": {
            "returncode": pos_proc.returncode,
            "console_output": console_text,
            "summary": summary,
            "checks": pos_checks,
        },
        "negative_run": {
            "status": neg_summary.get("status", "UNKNOWN"),
            "returncode": neg_proc.returncode,
            "expected_failure_detected": neg_ok,
        },
        "coverage": coverage,
        **({"gdb_rsp": gdb_result} if gdb_result is not None else {}),
    }
    write_json(out_dir / "report.json", report)
    (out_dir / "report.md").write_text(_render_report_md(report))
    return report


def calibrate_unified_soc(spec_path: Path, out_dir: Path) -> dict[str, Any]:
    """Compare Verilator-RTL hybrid run vs pure SystemC-TLM run on identical SMP RV64 SW."""
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ContractError("E_UNIFIED_OUT choose a fresh calibration output directory")
    ready, missing = has_unified_toolchain(require_verilator=True)
    if not ready:
        out_dir.mkdir(parents=True)
        blocked_res: dict[str, Any] = {
            "contract": "unified-rtl-tlm-calibration-1",
            "status": "BLOCKED",
            "missing_tools": missing,
        }
        write_json(out_dir / "calibration.json", blocked_res)
        return blocked_res

    spec = load_unified_spec(spec_path)
    has_lpu = any(str(d["model"]) == "user.lpu_engine" for d in spec["devices"])
    rtl_run = run_unified_soc(
        spec_path, out_dir / "rtl-hybrid", backend="verilator-rtl", verify_gdb=False
    )
    tlm_run = run_unified_soc(
        spec_path, out_dir / "tlm-behavioral", backend="systemc-tlm", verify_gdb=False
    )
    rtl_time = float(rtl_run["positive_run"]["summary"]["systemc_time_ps"])
    tlm_time = float(tlm_run["positive_run"]["summary"]["systemc_time_ps"])
    cal = evaluate_calibration_report(
        roi_name=f"{spec['name'].lower()}_{'7rtl' if has_lpu else '6rtl'}_smp_benchmark",
        reference_source="native-rtl",
        model_source="qemu-analytical-estimate",
        reference_cycles=rtl_time / 20_000.0,
        estimated_cycles=tlm_time / 20_000.0,
        max_relative_error=0.10,
    )
    rtl_ip = rtl_run["positive_run"]["summary"]["ip_results"]
    tlm_ip = tlm_run["positive_run"]["summary"]["ip_results"]
    parity = (
        rtl_run["status"] == "PASSED"
        and tlm_run["status"] == "PASSED"
        and rtl_run["positive_run"]["console_output"] == tlm_run["positive_run"]["console_output"]
        and rtl_ip["npu_acc_final"] == tlm_ip["npu_acc_final"]
        and rtl_ip["sha256_blocks_hashed"] == tlm_ip["sha256_blocks_hashed"]
        and rtl_ip["aes_blocks_encrypted"] == tlm_ip["aes_blocks_encrypted"]
        and rtl_ip["spi_xfers_completed"] == tlm_ip["spi_xfers_completed"]
        and rtl_ip["i2c_xfers_completed"] == tlm_ip["i2c_xfers_completed"]
        and rtl_ip["l2_hits"] == tlm_ip["l2_hits"]
        and (
            not has_lpu
            or (
                rtl_ip["lpu_linear_ops"] == tlm_ip["lpu_linear_ops"]
                and rtl_ip["lpu_tokens_decoded"] == tlm_ip["lpu_tokens_decoded"]
                and rtl_ip["lpu_argmax_idx"] == tlm_ip["lpu_argmax_idx"]
                and rtl_ip["lpu_max_logit"] == tlm_ip["lpu_max_logit"]
            )
        )
    )
    rtl_cycles: dict[str, Any] = {
        "mac_npu": rtl_ip["npu_rtl_cycles"],
        "sha256": rtl_ip["sha256_rtl_cycles"],
        "aes128": rtl_ip["aes_rtl_cycles"],
        "spi": rtl_ip["spi_rtl_cycles"],
        "i2c": rtl_ip["i2c_rtl_cycles"],
        "l2_cache": rtl_ip["l2_rtl_cycles"],
    }
    if has_lpu:
        rtl_cycles["lpu"] = rtl_ip["lpu_rtl_cycles"]
    res: dict[str, Any] = {
        "contract": "unified-rtl-tlm-calibration-1",
        "status": "PASSED" if parity and cal["within_budget"] else "FAILED",
        "functional_parity": parity,
        "rtl_cycles": rtl_cycles,
        "rtl_systemc_time_ps": rtl_time,
        "tlm_systemc_time_ps": tlm_time,
        "calibration": cal,
    }
    write_json(out_dir / "calibration.json", res)
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="usp unified-soc")
    commands = parser.add_subparsers(dest="command", required=True)
    plan_cmd = commands.add_parser("plan", help="validate spec and generate header/linker/IR")
    plan_cmd.add_argument(
        "spec",
        type=Path,
        nargs="?",
        default=Path("specs/multiplatform/unified_hetero_soc.yaml"),
    )
    plan_cmd.add_argument("--out", type=Path, required=True)

    run_cmd = commands.add_parser(
        "run", help="compile SystemC (+4x Verilator RTL) unified SoC + RV64 SW and execute"
    )
    run_cmd.add_argument(
        "spec",
        type=Path,
        nargs="?",
        default=Path("specs/multiplatform/unified_hetero_soc.yaml"),
    )
    run_cmd.add_argument("--out", type=Path, required=True)
    run_cmd.add_argument(
        "--backend", choices=("auto", "verilator-rtl", "systemc-tlm"), default="auto"
    )
    run_cmd.add_argument("--no-gdb-check", action="store_true")
    run_cmd.add_argument("--max-steps", type=int, default=500_000)
    run_cmd.add_argument("--timeout", type=float, default=30.0)

    cal_cmd = commands.add_parser(
        "calibrate", help="run 4x Verilator-RTL vs SystemC-TLM cycle calibration on unified SoC"
    )
    cal_cmd.add_argument(
        "spec",
        type=Path,
        nargs="?",
        default=Path("specs/multiplatform/unified_hetero_soc.yaml"),
    )
    cal_cmd.add_argument("--out", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            ir = generate_unified_artifacts(args.spec, args.out)
            print(json.dumps({"status": "PASSED", "design": ir["name"], "out": str(args.out)}))
            return 0
        if args.command == "calibrate":
            cal = calibrate_unified_soc(args.spec, args.out)
            print(json.dumps(cal, indent=2, sort_keys=True))
            return 0 if cal["status"] == "PASSED" else 1
        report = run_unified_soc(
            args.spec,
            args.out,
            backend=args.backend,
            verify_gdb=not args.no_gdb_check,
            max_steps=args.max_steps,
            timeout=args.timeout,
        )
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "design": report["design"],
                    "fidelity": report.get("fidelity"),
                    "coverage_percent": report.get("coverage", {}).get("coverage_percent"),
                    "report": str(args.out / "report.json"),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if report["status"] == "PASSED" else 1
    except (ContractError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}))
        return 2
