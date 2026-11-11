"""Explicit local QEMU/SystemC/Verilator transaction adapter, single memory owner."""

import json
import os
import shlex
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from .domain import ContractError
from .execution import cleanup_on_termination
from .ipxact import digest
from .pipeline import execute, run_custom, verify_source
from .runner import tool_version, write_json

OPENTITAN_SHA = "ed044fc9760bdf9fc075d0015ba1db07fa075355"


def build_models(out, native_source, opentitan_source):
    out.mkdir(parents=True, exist_ok=True)
    opentitan_source = verify_source(opentitan_source, OPENTITAN_SHA)
    version = subprocess.run(
        ["pkg-config", "--modversion", "systemc"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if version not in {"3.0.0", "3.0.1"}:
        raise ContractError(f"expected SystemC 3.0.1, got {version}")
    _, verilator_version = tool_version("verilator")
    if not verilator_version.startswith("Verilator 5.032"):
        raise ContractError("this adapter pins Verilator 5.032")
    flags = shlex.split(
        subprocess.run(
            ["pkg-config", "--cflags", "--libs", "systemc"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    execute(
        [
            "g++",
            "-std=c++17",
            str(native_source / "fifo_server.cpp"),
            *flags,
            "-o",
            str(out / "fifo-tlm-server"),
        ],
        out,
        out / "tlm-build.log",
    )
    rtl_dir = opentitan_source / "hw/ip/prim/rtl"
    cflags = ["-std=c++17", "-DUSP_RTL", *[f for f in flags if f.startswith("-I")]]
    ldflags = [f for f in flags if not f.startswith("-I")]
    execute(
        [
            "verilator",
            "--cc",
            "--exe",
            "--build",
            "--top-module",
            "usp_fifo",
            "--Mdir",
            str(out / "rtl"),
            "-j",
            "2",
            "-I" + str(rtl_dir),
            "-CFLAGS",
            " ".join(cflags),
            "-LDFLAGS",
            " ".join(ldflags),
            str(rtl_dir / "prim_util_pkg.sv"),
            str(rtl_dir / "prim_fifo_sync.sv"),
            str(native_source / "usp_fifo.sv"),
            str(native_source / "fifo_server.cpp"),
        ],
        out,
        out / "rtl-build.log",
        300,
    )
    files = [
        native_source / "fifo_server.cpp",
        native_source / "usp_fifo.sv",
        rtl_dir / "prim_util_pkg.sv",
        rtl_dir / "prim_fifo_sync.sv",
        rtl_dir / "prim_assert.sv",
        rtl_dir / "prim_assert_dummy_macros.svh",
    ]
    record = {
        "systemc_version": "3.0.1 (header and link ABI checked)",
        "pkg_config_version": version,
        "opentitan_commit": OPENTITAN_SHA,
        "verilator_version": verilator_version,
        "tlm_sha256": digest(out / "fifo-tlm-server"),
        "rtl_sha256": digest(out / "rtl/Vusp_fifo"),
        "source_hashes": {p.name: digest(p) for p in files},
        "assumption": (
            "10 ns annotated TLM transaction; 2 RTL edges per operation; no CPU-cycle correlation"
        ),
    }
    write_json(out / "models.json", record)
    return record


@cleanup_on_termination
def run_hybrid(project, generated, models, result_dir, mode="tlm", timeout=20):
    if project.manifest["profile"] != "usp-rv64-hybrid-v1" or mode not in {"tlm", "rtl"}:
        raise ContractError(
            "hybrid execution requires the explicit hybrid profile and tlm/rtl mode"
        )
    record = json.loads((models / "models.json").read_text())
    binary = models / ("fifo-tlm-server" if mode == "tlm" else "rtl/Vusp_fifo")
    if digest(binary) != record[mode + "_sha256"]:
        raise ContractError("modified hybrid model binary")
    with tempfile.TemporaryDirectory(prefix="usp-bridge-") as temporary_name:
        temporary = Path(temporary_name)
        sock = temporary / "bus.sock"
        trace = temporary / "trace.jsonl"
        with (temporary / "model.log").open("wb") as stream:
            process = subprocess.Popen(
                [str(binary), str(sock), str(trace)],
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 5
                while not sock.exists():
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise ContractError("hybrid model did not become ready")
                    time.sleep(0.01)
                report = run_custom(project, generated, result_dir, timeout, bridge_socket=sock)
                process.wait(timeout=5)
                report["bridge"] = {"mode": mode, "returncode": process.returncode, **record}
                transactions = [json.loads(line) for line in trace.read_text().splitlines()]
                bridge_passed = (
                    process.returncode == 0
                    and any(
                        t["op"] == 1 and t["irq"] == 1 and t["data"] == 0x5A3C1234
                        for t in transactions
                    )
                    and any(
                        t["op"] == 0 and t["irq"] == 0 and t["value"] == 0x5A3C1234
                        for t in transactions
                    )
                )
                report["assertions"]["bridge_mmio_and_irq"] = bridge_passed
                if not bridge_passed:
                    report["status"] = "FAILED"
                for source, name in [
                    (trace, "bridge-trace.jsonl"),
                    (temporary / "model.log", "bridge-model.log"),
                ]:
                    (result_dir / name).write_bytes(source.read_bytes())
                write_json(result_dir / "report.json", report)
                (result_dir / "report.md").write_text(
                    "# USP hybrid acceptance\n\nStatus: **"
                    + report["status"]
                    + "**\n\n"
                    + f"Bridge mode: {mode}; MMIO/readback/IRQ: {bridge_passed}.\n\n"
                    + record["assumption"]
                    + "\n\n"
                    + "QEMU SHA256: `"
                    + report["qemu_binary_sha256"]
                    + "`\n\n"
                    + "Firmware SHA256: `"
                    + report["firmware_sha256"]
                    + "`\n\n"
                    + "IR SHA256: `"
                    + report["ir_sha256"]
                    + "`\n\n"
                    + "Assertions:\n\n"
                    + "\n".join(f"- {k}: {v}" for k, v in report["assertions"].items())
                    + "\n\nRaw serial: stdout.log; ordered transaction evidence: "
                    "bridge-trace.jsonl.\n"
                )
                return report
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
