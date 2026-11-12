"""One IP-XACT SoC/ELF and real SystemC RTL peripherals on QEMU or gem5."""

import argparse
import fcntl
import hashlib
import json
import math
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .domain import ContractError
from .execution import cleanup_on_termination
from .gem5 import (
    GEM5_SHA,
    configure_external_systemc,
    python_environment,
    record_gem5_configuration,
    verify_gem5_configuration,
)
from .ipblocks import run_ipblocks, verify_models
from .ipxact import digest
from .pipeline import execute, identity, resolve, verify_generated, verify_source
from .project import inside, load_project
from .runner import write_json

RESOURCES = Path(__file__).parent / "resources/unified"


def native_identity():
    return {
        n: digest(RESOURCES / n)
        for n in ("SConscript", "UspBridge.py.in", "usp_bridge.cc", "usp_bridge.hh")
    }


def copy_extension(resources, names, extension):
    """Bind generated SimObject headers without global EXTRAS include flags."""
    headers = {"UspBridge.py": "usp_bridge.hh", "UspRtlMaster.py": "usp_rtl_master.hh"}
    for name in names:
        target = extension / name.removesuffix(".in")
        if target.name in headers:
            header = headers[target.name]
            text = (resources / name).read_text()
            original = "cxx_header = " + repr(header)
            if text.count(original) != 1:
                raise ContractError("unsupported gem5 extension header declaration")
            target.write_text(
                text.replace(original, "cxx_header = " + repr(str(extension / header)))
            )
        else:
            shutil.copy2(resources / name, target)


