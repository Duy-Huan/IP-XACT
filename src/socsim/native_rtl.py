"""Actual source CPU on a native, arbitrary-instance IR memory/peripheral graph."""

import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from .domain import ContractError
from .execution import cleanup_on_termination
from .gem5 import python_environment
from .ipblocks import verify_models
from .ipxact import digest
from .log_capture import LogCapture
from .native_cpu_binding import is_rtl, verify_cpu
from .native_rtl_engine import verify as verify_engine
from .pipeline import QEMU_SHA
from .project import inside
from .rocket_cpu import TRACE_PC, acceptance, captured_streams, rtl_metrics, validate_elf
from .rtl_cpu import wait_socket
from .runner import write_json
from .soc_spec import devices, model_instances

BOARD = Path(__file__).parent / "resources/native_rtl/board.py.in"


def identity():
    root = Path(__file__).parent
    return {
        p: digest(root / p)
        for p in (
            "native_rtl.py",
            "native_cpu_binding.py",
            "native_rtl_engine.py",
            "rocket_cpu.py",
            "log_capture.py",
            "resources/native_rtl/board.py.in",
        )
    }


def check_build(generated):
    from .soc_native import verify_generated

    spec = verify_generated(generated)
    if not is_rtl(spec["cpu"]):
        raise ContractError("actual native RTL run requires an explicit source CPU binding")
    build = json.loads((generated / "build.json").read_text())
    if build["generated_manifest_sha256"] != digest(generated / "manifest.json"):
        raise ContractError("stale native machine/firmware build")
    if "qemu-build/qemu-system-riscv64" in build["files"]:
        if build["source_commit"] != QEMU_SHA:
            raise ContractError("native QEMU source pin does not match its memory engine")
    elif (
        build.get("contract") != "native-gem5-software-bundle-1"
        or set(build["files"]) != {"firmware.elf", "soc.dtb"}
        or digest(Path(build["firmware_compiler"]["path"]))
        != build["firmware_compiler"]["binary_sha256"]
    ):
        raise ContractError("native RTL firmware-only compiler closure is not valid")
    for name, sha in build["files"].items():
        if digest(inside(generated, name)) != sha:
            raise ContractError("modified native machine/firmware build")
    return spec


def peripheral_checks(spec, out, conformance):
    checks = {}
    for d in model_instances(spec):
        path = out / (d["name"] + ".trace.jsonl")
        try:
            tx = [json.loads(line) for line in path.read_text().splitlines()]
            checks[d["name"] + ".transactions"] = (bool(tx) if conformance else True) and all(
                t["status"] == 0 for t in tx
            )
            if conformance and d["model"] == "chipyard.gcd":
                answer = 6 + 2 * devices(spec, "chipyard.gcd").index(d)
                checks[d["name"] + ".result"] = [
                    t["value"] for t in tx if t["op"] == 0 and t["offset"] == 12
                ] == [answer]
            if conformance:
                bit = 2 if d["model"] == "opentitan.gpio" else 1
                checks[d["name"] + ".irq"] = any(t["irq"] & bit for t in tx)
        except (OSError, ValueError, KeyError, TypeError):
            checks[d["name"] + ".transactions"] = False
    return checks


def evaluate(spec, out, record):
    checks, counters = acceptance(
        out,
        record["engine"],
        record["expected_markers"],
        record["driver_returncode"],
        record["engine_returncode"],
        record["timed_out"],
        record["axi_latency_cycles"],
        record["diagnostic_fault_address"],
    )
    checks.update(peripheral_checks(spec, out, record["generated_conformance"]))
    checks["model_exits"] = set(record["model_exit_codes"]) == {
        d["name"] for d in model_instances(spec)
    } and all(v == 0 for v in record["model_exit_codes"].values())
    checks["ram_transport"] = (
        counters.get("ram_bulk_reads", 0) > 0
        if record["engine"] == "qemu"
        else counters.get("ram_bulk_reads") == 0
    )
    capture = json.loads((out / "capture.json").read_text())
    checks["lossless_trace_complete"] = (
        capture["complete"] is True
        and capture["errors"] == []
        and set(capture["streams"]) == captured_streams(record["engine"])
        and all(
            r["compressed_sha256"] == digest(out / (n + ".gz"))
            for n, r in capture["streams"].items()
        )
    )
    return checks, counters


