"""Actual Rocket instructions over AXI, with QEMU/gem5 physical memory ownership."""

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from string import Template

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from .domain import ContractError, CpuContract
from .execution import cleanup_on_termination
from .gem5 import python_environment
from .ipblocks import verify_models
from .ipxact import digest
from .pipeline import execute, identity, verify_generated
from .rocket_build import CPU_CONTRACT, PINS, RESOURCES, address_map_contract, verify_build
from .rtl_cpu import MARKERS, ZERO_COUNTERS, read_rows, unmapped_test_address, wait_socket
from .rtl_cpu_build import verify_engine
from .runner import tool_version, write_json
from .unified import board_contract, trace_checks


def runtime_identity():
    root = Path(__file__).parent
    return {
        name: digest(root / name)
        for name in (
            "rocket_cpu.py",
            "rocket_software.py",
            "unified.py",
            "rtl_cpu.py",
            "execution.py",
            "resources/rtl_cpu/board.py.in",
            "resources/layers/start.S",
            "resources/layers/firmware.ld.in",
        )
    }


def layout(project):
    b = board_contract(project)
    ram = {"base": b["ram_base"], "size": b["ram_size"]}
    regions = [
        {"base": w.base, "size": w.size, "kind": "rom" if i.name == "rom" else "mmio"}
        for i in project.ir.instances
        if i.name not in {"ram", "rv64"}
        for w in i.windows
    ]
    return ram, address_map_contract(ram, regions)


def target_ir(project, build, entry=None):
    ram, regions = layout(project)
    if build["ram"] != ram or build["regions"] != regions:
        raise ContractError("Rocket PMA layout differs from the selected SoC IR")
    b = board_contract(project)
    cpu = CpuContract(
        str(CPU_CONTRACT["isa"]),
        "MSU",
        "sv39",
        ram["base"] if entry is None else entry,
        ram["base"] if entry is None else entry,
        b["timebase_hz"],
    )
    return replace(
        project.ir,
        cpu=cpu,
        instances=tuple(
            replace(
                i,
                name="cpu",
                hierarchy_path=("cpu",),
                parameters=(),
                vlnv="socsim.pinned:chipyard:rocket64-imac-axi32:1.0",
            )
            if i.name == "rv64"
            else i
            for i in project.ir.instances
        ),
        interrupts=tuple(
            replace(i, target="cpu") if i.target == "rv64" else i for i in project.ir.interrupts
        ),
        address_spaces=tuple(replace(s, initiator="cpu") for s in project.ir.address_spaces),
        sources=tuple(sorted((*project.ir.sources, ("rocket-source", PINS["rocket-chip"])))),
    )


def validate_elf(elf, ram):
    try:
        if elf.stat().st_size > 64 * 1024**2:
            raise ContractError("oversized Rocket firmware")
        with elf.open("rb") as file:
            e = ELFFile(file)
            if (
                e.elfclass != 64
                or not e.little_endian
                or e["e_machine"] != "EM_RISCV"
                or e["e_type"] != "ET_EXEC"
                or e["e_flags"] & ~1
                or e["e_entry"] % 2
            ):
                raise ContractError("Rocket profile requires an executable RV64IMAC LP64 ELF")
            segments = [s for s in e.iter_segments() if s["p_type"] == "PT_LOAD" and s["p_memsz"]]
            if not 1 <= len(segments) <= 128:
                raise ContractError("invalid Rocket firmware load segments")
            executable_entry = False
            for s in segments:
                if (
                    s["p_paddr"] != s["p_vaddr"]
                    or s["p_filesz"] > s["p_memsz"]
                    or s["p_offset"] + s["p_filesz"] > elf.stat().st_size
                    or s["p_paddr"] < ram["base"]
                    or s["p_paddr"] + s["p_memsz"] > ram["base"] + ram["size"]
                ):
                    raise ContractError("Rocket firmware load escapes the declared RAM")
                executable_entry |= bool(
                    s["p_flags"] & 1 and s["p_vaddr"] <= e["e_entry"] < s["p_vaddr"] + s["p_memsz"]
                )
            if not executable_entry:
                raise ContractError("Rocket entry is not inside executable RAM")
            return e["e_entry"]
    except ELFError as exc:
        raise ContractError("invalid Rocket ELF container") from exc