@cleanup_on_termination
def build_bridge(source, out, jobs=2):
    source = verify_source(source, GEM5_SHA)
    out = out.resolve()
    if not 1 <= jobs <= 4:
        raise ContractError("unsupported gem5 build parallelism")
    out.mkdir(parents=True, exist_ok=False)
    configuration = configure_external_systemc(source, "RISCV", out)
    native = native_identity()
    extension = out / "extension"
    extension.mkdir()
    copy_extension(RESOURCES, native, extension)
    binary = source / "build/RISCV/gem5.opt"
    # EXTRAS is an upstream-supported out-of-tree extension. Reuse object files,
    # but preserve the existing independently verified SE binary at its old path.
    with (source / "build/.usp-build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        backup = out / "base-gem5.opt"
        old_sha = digest(binary) if binary.exists() else None
        if old_sha:
            shutil.copy2(binary, backup)
        command = [
            "scons",
            "build/RISCV/gem5.opt",
            "-j" + str(jobs),
            "--without-tcmalloc",
            "EXTRAS=" + str(extension),
        ]
        config = Path(sys.base_prefix) / "bin/python3-config"
        if config.exists():
            command.append("PYTHON_CONFIG=" + str(config))
        try:
            execute(command, source, out / "build.log", 3600, python_environment())
            shutil.copy2(binary, out / "gem5.opt")
        finally:
            if old_sha:
                shutil.copy2(backup, binary)
                if digest(binary) != old_sha:
                    raise ContractError("failed to restore existing gem5 executable")
    verify_source(source, GEM5_SHA)
    if native != native_identity():
        raise ContractError("bridge source changed during compilation")
    shutil.copy2(source / "LICENSE", out / "gem5-LICENSE")
    record_gem5_configuration(out, out / "gem5.opt", configuration)
    record = {
        "contract": "systemc-ips-gem5-v1",
        "configuration": configuration,
        "source_commit": GEM5_SHA,
        "native_identity": native,
        "base_binary_sha256": old_sha,
        "binary_sha256": digest(out / "gem5.opt"),
        "files": {p.relative_to(out).as_posix(): digest(p) for p in extension.iterdir()},
    }
    write_json(out / "bridge-build.json", record)
    return record


def verify_bridge(out):
    out = out.resolve()
    r = json.loads((out / "bridge-build.json").read_text())
    verify_gem5_configuration(out, r)
    if (
        r["contract"] != "systemc-ips-gem5-v1"
        or r["source_commit"] != GEM5_SHA
        or r["native_identity"] != native_identity()
        or r["binary_sha256"] != digest(out / "gem5.opt")
    ):
        raise ContractError("stale or modified unified gem5 bridge")
    for name, sha in r["files"].items():
        if digest(inside(out, name)) != sha:
            raise ContractError("modified gem5 extension source")
    return r


def board_contract(project, max_ticks=2000000000000):
    resolve(project)
    if project.manifest["profile"] != "usp-rv64-ips-v1":
        raise ContractError("unified RTL binding requires usp-rv64-ips-v1")
    if type(max_ticks) is not int or not 1 <= max_ticks <= 10000000000000:
        raise ContractError("unsupported gem5 simulation bound")
    values = {
        i.name + "_" + key: value
        for i in project.ir.instances
        for w in i.windows
        for key, value in (("base", w.base), ("size", w.size))
    }
    values.update({i.source + "_irq": i.line for i in project.ir.interrupts if i.target == "plic"})
    values.update(
        timebase_hz=project.ir.cpu.timebase_hz,
        reset_vector=project.ir.cpu.reset_vector,
        entry=project.ir.cpu.entry,
        plic_sources=32,
        max_ticks=max_ticks,
    )
    return values


def binding_plan(project, engine="gem5"):
    if engine not in {"qemu", "gem5"}:
        raise ContractError("unsupported unified CPU engine")
    board = board_contract(project)
    native_models = {
        "rv64": "RiscvTimingSimpleCPU",
        "ram": "SimpleMemory",
        "rom": "SimpleMemory+usp-rv64-reset-rom-v1",
        "clint": "Clint",
        "plic": "Plic",
        "uart": "RiscvUart8250",
        "finisher": "UspBridge.finisher-v1",
    }
    bindings = []
    for model in resolve(project):
        role = model["instance"]
        rtl = role in {"cygcd", "otgpio"}
        bindings.append(
            {
                "instance": role,
                "vlnv": model["vlnv"],
                "owner": "systemc-verilator" if rtl else engine,
                "implementation": model["implementation"]
                if engine == "qemu" or rtl
                else native_models[role],
                "source_commit": model["upstream_commit"] if engine == "qemu" or rtl else GEM5_SHA,
            }
        )
    return {
        "profile": "unified-systemc-v1",
        "engine": engine,
        "ir_sha256": identity(project),
        "board": board,
        "bindings": bindings,
        "time_contract": "transaction-boundary synchronization; uncalibrated latency",
    }


def trace_checks(transactions):
    return {
        "gcd_results": [t["value"] for t in transactions if t["op"] == 0 and t["offset"] == 12]
        == [6, 6, 255, 1],
        "gcd_irq": any(t["irq"] & 1 for t in transactions),
        "gpio_irq": any(
            t["op"] == 1 and t["offset"] == 0x1014 and t["data"] == 1 and t["irq"] & 2
            for t in transactions
        ),
        "gpio_w1c": any(
            t["op"] == 1 and t["offset"] == 0x1000 and t["data"] == 1 and not t["irq"] & 2
            for t in transactions
        ),
        "no_bus_errors": bool(transactions) and all(t["status"] == 0 for t in transactions),
    }


def semantic_trace(transactions):
    """Keep actual commands/data; drop status polling and simulator timestamps."""
    return [
        {"op": t["op"], "offset": t["offset"], "data": t["data"] if t["op"] == 1 else t["value"]}
        for t in transactions
        if t["op"] in {1, 2} or t["offset"] == 12 or t["offset"] >= 0x1000
    ]


MARKERS = [
    "USP_ZEPHYR_BOOT_OK",
    "USP_TIMER_IRQ_OK",
    "USP_UART_PLIC_IRQ_OK",
    "USP_CHIPYARD_GCD_IRQ_OK",
    "USP_OPENTITAN_GPIO_IRQ_OK",
    "USP_TEST_PASS",
]


@cleanup_on_termination
def run_system(project, generated, models, bridge, out, timeout=30, max_ticks=2000000000000):
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ContractError("invalid unified runtime timeout")
    contract = board_contract(project, max_ticks)
    generated, models, bridge, out = (p.resolve() for p in (generated, models, bridge, out))
    verify_generated(project, generated)
    model_record, bridge_record = verify_models(models), verify_bridge(bridge)
    elf = generated / "zephyr-build/zephyr/zephyr.elf"
    software = json.loads((generated / "zephyr-build.json").read_text())
    if software["elf_sha256"] != digest(elf) or software["generated_manifest_sha256"] != digest(
        generated / "manifest.json"
    ):
        raise ContractError("stale or modified shared Zephyr firmware")
    out.mkdir(parents=True, exist_ok=False)
    # Original RV64 reset trampoline: AUIPC t0; CSRRS a0,mhartid; a1=0;
    # LD t0,24(t0); JALR t0; NOP; 64-bit entry literal. Firmware owns RAM.
    rom = struct.pack(
        "<6IQ",
        0x00000297,
        0xF1402573,
        0x00000593,
        0x0182B283,
        0x00028067,
        0x00000013,
        contract["entry"],
    )
    if contract["reset_vector"] != contract["rom_base"] or len(rom) > contract["rom_size"]:
        raise ContractError("unsupported unified reset ROM geometry")
    (out / "rom.bin").write_bytes(rom)
    contract["rom_image"] = str(out / "rom.bin")
    write_json(out / "board.json", contract)
    write_json(out / "engine-bindings.json", binding_plan(project))
    shutil.copy2(RESOURCES / "board.py.in", out / "board.py")
    timed_out = False
    with tempfile.TemporaryDirectory(prefix="usp-unified-") as name:
        sock = Path(name) / "bus.sock"
        with (out / "bridge-model.log").open("wb") as stream:
            model = subprocess.Popen(
                [str(models / "rtl/Vusp_ipblocks"), str(sock), str(out / "bridge-trace.jsonl")],
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 5
                while not sock.exists():
                    if model.poll() is not None or time.monotonic() > deadline:
                        raise ContractError("unified SystemC process did not become ready")
                    time.sleep(0.01)
                argv = [
                    str(bridge / "gem5.opt"),
                    "--listener-mode=off",
                    "--outdir=" + str(out),
                    str(out / "board.py"),
                    str(out / "board.json"),
                    str(sock),
                    str(elf),
                    str(out / "exit.json"),
                ]
                write_json(out / "command.json", {"argv": argv, "cwd": str(out)})
                with (out / "stdout.log").open("wb") as stdout:
                    guest = subprocess.Popen(
                        argv,
                        cwd=out,
                        env=python_environment(),
                        stdout=stdout,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    try:
                        rc = guest.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        os.killpg(guest.pid, signal.SIGKILL)
                        rc = guest.wait()
                    finally:
                        if guest.poll() is None:
                            os.killpg(guest.pid, signal.SIGKILL)
                            guest.wait()
                try:
                    model.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(model.pid, signal.SIGKILL)
                    model.wait()
            finally:
                if model.poll() is None:
                    os.killpg(model.pid, signal.SIGKILL)
                    model.wait()
    terminals = list(out.glob("*.terminal"))
    serial = b"".join(p.read_bytes() for p in sorted(terminals))
    (out / "uart.log").write_bytes(serial)
    text = serial.decode(errors="replace")
    trace = (
        [json.loads(s) for s in (out / "bridge-trace.jsonl").read_text().splitlines()]
        if (out / "bridge-trace.jsonl").exists()
        else []
    )
    exit_info = json.loads((out / "exit.json").read_text()) if (out / "exit.json").exists() else {}
    stats = {}
    if (out / "stats.txt").exists():
        for line in (out / "stats.txt").read_text().splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0] in {"simInsts", "simTicks", "simSeconds"}:
                stats[fields[0]] = float(fields[1])
    checks = {
        **trace_checks(trace),
        **{m: text.count(m) == 1 for m in MARKERS},
        "guest_exit": rc == 0
        and exit_info.get("cause") == "USP guest finisher"
        and exit_info.get("code") == 0,
        "model_exit": model.returncode == 0,
        "actual_cpu_execution": stats.get("simInsts", 0) > 0,
    }
    report = {
        "status": "PASSED" if all(checks.values()) else "TIMED_OUT" if timed_out else "FAILED",
        "profile": "unified-systemc-gem5-v1",
        "engine": "gem5-full-system",
        "ir_sha256": identity(project),
        "elf_sha256": digest(elf),
        "model_binary_sha256": model_record["binary_sha256"],
        "bridge_build": bridge_record,
        "checks": checks,
        "returncode": rc,
        "timed_out": timed_out,
        "exit": exit_info,
        "statistics": stats,
        "model_process": {"pid": model.pid, "returncode": model.returncode},
        "adapter_sha256": digest(Path(__file__)),
        "board_template_sha256": digest(RESOURCES / "board.py.in"),
        "limitations": [
            "One gem5 TimingSimpleCPU hart owns RAM; QEMU is an alternative CPU engine",
            "SystemC TLM drives actual Chipyard GCD/OpenTitan GPIO RTL at transaction boundaries",
            "Uncalibrated 100ns PIO latency; no CPU/RTL cycle correlation, DMA or coherence",
            "Engine-native UART/CLINT/PLIC; original reset ROM, no FDT handoff on gem5",
            "No live Garnet integration, checkpoint, full Rocket/Earlgrey or arbitrary-IP binding",
        ],
    }
    shutil.copytree(models / "licenses", out / "licenses")
    write_json(out / "report.json", report)
    (out / "report.md").write_text(
        "# Unified gem5/SystemC SoC\n\nStatus: **"
        + report["status"]
        + "**\n\n"
        + "\n".join(f"- {k}: {v}" for k, v in checks.items())
        + "\n\n"
        + "\n".join(report["limitations"])
        + "\n"
    )
    return report


def cross_checks(out):
    q = json.loads((out / "qemu/report.json").read_text())
    g = json.loads((out / "gem5/report.json").read_text())
    qt = [json.loads(s) for s in (out / "qemu/bridge-trace.jsonl").read_text().splitlines()]
    gt = [json.loads(s) for s in (out / "gem5/bridge-trace.jsonl").read_text().splitlines()]
    ir_sha = hashlib.sha256(
        json.dumps(json.loads((out / "soc-ir.json").read_text()), sort_keys=True).encode()
    ).hexdigest()
    return {
        "qemu_soc": q["status"] == "PASSED",
        "gem5_soc": g["status"] == "PASSED",
        "same_elf": q["software_source"]["elf_sha256"]
        == g["elf_sha256"]
        == digest(out / "firmware.elf"),
        "same_ir": q["ir_sha256"] == g["ir_sha256"] == ir_sha,
        "same_model": q["source_ip_models"]["binary_sha256"] == g["model_binary_sha256"],
        "same_ip_commands_and_data": semantic_trace(qt) == semantic_trace(gt),
        "both_actual_rtl_results_irqs": all(trace_checks(qt).values())
        and all(trace_checks(gt).values()),
    }


def accept_system(project, generated, models, bridge, out):
    board_contract(project)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    run_ipblocks(project, generated.resolve(), models.resolve(), out / "qemu")
    run_system(project, generated, models, bridge, out / "gem5")
    shutil.copy2(generated / "zephyr-build/zephyr/zephyr.elf", out / "firmware.elf")
    write_json(out / "soc-ir.json", project.ir.to_dict())
    write_json(
        out / "engine-bindings.json", {e: binding_plan(project, e) for e in ("qemu", "gem5")}
    )
    shutil.copy2(generated / "models.lock.json", out / "models.lock.json")
    checks = cross_checks(out)
    result = {
        "schema_version": "1",
        "status": "PASSED" if all(checks.values()) else "FAILED",
        "scope": "One nine-role IP-XACT SoC; same Zephyr ELF and RTL IPs on two CPU engines",
        "checks": checks,
        "full_architecture_complete": False,
        "files": {
            p.relative_to(out).as_posix(): digest(p) for p in sorted(out.rglob("*")) if p.is_file()
        },
    }
    write_json(out / "report.json", result)
    (out / "report.md").write_text(
        "# Unified QEMU/gem5/SystemC SoC\n\nStatus: **"
        + result["status"]
        + "**\n\n"
        + "\n".join(f"- {k}: {v}" for k, v in checks.items())
        + "\n\n"
        + result["scope"]
        + "\n\nNot full architecture or timing equivalence.\n"
    )
    return result


def verify_system(out):
    out = out.resolve()
    r = json.loads((out / "report.json").read_text())
    if r["schema_version"] != "1" or r["full_architecture_complete"] is not False:
        raise ContractError("invalid unified SoC completion claim")
    required = {
        "qemu/report.json",
        "gem5/report.json",
        "firmware.elf",
        "soc-ir.json",
        "models.lock.json",
        "engine-bindings.json",
        "qemu/bridge-trace.jsonl",
        "gem5/bridge-trace.jsonl",
    }
    if not required.issubset(r["files"]):
        raise ContractError("incomplete unified SoC evidence")
    for name, sha in r["files"].items():
        if digest(inside(out, name)) != sha:
            raise ContractError("modified unified SoC evidence")
    checks = cross_checks(out)
    if r["checks"] != checks or r["status"] != ("PASSED" if all(checks.values()) else "FAILED"):
        raise ContractError("invalid unified SoC acceptance claim")
    return r


def main(argv):
    parser = argparse.ArgumentParser(prog="usp unified")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("project", type=Path)
    plan.add_argument("--engine", choices=("qemu", "gem5"), default="gem5")
    build = sub.add_parser("build-gem5")
    build.add_argument("--source", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--jobs", type=int, choices=range(1, 5), default=2)
    sub.add_parser("verify-build").add_argument("build", type=Path)
    for cmd in ("run", "accept"):
        p = sub.add_parser(cmd)
        p.add_argument("project", type=Path)
        for key in ("generated", "models", "bridge", "out"):
            p.add_argument("--" + key, type=Path, required=(key != "bridge" or cmd == "accept"))
        if cmd == "run":
            p.add_argument("--engine", choices=("qemu", "gem5"), default="gem5")
    sub.add_parser("verify").add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = binding_plan(load_project(args.project), args.engine)
        elif args.command == "build-gem5":
            result = build_bridge(args.source, args.out, args.jobs)
        elif args.command == "verify-build":
            result = verify_bridge(args.build)
        elif args.command == "verify":
            result = verify_system(args.run_dir)
        elif args.command == "run" and args.engine == "qemu":
            result = run_ipblocks(
                load_project(args.project),
                args.generated.resolve(),
                args.models.resolve(),
                args.out.resolve(),
            )
        else:
            if args.bridge is None:
                raise ContractError("gem5 execution requires --bridge build directory")
            function = run_system if args.command == "run" else accept_system
            result = function(
                load_project(args.project), args.generated, args.models, args.bridge, args.out
            )
        print(json.dumps(result, indent=2))
        return 0 if result.get("status", "PASSED") == "PASSED" else 1
    except (ContractError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}))
        return 2