@cleanup_on_termination
def run(
    generated,
    cpu_build,
    models,
    engine,
    out,
    gem5_build=None,
    firmware=None,
    markers=None,
    max_cycles=10000000,
    timeout=120,
    axi_latency=0,
    fault_address=None,
):
    generated, cpu_build, out = (p.resolve() for p in (generated, cpu_build, out))
    spec = check_build(generated)
    cpu = verify_cpu(spec, cpu_build)
    if (
        engine not in {"qemu", "gem5"}
        or engine not in json.loads((generated / "manifest.json").read_text())["engines"]
    ):
        raise ContractError("native RTL engine was not selected during generation")
    if (
        type(max_cycles) is not int
        or not 16 <= max_cycles <= 100000000
        or type(timeout) not in {int, float}
        or not 1 <= timeout <= 1800
        or type(axi_latency) is not int
        or not 0 <= axi_latency <= 16
    ):
        raise ContractError("invalid native RTL execution budget")
    if fault_address is not None and (
        type(fault_address) is not int
        or not any(d["base"] <= fault_address < d["base"] + d["size"] for d in spec["devices"])
    ):
        raise ContractError("native RTL diagnostic fault must be in the declared map")
    model_records = {}
    models = models.resolve() if models else None
    if any(d["model"] in {"chipyard.gcd", "opentitan.gpio"} for d in spec["devices"]):
        if models is None:
            raise ContractError("actual Chipyard/OpenTitan source model build required")
        model_records = verify_models(models)
    conformance = firmware is None
    if conformance:
        from .soc_native import expected_markers

        if spec["external_models"]:
            raise ContractError(
                "external IP requires supplied firmware/markers, never inferred behavior"
            )
        firmware, markers = generated / "firmware.elf", expected_markers(spec)
    if not markers or len(markers) > 40 or any(not isinstance(m, str) or not m for m in markers):
        raise ContractError("native RTL firmware needs explicit acceptance markers")
    entry = validate_elf(firmware, cpu["ram"])
    if entry != spec["boot"]["entry"]:
        raise ContractError("native RTL firmware entry differs from the canonical boot contract")
    backend = generated / "qemu-build/qemu-system-riscv64"
    if engine == "gem5":
        if gem5_build is None:
            raise ContractError("compiled native per-instance RTL gem5 memory engine required")
        gem5_build = gem5_build.resolve()
        verify_engine(gem5_build)
        backend = gem5_build / "gem5.opt"
    runtime = identity()
    input_hashes = {
        "manifest.json": digest(generated / "manifest.json"),
        "native-ir.json": digest(generated / "native-ir.json"),
        "cpu-build": digest(cpu_build / "rocket-build.json"),
    }
    binary_hashes = {
        "cpu": {
            "path": str(cpu_build / "sim/VUspRocket"),
            "sha256": digest(cpu_build / "sim/VUspRocket"),
        },
        "engine": {"path": str(backend), "sha256": digest(backend)},
    }
    out.mkdir(parents=True, exist_ok=False)
    shutil.copy2(firmware, out / "firmware.elf")
    for n in ("native-ir.json", "soc-ir.json", "engine-bindings.json"):
        shutil.copy2(generated / n, out / n)
    (out / "ranges.txt").write_text("".join(f"{d['base']} {d['size']}\n" for d in spec["devices"]))
    (out / "uart.log").touch()
    capture = LogCapture(out)
    for name in captured_streams(engine) - {"cpu.log"}:
        capture.fifo(name)
    processes, logs, commands, instances = [], [], [], {}
    driver = memory = None
    error, timed_out = None, False

    def start(command, name, env=None):
        log = None if name == "cpu.log" else (out / name).open("wb")
        if log is not None:
            logs.append(log)
        commands.append(command)
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE if log is None else log,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        processes.append(proc)
        if log is None:
            capture.capture(proc.stdout, name)
        return proc

    with tempfile.TemporaryDirectory(prefix="usp-native-rtl-") as temp:
        bus, sockets, env = Path(temp) / "cpu.sock", {}, dict(os.environ)
        try:
            for slot, d in enumerate(model_instances(spec)):
                path = Path(temp) / f"{slot}.sock"
                descriptor = spec["external_models"].get(d["model"])
                executable = (
                    inside(generated, descriptor["executable"])
                    if descriptor
                    else models / "rtl/Vusp_ipblocks"
                )
                binary_hashes[d["name"]] = {"path": str(executable), "sha256": digest(executable)}
                instances[d["name"]] = start(
                    [str(executable), str(path), str(out / (d["name"] + ".trace.jsonl"))],
                    d["name"] + ".model.log",
                )
                wait_socket(path, instances[d["name"]], min(timeout, 30))
                sockets[d["name"]] = str(path)
                env[f"USP_NATIVE_SOCKET_{slot}"] = str(path)
            if engine == "qemu":
                command = [
                    str(backend),
                    "-M",
                    "usp-native",
                    "-cpu",
                    "rv64",
                    "-smp",
                    "1",
                    "-accel",
                    "qtest",
                    "-m",
                    str(cpu["ram"]["size"] // 1024) + "K",
                    "-display",
                    "none",
                    "-monitor",
                    "none",
                    "-kernel",
                    str(out / "firmware.elf"),
                    "-qtest",
                    "unix:" + str(bus) + ",server=on,wait=off",
                    "-qtest-log",
                    str(out / "qtest.log"),
                ]
                for d in devices(spec, "uart"):
                    command += ["-serial", "file:" + str(out / (d["name"] + ".uart.log"))]
            else:
                (out / "board.py").write_bytes(BOARD.read_bytes())
                write_json(
                    out / "board.json",
                    {
                        "spec": spec,
                        "elf": str(out / "firmware.elf"),
                        "sockets": sockets,
                        "rtl_socket": str(bus),
                        "max_ticks": 100000000000000,
                        "uart_index": str(out / "uart-index.json"),
                        "exit_file": str(out / "exit.json"),
                    },
                )
                command = [
                    str(backend),
                    "--listener-mode=off",
                    "--outdir=" + str(out),
                    str(out / "board.py"),
                    str(out / "board.json"),
                ]
                env = python_environment()
            memory = start(command, "engine.log", env)
            wait_socket(bus, memory, min(timeout, 30))
            driver = start(
                [
                    str(cpu_build / "sim/VUspRocket"),
                    engine,
                    str(bus),
                    str(out / "bus-trace.jsonl"),
                    str(out / "rtl-counters.json"),
                    str(entry),
                    str(max_cycles),
                    str(1000000000 // spec["cpu"]["clock_hz"]),
                    str(devices(spec, "finisher")[0]["base"]),
                    str(out / "ranges.txt"),
                    "16",
                    str(axi_latency),
                    "none" if fault_address is None else str(fault_address),
                    str(cpu["ram"]["base"]),
                    str(cpu["ram"]["size"]),
                    "1" if engine == "qemu" else "0",
                ],
                "cpu.log",
            )
            try:
                driver.wait(timeout=timeout)
                if driver.returncode == 0:
                    memory.wait(timeout=5)
                    for proc in instances.values():
                        proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                timed_out = True
        except (ContractError, OSError, TimeoutError) as exc:
            error = str(exc)
        finally:
            for proc in reversed(processes):
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
            for stream in logs:
                stream.close()
            capture.finish()
    if engine == "gem5" and (out / "uart-index.json").exists():
        for name, path in json.loads((out / "uart-index.json").read_text()).items():
            terminal = inside(out, path)
            if terminal.exists():
                shutil.copy2(terminal, out / (name + ".uart.log"))
    console = out / (spec["boot"]["console"] + ".uart.log")
    if console.exists():
        shutil.copy2(console, out / "uart.log")
    record = {
        "profile": "native-rtl-cpu-v1",
        "engine": engine,
        "runtime_identity": runtime,
        "generated": str(generated),
        "cpu_build": str(cpu_build),
        "gem5_build": str(gem5_build) if gem5_build else None,
        "inputs": input_hashes,
        "firmware_sha256": digest(out / "firmware.elf"),
        "ir_sha256": digest(out / "soc-ir.json"),
        "native_ir_sha256": digest(out / "native-ir.json"),
        "binary_artifacts": binary_hashes,
        "generated_conformance": conformance,
        "expected_markers": markers,
        "driver_returncode": driver.returncode if driver else None,
        "engine_returncode": memory.returncode if memory else None,
        "model_exit_codes": {n: p.returncode for n, p in instances.items()},
        "timed_out": timed_out,
        "error": error,
        "commands": commands,
        "max_cycles": max_cycles,
        "timeout_seconds": timeout,
        "axi_latency_cycles": axi_latency,
        "diagnostic_fault_address": fault_address,
        "trace_pc_encoding": TRACE_PC,
        "model_build": model_records,
        "models_root": str(models) if model_records else None,
        "ownership": {
            "instructions": "actual-RocketTile-RTL",
            "backing_memory": engine,
            "native_cpu": "halted IRQ context",
            "peripheral_state": "per-instance source models",
        },
        "limitations": [
            "one RV64IMAC hart; AXI PA32 and one RAM PMA region",
            "atomic backing memory; no external DMA coherence or silicon timing calibration",
        ],
        "full_master_objective_complete": False,
    }
    checks, counters = evaluate(spec, out, record)
    checks["unchanged_inputs"] = runtime == identity() and all(
        digest(Path(a["path"])) == a["sha256"] for a in binary_hashes.values()
    )
    verify_cpu(spec, cpu_build)
    checks["unchanged_inputs"] &= input_hashes["manifest.json"] == digest(
        generated / "manifest.json"
    )
    record.update(
        status="PASSED" if error is None and all(checks.values()) else "FAILED",
        checks=checks,
        counters=counters,
        performance=rtl_metrics(counters),
    )
    record["files"] = {
        p.relative_to(out).as_posix(): digest(p) for p in out.rglob("*") if p.is_file()
    }
    write_json(out / "report.json", record)
    (out / "report.md").write_text(
        f"# Native RTL SoC: {record['status']}\n\n"
        + "\n".join(f"- {k}: {v}" for k, v in checks.items())
        + "\n"
    )
    return record


def verify(out):
    record = json.loads((out / "report.json").read_text())
    if record["profile"] != "native-rtl-cpu-v1" or record["runtime_identity"] != identity():
        raise ContractError("modified/stale native RTL execution adapter")
    generated, cpu = Path(record["generated"]), Path(record["cpu_build"])
    spec = check_build(generated)
    build = verify_cpu(spec, cpu)
    if record["inputs"] != {
        "manifest.json": digest(generated / "manifest.json"),
        "native-ir.json": digest(generated / "native-ir.json"),
        "cpu-build": digest(cpu / "rocket-build.json"),
    }:
        raise ContractError("mixed native IR/RTL CPU build evidence")
    required = {
        "native-ir.json",
        "soc-ir.json",
        "firmware.elf",
        "capture.json",
        "uart.log",
        "rtl-counters.json",
        *{n + ".gz" for n in captured_streams(record["engine"])},
    }
    if not required.issubset(record["files"]):
        raise ContractError("incomplete native RTL execution dependency closure")
    for name, sha in record["files"].items():
        if digest(inside(out, name)) != sha:
            raise ContractError("modified native RTL run artifact")
    if (
        record["firmware_sha256"] != digest(out / "firmware.elf")
        or record["ir_sha256"] != digest(out / "soc-ir.json")
        or record["native_ir_sha256"] != digest(out / "native-ir.json")
        or record["native_ir_sha256"] != record["inputs"]["native-ir.json"]
    ):
        raise ContractError("mixed native RTL firmware/hardware identity")
    for artifact in record["binary_artifacts"].values():
        if digest(Path(artifact["path"])) != artifact["sha256"]:
            raise ContractError("modified native RTL binary dependency")
    expected = {
        "cpu": cpu / "sim/VUspRocket",
        "engine": Path(record["gem5_build"]) / "gem5.opt"
        if record["engine"] == "gem5"
        else generated / "qemu-build/qemu-system-riscv64",
    }
    for d in model_instances(spec):
        descriptor = spec["external_models"].get(d["model"])
        expected[d["name"]] = (
            inside(generated, descriptor["executable"])
            if descriptor
            else Path(record["models_root"]) / "rtl/Vusp_ipblocks"
        )
    if (
        record["model_build"]
        and verify_models(Path(record["models_root"])) != record["model_build"]
    ):
        raise ContractError("native RTL source IP model identity changed")
    if set(expected) != set(record["binary_artifacts"]) or any(
        record["binary_artifacts"][n]["sha256"] != digest(p)
        or Path(record["binary_artifacts"][n]["path"]).resolve() != p.resolve()
        for n, p in expected.items()
    ):
        raise ContractError("native RTL binary owner/identity closure is incomplete")
    for n in ("native-ir.json", "soc-ir.json", "engine-bindings.json"):
        if digest(out / n) != digest(generated / n):
            raise ContractError("native RTL copied hardware differs from generated design")
    if record["engine"] == "gem5":
        verify_engine(Path(record["gem5_build"]))
        if (out / "board.py").read_bytes() != BOARD.read_bytes():
            raise ContractError("wrong native gem5 memory/IRQ ownership")
    if validate_elf(out / "firmware.elf", build["ram"]) != spec["boot"]["entry"]:
        raise ContractError("native RTL boot does not match canonical IR")
    checks, counters = evaluate(spec, out, record)
    checks["unchanged_inputs"] = True
    if (
        record["status"] != "PASSED"
        or record["error"] is not None
        or not all(checks.values())
        or checks != record["checks"]
        or counters != record["counters"]
        or counters["cycles"] > record["max_cycles"]
        or record["trace_pc_encoding"] != TRACE_PC
        or record["performance"] != rtl_metrics(counters)
    ):
        raise ContractError("native RTL run lacks successful source CPU/instance evidence")
    return record