def build_firmware(project, out):
    out.mkdir(parents=True, exist_ok=False)
    b = board_contract(project)
    (out / "firmware.c").write_text(
        Template((RESOURCES / "firmware.c.in").read_text()).substitute(
            gcd_irq=b["cygcd_irq"], gpio_irq=b["otgpio_irq"], timer_ticks=100
        )
    )
    layers = RESOURCES.parent / "layers"
    shutil.copy2(layers / "start.S", out / "start.S")
    shutil.copy2(layers / "firmware.ld.in", out / "firmware.ld")
    (out / "soc.h").write_text(
        "#include <stdint.h>\n"
        + f"#define USP_FAULT_ADDRESS {unmapped_test_address(project.ir)}UL\n"
        + "".join(
            f"#define USP_{n.upper()}_BASE 0x{b[n + '_base']:x}UL\n"
            for n in ("uart", "clint", "plic", "finisher", "cygcd", "otgpio")
        )
    )
    (out / "memory.ld").write_text(
        f"MEMORY {{ RAM (rwx) : ORIGIN = 0x{b['ram_base']:x}, LENGTH = 0x{b['ram_size']:x} }}\n"
    )
    compiler = os.environ.get("CROSS_COMPILE", "riscv64-unknown-elf-") + "gcc"
    common = [
        compiler,
        "-march=rv64imac_zicsr_zifencei",
        "-mabi=lp64",
        "-mcmodel=medany",
        "-mno-relax",
        "-msmall-data-limit=0",
        "-nostdlib",
        "-nostartfiles",
        "-ffreestanding",
        "-O2",
        "-Wall",
        "-Werror",
        "-Wl,--build-id=none",
    ]
    for name in ("start.S", "firmware.c"):
        execute([*common, "-c", name, "-o", name + ".o"], out, out / (name + ".log"), 60)
    execute(
        [*common, "-T", "firmware.ld", "start.S.o", "firmware.c.o", "-o", "firmware.elf"],
        out,
        out / "link.log",
        60,
    )
    _, version = tool_version(compiler)
    write_json(
        out / "software-build.json",
        {
            "soc_ir_sha256": identity(project),
            "compiler": version,
            "files": {
                name: digest(out / name)
                for name in (
                    "start.S",
                    "firmware.c",
                    "soc.h",
                    "memory.ld",
                    "firmware.ld",
                    "firmware.elf",
                )
            },
        },
    )
    return out / "firmware.elf"


def axi_checks(path, counters):
    rows = events(path)
    counts = {k: 0 for k in ("AR", "AW", "R", "W", "B")}
    read = write = None
    maximum = 0
    try:
        for row in rows:
            channel = row["channel"]
            if (
                any(
                    type(row.get(k)) is not int
                    for k in ("cycle", "address", "id", "data", "response")
                )
                or not 0 <= row["cycle"] < counters["cycles"]
                or not 0 <= row["address"] < 2**32
                or not 0 <= row["id"] < 16
                or row["response"] != 0
            ):
                return False
            counts[channel] += 1
            if channel in {"AR", "AW"}:
                if not 1 <= row["data"] <= 256:
                    return False
                maximum = max(maximum, row["data"])
                request = [row["id"], row["data"]]
                if channel == "AR":
                    if read is not None:
                        return False
                    read = request
                else:
                    if write is not None:
                        return False
                    write = request
            elif channel == "R":
                if read is None or row["id"] != read[0] or read[1] < 1:
                    return False
                read[1] -= 1
                if read[1] == 0:
                    read = None
            elif channel == "W":
                if write is None or row["id"] != write[0] or write[1] < 1:
                    return False
                write[1] -= 1
            else:
                if write is None or row["id"] != write[0] or write[1] != 0:
                    return False
                write = None
        return (
            bool(rows)
            and read is None
            and write is None
            and maximum == counters["max_burst_beats"]
            and all(
                counts[k] == counters[name]
                for k, name in (
                    ("AR", "axi_ar"),
                    ("AW", "axi_aw"),
                    ("R", "axi_r_beats"),
                    ("W", "axi_w_beats"),
                    ("B", "axi_b"),
                )
            )
        )
    except (KeyError, TypeError):
        return False


