"""Single Unified Multi-Platform SystemC/TLM-2.0 + Verilator RTL SoC compiler and runner.

Compiles one heterogeneous SoC spec (`unified-systemc-soc-1`) into a shared address/IRQ/NoC
header (`unified_soc_map.h`) and linker script (`link.ld`), builds the single SystemC/TLM-2.0
2x2 Mesh NoC emulator connecting all mapped IPs (QEMU-compatible UART/CLINT/PLIC/Finisher/RAM,
Chipyard GCD, OpenTitan GPIO, 2nd-master NoC DMA Engine, Verilator-compiled SystemVerilog RTL
or TLM-2.0 MAC NPU-lite, and SystemC PMP/IOPMP Bus Guard) with optional GDB RSP debug attach,
compiles real RV64IM_Zicsr bare-metal software (`unified_sw.elf` + negative `fail_sw.elf`),
executes both, and records SHA-256 evidence with honest fidelity disclosures.
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
        "user.mac_npu",
        "user.pmp_guard",
    }
)
EXPECTED_POSITIVE_MARKERS = (
    "UNIFIED_SOC_BOOT_OK",
    "RISCV_PMP_CSR_OK",
    "CHIPYARD_GCD_IRQ_OK",
    "OPENTITAN_GPIO_IRQ_OK",
    "MAC_NPU_DOT_IRQ_OK",
    "PMP_GUARD_FAULT_IRQ_OK",
    "NOC_DMA_MASTER_IRQ_OK",
    "DMA_PMP_BLOCK_OK",
    "UNIFIED_SOC_ALL_PASS",
)


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
    keys(
        noc,
        {"topology", "rows", "cols", "hop_latency_ns", "link_latency_ns", "cpu_router"},
        "unified noc",
    )
    rows, cols = int(noc["rows"]), int(noc["cols"])
    if rows != 2 or cols != 2:
        raise ContractError("E_UNIFIED_NOC unified SystemC emulator requires a 2x2 mesh topology")
    cpu_rx, cpu_ry = int(noc["cpu_router"][0]), int(noc["cpu_router"][1])
    if not (0 <= cpu_rx < cols and 0 <= cpu_ry < rows):
        raise ContractError("E_UNIFIED_NOC cpu_router out of mesh bounds")

    cpu = data["cpu"]
    keys(
        cpu,
        {
            "name",
            "isa",
            "privilege",
            "pmp_regions",
            "clock_hz",
            "timebase_hz",
            "entry",
            "stack_top",
            "protected_scratch",
            "protected_size",
            "dma_src_buf",
            "dma_dst_buf",
            "model_disclosure",
        },
        "unified cpu",
    )

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
        base = int(dev["base"])
        size = int(dev["size"])
        rx, ry = int(dev["router"][0]), int(dev["router"][1])
        backend = str(dev.get("backend", "systemc-tlm"))
        if backend not in {"systemc-tlm", "verilator-rtl"}:
            raise ContractError(f"E_UNIFIED_SPEC {name}: unsupported backend {backend}")
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
    scratch = int(cpu["protected_scratch"])
    scratch_size = int(cpu["protected_size"])
    dma_src = int(cpu["dma_src_buf"])
    dma_dst = int(cpu["dma_dst_buf"])
    if not (dram_base <= entry < stack_top <= dram_end):
        raise ContractError("E_UNIFIED_SPEC entry/stack_top outside main DRAM")
    if not (dram_base <= scratch and scratch + scratch_size <= dram_end):
        raise ContractError("E_UNIFIED_SPEC protected_scratch outside main DRAM")
    if scratch < stack_top:
        raise ContractError("E_UNIFIED_SPEC protected_scratch overlaps stack/code region")
    if not (dram_base <= dma_src < dram_end and dram_base <= dma_dst < dram_end):
        raise ContractError("E_UNIFIED_SPEC DMA buffers outside main DRAM")

    return data


def _render_map_header(spec: dict[str, Any]) -> str:
    noc = spec["noc"]
    cpu = spec["cpu"]
    lines = [
        "/* Generated from unified-systemc-soc-1 specification; DO NOT EDIT BY HAND. */",
        "#ifndef USOC_UNIFIED_SOC_MAP_H",
        "#define USOC_UNIFIED_SOC_MAP_H",
        f"#define USOC_NOC_HOP_NS {int(noc['hop_latency_ns'])}U",
        f"#define USOC_NOC_LINK_NS {int(noc['link_latency_ns'])}U",
        f"#define USOC_CPU_RX {int(noc['cpu_router'][0])}",
        f"#define USOC_CPU_RY {int(noc['cpu_router'][1])}",
        f"#define USOC_CPU_ENTRY 0x{int(cpu['entry']):08X}ULL",
        f"#define USOC_STACK_TOP 0x{int(cpu['stack_top']):08X}ULL",
        f"#define USOC_PROTECTED_SCRATCH_BASE 0x{int(cpu['protected_scratch']):08X}ULL",
        f"#define USOC_PROTECTED_SCRATCH_SIZE 0x{int(cpu['protected_size']):08X}ULL",
        f"#define USOC_DMA_SRC_BUF 0x{int(cpu['dma_src_buf']):08X}ULL",
        f"#define USOC_DMA_DST_BUF 0x{int(cpu['dma_dst_buf']):08X}ULL",
    ]
    for dev in spec["devices"]:
        key = str(dev["name"]).upper()
        lines.append(f"#define USOC_{key}_BASE 0x{int(dev['base']):08X}ULL")
        lines.append(f"#define USOC_{key}_SIZE 0x{int(dev['size']):08X}ULL")
        lines.append(f"#define USOC_{key}_RX {int(dev['router'][0])}")
        lines.append(f"#define USOC_{key}_RY {int(dev['router'][1])}")
        if "irq" in dev:
            lines.append(f"#define USOC_{key}_IRQ {int(dev['irq'])}U")
    lines.append("#endif /* USOC_UNIFIED_SOC_MAP_H */")
    return "\n".join(lines) + "\n"


def _render_linker_script(spec: dict[str, Any]) -> str:
    cpu = spec["cpu"]
    dram = next(d for d in spec["devices"] if d["model"] == "ram")
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
        "}\n"
    )


def generate_unified_artifacts(spec_path: Path, out_dir: Path) -> dict[str, Any]:
    """Validate spec and emit shared unified_soc_map.h, link.ld, and unified-soc-ir.json."""
    spec_path = spec_path.resolve()
    out_dir = out_dir.resolve()
    spec = load_unified_spec(spec_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    header_path = out_dir / "unified_soc_map.h"
    linker_path = out_dir / "link.ld"
    header_path.write_text(_render_map_header(spec))
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
        "-march=rv64im_zicsr",
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


def _build_verilator_mac_npu(src_dir: Path, out_dir: Path) -> tuple[list[str], list[str], str]:
    """Compile mac_npu_rtl.sv with Verilator into a linkable C++ RTL library."""
    rtl_sv = src_dir / "rtl/mac_npu_rtl.sv"
    if not rtl_sv.is_file():
        raise ContractError(f"E_UNIFIED_RTL missing SystemVerilog source: {rtl_sv}")
    mdir = out_dir / "verilator_mac_npu"
    mdir.mkdir(parents=True, exist_ok=True)
    verilator_bin, verilator_ver = tool_version("verilator")
    vroot = subprocess.check_output(
        [verilator_bin, "--getenv", "VERILATOR_ROOT"], text=True, timeout=15
    ).strip()
    cmd = [
        verilator_bin,
        "--cc",
        "--Mdir",
        str(mdir),
        "--prefix",
        "Vmac_npu_rtl",
        "--build",
        "-j",
        "2",
        "-Wall",
        str(rtl_sv),
    ]
    execute_build(cmd, out_dir, out_dir / "verilator-build.log", 120)

    includes = [
        "-DUSOC_ENABLE_VERILATOR_MAC_NPU",
        "-isystem",
        str(mdir),
        "-isystem",
        f"{vroot}/include",
        "-isystem",
        f"{vroot}/include/vltstd",
    ]
    link_objs: list[str] = []
    lib_model = mdir / "libVmac_npu_rtl.a"
    alt_model = mdir / "Vmac_npu_rtl__ALL.a"
    if lib_model.is_file():
        link_objs.append(str(lib_model))
    elif alt_model.is_file():
        link_objs.append(str(alt_model))
    else:
        raise ContractError("E_UNIFIED_RTL Verilator build did not produce model archive")
    lib_verilated = mdir / "libverilated.a"
    if lib_verilated.is_file():
        link_objs.append(str(lib_verilated))
    else:
        link_objs.append(f"{vroot}/include/verilated.cpp")
        threads_cpp = Path(f"{vroot}/include/verilated_threads.cpp")
        if threads_cpp.is_file():
            link_objs.append(str(threads_cpp))
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
        pc0_le = bytes.fromhex(regs0[-16:])
        pc1_le = bytes.fromhex(regs1[-16:])
        pc0 = int.from_bytes(pc0_le, "little")
        pc1 = int.from_bytes(pc1_le, "little")
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


def _render_report_md(report: dict[str, Any]) -> str:
    pos = report.get("positive_run", {})
    neg = report.get("negative_run", {})
    summary = pos.get("summary", {})
    ip_hits = summary.get("ip_hits", {})
    lines = [
        f"# Unified Multi-Platform SystemC/TLM-2.0 + Verilator RTL Report ({report['design']})",
        "",
        f"- **Contract:** `{report['contract']}`",
        f"- **Status:** **{report['status']}**",
        f"- **Fidelity:** `{report['fidelity']}`",
        f"- **MAC NPU Backend:** `{summary.get('mac_npu_backend')}`",
        f"- **Timestamp (UTC):** `{report['created_utc']}`",
        "",
        "## Positive RISC-V64 Bare-Metal Software Run (`unified_sw.elf`)",
        "",
        f"- Exit code: `{pos.get('returncode')}`",
        f"- Instructions executed: `{summary.get('instructions_executed')}`",
        f"- Traps / IRQs taken: `{summary.get('traps_taken')}`",
        f"- SystemC simulated time (ps): `{summary.get('systemc_time_ps')}`",
        f"- NoC 2×2 total transactions / hops: `{summary.get('noc_total_transactions')}` / "
        f"`{summary.get('noc_total_hops')}`",
        f"- DMA 2nd-master NoC transactions: `{summary.get('noc_dma_master_transactions')}`",
        f"- Router hit counts `[R00, R10, R01, R11]`: `{summary.get('router_hits')}`",
        "",
        "| Mapped IP | Transactions |",
        "|---|---|",
    ]
    for name, count in ip_hits.items():
        lines.append(f"| `{name}` | `{count}` |")
    if "gdb_rsp" in report:
        gdb = report["gdb_rsp"]
        lines += [
            "",
            "## GDB Remote Serial Protocol (RSP) Verification",
            "",
            f"- Status: **{gdb['status']}** (`pc`: `{gdb.get('pc_before_step')}` → "
            f"`{gdb.get('pc_after_step')}`, continue: `{gdb.get('continue_reply')}`)",
        ]
    lines += [
        "",
        "## Negative Failure Detection (`fail_sw.elf`)",
        "",
        f"- Expected failure status: `{neg.get('status')}` (returncode `{neg.get('returncode')}`)",
        "",
    ]
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
    """Compile and execute the single unified SystemC (+ Verilator RTL) SoC with real RV64 SW."""
    if backend not in {"auto", "verilator-rtl", "systemc-tlm"}:
        raise ContractError(f"E_UNIFIED_BACKEND unsupported backend: {backend}")
    spec_path = spec_path.resolve()
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ContractError("E_UNIFIED_OUT choose a fresh output directory to preserve evidence")

    ir = generate_unified_artifacts(spec_path, out_dir)
    use_verilator = backend == "verilator-rtl" or (
        backend == "auto" and shutil.which("verilator") is not None
    )
    ready, missing = has_unified_toolchain(require_verilator=(backend == "verilator-rtl"))
    if not ready:
        report: dict[str, Any] = {
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
        write_json(out_dir / "report.json", report)
        return report

    src_dir = spec_path.parent / "unified_systemc"
    cpp_src = src_dir / "unified_systemc_soc.cpp"
    if not cpp_src.is_file():
        raise ContractError(f"E_UNIFIED_SRC missing SystemC emulator source: {cpp_src}")

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
        v_includes, v_links, verilator_ver = _build_verilator_mac_npu(src_dir, out_dir)

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
    pos_proc = subprocess.run(
        [
            str(emu_bin),
            str(pos_elf),
            str(pos_console),
            str(pos_trace),
            str(pos_summary_path),
            str(max_steps),
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

    marker_checks = {m: (m in console_text) for m in EXPECTED_POSITIVE_MARKERS}
    ip_hits = summary.get("ip_hits", {})
    ip_results = summary.get("ip_results", {})
    router_hits = summary.get("router_hits", [0, 0, 0, 0])

    pos_checks = {
        **marker_checks,
        "exit_zero": pos_proc.returncode == 0 and summary.get("status") == "PASSED",
        "all_four_noc_routers_active": len(router_hits) == 4 and all(h > 0 for h in router_hits),
        "noc_hops_positive": summary.get("noc_total_hops", 0) > 0,
        "dma_second_master_active": summary.get("noc_dma_master_transactions", 0) >= 9,
        "uart_exercised": ip_hits.get("qemu_uart_0", 0) > 0,
        "clint_exercised": ip_hits.get("sys_clint", 0) > 0,
        "plic_exercised": ip_hits.get("sys_plic", 0) > 0,
        "chipyard_gcd_3_ops": ip_results.get("gcd_ops_completed") == 3,
        "opentitan_gpio_exercised": ip_hits.get("opentitan_gpio_0", 0) > 0,
        "dma_16b_and_pmp_error": (
            ip_results.get("dma_bytes_transferred") == 16 and ip_results.get("dma_bus_errors") == 1
        ),
        "mac_npu_dot_126": (
            ip_results.get("npu_ops_done") == 3 and ip_results.get("npu_acc_final") == 126
        ),
        "mac_npu_rtl_cycles_if_verilator": (
            (ip_results.get("npu_rtl_cycles", 0) > 0) if use_verilator else True
        ),
        "pmp_guard_blocked_cpu_and_dma": (
            ip_hits.get("pmp_guard_blocked_writes") == 2
            and ip_results.get("pmp_guard_fault_count") == 2
        ),
        "cpu_pmp_csr_configured": ip_results.get("cpu_pmpcfg0") == 31,
        "trace_non_empty": len(trace_lines) > 25,
    }

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
    gdb_ok = True if gdb_result is None else gdb_result["status"] == "PASSED"

    overall = "PASSED" if all(pos_checks.values()) and neg_ok and gdb_ok else "FAILED"
    fidelity = (
        "hybrid-systemc-tlm2-and-verilator-rtl-annotated-noc"
        if use_verilator
        else "systemc-tlm2-functional-annotated-noc"
    )
    report = {
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
        **({"gdb_rsp": gdb_result} if gdb_result is not None else {}),
    }
    write_json(out_dir / "report.json", report)
    (out_dir / "report.md").write_text(_render_report_md(report))
    return report


def calibrate_unified_soc(spec_path: Path, out_dir: Path) -> dict[str, Any]:
    """Compare real Verilator-RTL hybrid run vs pure SystemC-TLM run on identical RV64 SW (M7)."""
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

    rtl_run = run_unified_soc(
        spec_path, out_dir / "rtl-hybrid", backend="verilator-rtl", verify_gdb=False
    )
    tlm_run = run_unified_soc(
        spec_path, out_dir / "tlm-behavioral", backend="systemc-tlm", verify_gdb=False
    )
    rtl_time = float(rtl_run["positive_run"]["summary"]["systemc_time_ps"])
    tlm_time = float(tlm_run["positive_run"]["summary"]["systemc_time_ps"])
    cal = evaluate_calibration_report(
        roi_name="unified_hetero_soc_full_sw",
        reference_source="native-rtl",
        model_source="qemu-analytical-estimate",
        reference_cycles=rtl_time / 20_000.0,  # 50 MHz clock = 20,000 ps/cycle
        estimated_cycles=tlm_time / 20_000.0,
        max_relative_error=0.10,
    )
    parity = (
        rtl_run["status"] == "PASSED"
        and tlm_run["status"] == "PASSED"
        and rtl_run["positive_run"]["console_output"] == tlm_run["positive_run"]["console_output"]
        and rtl_run["positive_run"]["summary"]["ip_results"]["npu_acc_final"]
        == tlm_run["positive_run"]["summary"]["ip_results"]["npu_acc_final"]
    )
    res = {
        "contract": "unified-rtl-tlm-calibration-1",
        "status": "PASSED" if parity and cal["within_budget"] else "FAILED",
        "functional_parity": parity,
        "rtl_npu_cycles": rtl_run["positive_run"]["summary"]["ip_results"]["npu_rtl_cycles"],
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
        "run", help="compile SystemC (+Verilator RTL) unified SoC + RV64 SW and execute"
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
        "calibrate", help="run Verilator-RTL vs SystemC-TLM cycle calibration on unified SoC"
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