def events(path):
    """A truncated/crashed raw log cannot become successful partial evidence."""
    try:
        rows = read_rows(path)
        return rows if all(isinstance(row, dict) for row in rows) else []
    except (OSError, ValueError):
        return []


def acceptance(
    out, engine, markers, cpu_rc, engine_rc, timed_out, axi_latency=0, fault_address=None
):
    try:
        counters = json.loads((out / "rtl-counters.json").read_text())
    except (OSError, ValueError):
        counters = {}
    if not isinstance(counters, dict):
        counters = {}
    commits = events(out / "rtl-counters.json.commit.jsonl")
    n = sum(row.get("trap") is False or row.get("trap") == 0 for row in commits)
    traps = sum(row.get("trap") is True or row.get("trap") == 1 for row in commits)
    valid = bool(commits) and all(
        type(x.get("cycle")) is int
        and type(x.get("pc")) is int
        and type(x.get("instruction")) is int
        and type(x.get("trap")) in {int, bool}
        and x["trap"] in {0, 1}
        and x["cycle"] >= 0
        and 0 <= x["pc"] < 2**64
        and 0 <= x["instruction"] < 2**32
        for x in commits
    )
    valid = valid and all(
        a["cycle"] < b["cycle"] for a, b in zip(commits, commits[1:], strict=False)
    )
    counter_valid = all(
        type(counters.get(k)) is int and counters[k] >= 0
        for k in (
            "cycles",
            "instructions",
            "traps",
            "signature",
            "finished",
            "axi_ar",
            "axi_aw",
            "axi_r_beats",
            "axi_w_beats",
            "axi_b",
            "axi_errors",
            "axi_stalls",
            "max_burst_beats",
        )
    )
    uart = (out / "uart.log").read_text(errors="replace") if (out / "uart.log").exists() else ""
    checks = {
        "no_timeout": not timed_out,
        "cpu_exit": cpu_rc == 0,
        "engine_exit": engine_rc == 0,
        "valid_counters": counter_valid,
        "actual_retirement": valid
        and n > 0
        and counters.get("instructions") == n
        and counters.get("traps") == traps,
        "clock_edges": valid and counter_valid and counters["cycles"] > commits[-1]["cycle"],
        "native_finisher": counters.get("finished") == 1 and counters.get("signature") == 0x5555,
        "axi_protocol": axi_checks(out / "rtl-counters.json.axi.jsonl", counters)
        if counter_valid
        else False,
        "no_downstream_bus_error": counters.get("axi_errors") == 0,
        "uart_markers": all(uart.splitlines().count(m) == 1 for m in markers),
        "diagnostic_fault_disabled": fault_address is None,
        "axi_latency_configuration": counters.get("axi_latency") == axi_latency,
        "actual_request_backpressure": axi_latency == 0
        or (type(counters.get("axi_request_stalls")) is int and counters["axi_request_stalls"] > 0),
    }
    if markers == MARKERS:
        try:
            checks.update(trace_checks(events(out / "ip-trace.jsonl")))
        except (KeyError, TypeError):
            checks["valid_ip_trace"] = False
        bus = events(out / "bus-trace.jsonl")
        checks["physical_byte_and_word_accesses"] = {1, 4}.issubset(
            {row.get("width") for row in bus}
        )
        checks["retired_subword_and_wide_stores"] = {0, 1, 3}.issubset(
            {(x["instruction"] >> 12) & 7 for x in commits if x.get("instruction", 0) & 127 == 0x23}
        )
    from .freertos import MARKERS as RTOS_MARKERS
    from .freertos import acceptance as rtos_acceptance
    from .rocket_software import MMU_MARKERS

    if markers == list(RTOS_MARKERS):
        checks.update(rtos_acceptance(uart)[0])
    if markers == MMU_MARKERS:
        checks["actual_virtual_user_retirement"] = any(
            0x50000000 <= c.get("pc", -1) < 0x50001000 for c in commits
        )
        checks["actual_privilege_faults"] = traps >= 5
    if engine == "gem5":
        stats = {}
        for line in (
            (out / "stats.txt").read_text().splitlines() if (out / "stats.txt").exists() else []
        ):
            parts = line.split()
            if len(parts) > 1 and parts[0] in ZERO_COUNTERS:
                stats[parts[0]] = float(parts[1])
        checks["no_native_iss"] = set(stats) == ZERO_COUNTERS and all(
            v == 0 for v in stats.values()
        )
    return checks, counters


@cleanup_on_termination
def run(
    project,
    cpu_build,
    generated,
    models,
    engine,
    out,
    bridge=None,
    elf=None,
    markers=None,
    clock_hz=50000000,
    quantum=16,
    max_cycles=1000000,
    timeout=120,
    axi_latency=0,
    fault_address=None,
):
    if engine not in {"qemu", "gem5"}:
        raise ContractError("unsupported Rocket memory engine")
    if (
        type(clock_hz) is not int
        or not 100000 <= clock_hz <= 500000000
        or 1000000000 % clock_hz
        or type(quantum) is not int
        or not 1 <= quantum <= 64
        or type(max_cycles) is not int
        or not 16 <= max_cycles <= 10000000
        or type(axi_latency) is not int
        or not 0 <= axi_latency <= 16
        or not 1 <= timeout <= 300
    ):
        raise ContractError("invalid Rocket execution bounds")
    cpu_build, generated, models, out = (p.resolve() for p in (cpu_build, generated, models, out))
    build = verify_build(cpu_build)
    ir = target_ir(project, build)
    b = board_contract(project)
    if fault_address is not None and (
        type(fault_address) is not int
        or not any(
            w.base <= fault_address < w.base + w.size for i in ir.instances for w in i.windows
        )
    ):
        raise ContractError("diagnostic fault address must lie in the declared physical map")
    verify_generated(project, generated)
    model = verify_models(models)
    if out.exists():
        raise ContractError("preserve existing Rocket run evidence")
    if engine == "qemu":
        backend_binary = generated / "qemu-build/qemu-system-riscv64"
        backend_record = json.loads((generated / "qemu-build.json").read_text())
        if backend_record["binary_sha256"] != digest(backend_binary) or backend_record[
            "generated_manifest_sha256"
        ] != digest(generated / "manifest.json"):
            raise ContractError("modified/stale QEMU memory engine")
    else:
        if bridge is None:
            raise ContractError("verified gem5 external memory master required")
        backend_record = verify_engine(bridge)
        backend_binary = bridge.resolve() / "gem5.opt"
    out.mkdir(parents=True)
    if elf is None:
        elf = build_firmware(project, out / "firmware")
        markers = MARKERS
    else:
        elf = elf.resolve()
    if not markers or len(markers) > 40 or any(not isinstance(m, str) or not m for m in markers):
        raise ContractError("explicit nonempty software acceptance markers required")
    entry = validate_elf(elf, build["ram"])
    ir = target_ir(project, build, entry)
    shutil.copy2(elf, out / "firmware.elf")
    write_json(out / "soc-ir.json", ir.to_dict())
    (out / "ranges.txt").write_text(
        "".join(f"{w.base} {w.size}\n" for i in ir.instances for w in i.windows)
    )
    (out / "uart.log").touch()
    processes = []
    logs = []
    commands = []
    driver = backend = None
    timed_out = False
    error = None

    def start(command, name, env=None):
        file = (out / name).open("wb")
        logs.append(file)
        commands.append(command)
        proc = subprocess.Popen(
            command, stdout=file, stderr=subprocess.STDOUT, env=env, start_new_session=True
        )
        processes.append(proc)
        return proc

    with tempfile.TemporaryDirectory(prefix="usp-rocket-") as temp:
        ip = Path(temp) / "ip.sock"
        bus = Path(temp) / "cpu.sock"
        try:
            peripheral = start(
                [str(models / "rtl/Vusp_ipblocks"), str(ip), str(out / "ip-trace.jsonl")],
                "model.log",
            )
            wait_socket(ip, peripheral, min(timeout, 30))
            if engine == "qemu":
                env = dict(os.environ, USP_BRIDGE_SOCKET=str(ip))
                command = [
                    str(backend_binary),
                    "-machine",
                    "usp-rv64",
                    "-cpu",
                    "rv64",
                    "-accel",
                    "qtest",
                    "-display",
                    "none",
                    "-monitor",
                    "none",
                    "-serial",
                    "file:" + str(out / "uart.log"),
                    "-m",
                    str(b["ram_size"] // 1048576) + "M",
                    "-kernel",
                    str(out / "firmware.elf"),
                    "-qtest",
                    "unix:" + str(bus) + ",server=on,wait=off",
                    "-qtest-log",
                    str(out / "qtest.log"),
                ]
            else:
                board = dict(
                    b,
                    ram_image=str(out / "firmware.elf"),
                    rtl_socket=str(bus),
                    cpu_model="RiscvAtomicSimpleCPU",
                )
                write_json(out / "board.json", board)
                (out / "board.py").write_text(
                    (RESOURCES.parent / "rtl_cpu/board.py.in")
                    .read_text()
                    .replace("riscv_type = 'RV32'", "riscv_type = 'RV64'")
                )
                env = python_environment()
                command = [
                    str(backend_binary),
                    "--outdir=" + str(out),
                    str(out / "board.py"),
                    str(out / "board.json"),
                    str(ip),
                    str(out / "firmware.elf"),
                    str(out / "exit.json"),
                ]
            backend = start(command, "engine.log", env)
            wait_socket(bus, backend, min(timeout, 30))
            driver = start(
                [
                    str(cpu_build / "sim/VUspRocket"),
                    engine,
                    str(bus),
                    str(out / "bus-trace.jsonl"),
                    str(out / "rtl-counters.json"),
                    str(entry),
                    str(max_cycles),
                    str(1000000000 // clock_hz),
                    str(b["finisher_base"]),
                    str(out / "ranges.txt"),
                    str(quantum),
                    str(axi_latency),
                    str(fault_address) if fault_address is not None else "none",
                ],
                "cpu.log",
            )
            try:
                driver.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            if driver.poll() == 0:
                try:
                    backend.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    timed_out = True
        except (ContractError, TimeoutError, OSError) as exc:
            error = str(exc)
            timed_out |= isinstance(exc, TimeoutError)
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
    if engine == "gem5":
        terminal = list(out.glob("*.terminal"))
        if terminal:
            (out / "uart.log").write_bytes(terminal[0].read_bytes())
    driver_rc = driver.returncode if driver else None
    engine_rc = backend.returncode if backend else None
    checks, counters = acceptance(
        out, engine, markers, driver_rc, engine_rc, timed_out, axi_latency, fault_address
    )
    cycles = counters.get("cycles", 0) if checks["valid_counters"] else 0
    instructions = counters.get("instructions", 0) if checks["valid_counters"] else 0
    report = {
        "status": "PASSED" if error is None and all(checks.values()) else "FAILED",
        "cpu_model": CPU_CONTRACT["model"],
        "engine": engine,
        "checks": checks,
        "counters": counters,
        "error": error,
        "driver_returncode": driver_rc,
        "engine_returncode": engine_rc,
        "timed_out": timed_out,
        "expected_markers": markers,
        "commands": commands,
        "clock_hz": clock_hz,
        "quantum": quantum,
        "axi_latency_cycles": axi_latency,
        "diagnostic_bus_fault_address": fault_address,
        "runtime_identity": runtime_identity(),
        "base_io_ir_sha256": identity(project),
        "derived_ir_sha256": digest(out / "soc-ir.json"),
        "firmware_sha256": digest(out / "firmware.elf"),
        "cpu_build_sha256": digest(cpu_build / "rocket-build.json"),
        "cpu_source_pins": PINS,
        "models_binary_sha256": model["binary_sha256"],
        "engine_binary_sha256": digest(backend_binary),
        "performance": {
            "source": "actual Rocket clock edges and commit trace",
            "cycles": cycles,
            "instructions": instructions,
            "cpi": cycles / instructions if instructions else None,
            "ipc": instructions / cycles if cycles else None,
            "calibrated_to_silicon": False,
            "scope": "reset through final-response drain; atomic backing memory; bounded quantum",
        },
        "limitations": [
            "single RV64IMAC RocketTile; 32-bit physical addresses",
            "backing RAM/peripherals owned by the selected engine; no native ISS execution",
            "unmapped PMA access faults; downstream AXI errors reject the run",
            "no external DMA coherence or calibrated global timing; single RocketTile",
        ],
    }
    report["files"] = {str(p.relative_to(out)): digest(p) for p in out.rglob("*") if p.is_file()}
    write_json(out / "report.json", report)
    (out / "report.md").write_text(
        f"# Rocket CPU: {report['status']}\n\n"
        + "\n".join(f"- {name}: {value}" for name, value in checks.items())
        + "\n"
    )
    return report


def verify_run(project, cpu_build, out):
    verify_build(cpu_build)
    report = json.loads((out / "report.json").read_text())
    if (
        report["runtime_identity"] != runtime_identity()
        or report["base_io_ir_sha256"] != identity(project)
        or report["cpu_build_sha256"] != digest(cpu_build / "rocket-build.json")
    ):
        raise ContractError("stale/mixed Rocket run identity")
    required = {
        "soc-ir.json",
        "firmware.elf",
        "rtl-counters.json",
        "uart.log",
        "rtl-counters.json.commit.jsonl",
        "rtl-counters.json.axi.jsonl",
        "bus-trace.jsonl",
    }
    if not required.issubset(report["files"]):
        raise ContractError("incomplete Rocket run artifact closure")
    if report["derived_ir_sha256"] != digest(out / "soc-ir.json") or report[
        "firmware_sha256"
    ] != digest(out / "firmware.elf"):
        raise ContractError("mixed Rocket firmware/derived IR")
    for name, expected in report["files"].items():
        path = (out / name).resolve()
        if not path.is_relative_to(out.resolve()) or digest(path) != expected:
            raise ContractError("modified/escaped Rocket run evidence")
    checks, counters = acceptance(
        out,
        report["engine"],
        report["expected_markers"],
        report["driver_returncode"],
        report["engine_returncode"],
        report["timed_out"],
        report["axi_latency_cycles"],
        report["diagnostic_bus_fault_address"],
    )
    if (
        report["status"] != "PASSED"
        or checks != report["checks"]
        or counters != report["counters"]
        or not all(checks.values())
    ):
        raise ContractError("Rocket run lacks actual successful execution evidence")
    return {"status": "PASSED", "checks": checks}


def selection(config, engine):
    cpu = config["cpu"]
    if engine not in {"qemu", "gem5"} or config["engine"] != engine:
        raise ContractError("unsupported Rocket memory engine")
    if "rtl_manifest" in cpu or "qemu_cost_model" in config:
        raise ContractError("Rocket uses its pinned source profile and actual RTL counters")
    if set(config["workloads"]) - {"baremetal", "freertos"}:
        raise ContractError("selected Rocket software/MMU port is not yet qualified")
    if set(config.get("software_builds", {})) != set(config["workloads"]) - {"baremetal"}:
        raise ContractError(
            "Rocket kernel requires its verified source-built software_builds entry"
        )
    if set(config.get("artifacts", {})) - {"baremetal"}:
        raise ContractError("Rocket OS requires a verified kernel/BSP build")
    hz = cpu.get("clock_hz", 50000000)
    if type(hz) is not int or not 100000 <= hz <= 500000000 or 1000000000 % hz:
        raise ContractError("Rocket clock requires an integral 2..10000ns period")
    return {
        "provider": "chipyard",
        "model": CPU_CONTRACT["model"],
        "engine": engine,
        "clock_hz": hz,
        "status": "AVAILABLE_ADAPTER",
        "rocket_profile": True,
        "adapter": "rocket-axi32-" + engine + "-v1",
        "performance": "native-rtl-counters",
    }


def platform_plan(project, config, selected, base):
    from .execution_plan import (
        AddressRoute,
        ExecutionPlan,
        InterruptRoute,
        ModelBinding,
        TimeDomain,
    )

    ram, regions = layout(project)
    ir = target_ir(project, {"ram": ram, "regions": regions})
    bindings = []
    for instance in ir.instances:
        if instance.name == "cpu":
            bindings.append(
                ModelBinding(
                    "cpu",
                    instance.vlnv,
                    "systemc-verilator-cpu",
                    "RocketTile+TLBroadcast+TLToAXI4",
                    PINS["rocket-chip"],
                )
            )
        else:
            model = next(m for m in base["bindings"] if m["instance"] == instance.name)
            implementation = model["implementation"]
            if config["engine"] == "gem5" and instance.name == "rom":
                implementation = "SimpleMemory.zero-filled-unused-rom-v1"
            bindings.append(
                ModelBinding(
                    instance.name,
                    instance.vlnv,
                    model["owner"],
                    implementation,
                    model["source_commit"],
                )
            )
    plan = ExecutionPlan(
        "1",
        selected["status"],
        hashlib.sha256(json.dumps(ir.to_dict(), sort_keys=True).encode()).hexdigest(),
        hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "chipyard",
        str(CPU_CONTRACT["model"]),
        config["engine"],
        tuple(bindings),
        tuple(
            AddressRoute(
                "cpu",
                i.name,
                w.base,
                w.size,
                "systemc-verilator" if i.name in {"cygcd", "otgpio"} else config["engine"],
                "axi64-to-aligned-mmio32-to-" + config["engine"],
            )
            for i in ir.instances
            for w in i.windows
        ),
        tuple(InterruptRoute(i.source, i.target, i.line, i.trigger, True) for i in ir.interrupts),
        (
            TimeDomain(
                "systemc-verilator-cpu",
                "actual-rtl-clock",
                selected["clock_hz"],
                "one edge per CPU cycle; L1 and PTW remain upstream RTL",
            ),
            TimeDomain(
                config["engine"],
                "atomic-memory-peripherals-only",
                None,
                "explicit bounded clock quantum; passive native CPU",
            ),
            TimeDomain(
                "systemc-verilator", "annotated-peripheral-rtl", 100000000, "per-MMIO transaction"
            ),
        ),
        synchronization="rocket-axi32-physical-master-v1",
    )
    return {
        "status": selected["status"],
        "cpu": selected,
        "ir_sha256": plan.soc_ir_sha256,
        "base_io_ir_sha256": base["ir_sha256"],
        "board": base["board"],
        "peripheral_bindings": [b for b in base["bindings"] if b["instance"] != "rv64"],
        "workloads": config["workloads"],
        "requested_configuration": config,
        "execution_plan": plan.to_dict(),
        "ownership": {
            "instructions": "systemc-verilator-cpu",
            "l1_caches": "RocketTile",
            "backing_memory": config["engine"],
            "native_engine_cpu": "halted-irq-sink",
            "external_dma_coherence": False,
        },
    }


def run_platform(project, config, generated, models, bridge, cpu_build, out):
    from .cpu_platform import platform_plan as compile_plan

    if cpu_build is None:
        raise ContractError("Rocket requires a real --rtl-cpu-build; no ISS substitution")
    selected = selection(config, config["engine"])
    build = verify_build(cpu_build.resolve())
    target_ir(project, build)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "plan.json", compile_plan(project, config))
    write_json(out / "soc-ir.json", target_ir(project, build).to_dict())
    results = {}
    for workload in config["workloads"]:
        artifact = config.get("artifacts", {}).get(workload)
        elf = None
        markers = None
        if artifact:
            elf = Path(artifact["elf"])
            if "dtb" in artifact or digest(elf) != artifact["sha256"]:
                raise ContractError("modified or incompatible Rocket external software")
            markers = artifact["expected_markers"]
        if workload == "freertos":
            from .rocket_software import verify_software

            software = Path(config["software_builds"][workload])
            sw = verify_software(project, cpu_build, software)
            if sw["kind"] != workload or sw["case"] != "smoke":
                raise ContractError("Rocket platform requires positive matching kernel acceptance")
            elf, markers = software / "firmware.elf", sw["expected_markers"]
        results[workload] = run(
            project,
            cpu_build,
            generated,
            models,
            config["engine"],
            out / workload,
            bridge=bridge,
            elf=elf,
            markers=markers,
            clock_hz=selected["clock_hz"],
            max_cycles=10000000 if workload == "freertos" else 1000000,
        )
    report = {
        "schema_version": "1",
        "profile": "rocket-cpu-platform-v1",
        "status": "PASSED" if all(r["status"] == "PASSED" for r in results.values()) else "FAILED",
        "cpu": selected,
        "project_root": str(project.root),
        "cpu_build_root": str(cpu_build.resolve()),
        "results": {
            k: {n: r[n] for n in ("status", "checks", "performance")} for k, r in results.items()
        },
        "full_architecture_complete": False,
        "files": {
            p.relative_to(out).as_posix(): digest(p) for p in sorted(out.rglob("*")) if p.is_file()
        },
    }
    write_json(out / "report.json", report)
    return report


def verify_platform(out):
    from .cpu_platform import platform_plan as compile_plan
    from .project import inside, load_project

    report = json.loads((out / "report.json").read_text())
    project = load_project(Path(report["project_root"]))
    cpu_build = Path(report["cpu_build_root"])
    plan = json.loads((out / "plan.json").read_text())
    if (
        report["profile"] != "rocket-cpu-platform-v1"
        or report["full_architecture_complete"] is not False
        or plan != compile_plan(project, plan["requested_configuration"])
        or report["cpu"] != plan["cpu"]
        or set(report["results"]) != set(plan["workloads"])
    ):
        raise ContractError("mixed Rocket CPU/platform plan identity")
    for name, sha in report["files"].items():
        if digest(inside(out, name)) != sha:
            raise ContractError("modified Rocket platform evidence")
    for workload, result in report["results"].items():
        verify_run(project, cpu_build, out / workload)
        row = json.loads((out / workload / "report.json").read_text())
        if result != {n: row[n] for n in ("status", "checks", "performance")}:
            raise ContractError("mixed Rocket software-stage evidence")
    if report["status"] != "PASSED":
        raise ContractError("Rocket platform lacks complete successful execution evidence")
    return report


def main(argv):
    from .project import load_project
    from .systems import load_system

    parser = argparse.ArgumentParser(prog="usp rocket")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "run", "verify"):
        p = sub.add_parser(name)
        p.add_argument("project", type=Path)
        if name == "build":
            for option in ("out", "upstream", "tools", "java-home", "scala-cache"):
                p.add_argument("--" + option, type=Path, required=True)
            p.add_argument("--jobs", type=int, default=2)
        else:
            p.add_argument("--cpu-build", type=Path, required=True)
            p.add_argument("--out", type=Path, required=True)
        if name == "run":
            for option in ("generated", "models", "bridge", "elf"):
                p.add_argument("--" + option, type=Path, required=option in {"generated", "models"})
            p.add_argument("--engine", choices=("qemu", "gem5"), required=True)
            p.add_argument("--marker", action="append")
            p.add_argument("--clock-hz", type=int, default=50000000)
            p.add_argument("--quantum", type=int, default=16)
            p.add_argument("--max-cycles", type=int, default=1000000)
            p.add_argument("--timeout", type=float, default=120)
            p.add_argument("--axi-latency", type=int, default=0)
            p.add_argument("--inject-bus-error-address", type=lambda s: int(s, 0))
    args = parser.parse_args(argv)
    try:
        root = args.project.resolve()
        project = load_system(root)[2] if (root / "system.yaml").exists() else load_project(root)
        if args.command == "build":
            from .rocket_build import elaborate

            ram, regions = layout(project)
            record = elaborate(
                args.out,
                args.upstream,
                args.tools,
                args.java_home,
                args.scala_cache,
                ram["base"],
                ram["size"],
                regions,
                args.jobs,
            )
            report = {
                "status": "PASSED",
                "build_contract": record["contract"],
                "model": record["model"],
                "out": str(args.out.resolve()),
            }
        elif args.command == "verify":
            report = verify_run(project, args.cpu_build.resolve(), args.out.resolve())
        else:
            report = run(
                project,
                args.cpu_build,
                args.generated,
                args.models,
                args.engine,
                args.out,
                args.bridge,
                args.elf,
                args.marker,
                args.clock_hz,
                args.quantum,
                args.max_cycles,
                args.timeout,
                args.axi_latency,
                args.inject_bus_error_address,
            )
        print(json.dumps(report, indent=2))
        return 0 if report["status"] == "PASSED" else 1
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}))
        return 2
