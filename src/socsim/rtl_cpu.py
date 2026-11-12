"""Actual RTL instructions, with QEMU/gem5 owning physical memory and devices."""

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from string import Template

from .domain import ContractError, CpuContract
from .execution import cleanup_on_termination
from .gem5 import python_environment
from .ipblocks import verify_models
from .ipxact import digest
from .pipeline import execute, identity, verify_generated
from .project import inside
from .rtl_cpu_build import (
    CORE,
    RESOURCES,
    build_cpu,
    build_engine,
    load_manifest,
    verify_cpu,
    verify_engine,
)
from .runner import tool_version, write_json
from .unified import board_contract, trace_checks

MARKERS = [
    "USP_BAREMETAL_BOOT_OK",
    "USP_BAREMETAL_MEMORY_OK",
    "USP_RTL_BUS_FAULT_OK",
    "USP_TIMER_IRQ_OK",
    "USP_CHIPYARD_GCD_IRQ_OK",
    "USP_OPENTITAN_GPIO_IRQ_OK",
    "USP_TEST_PASS",
]
ZERO_COUNTERS = {
    "system.cpu.numCycles",
    "system.cpu.executeStats0.numInsts",
    "system.cpu.fetchStats0.numInsts",
    "system.cpu.thread_0.numInsts",
}


def target_ir(project, manifest):
    b = board_contract(project)
    m = load_manifest(manifest)
    if b["ram_base"] % 256 or any(
        w.base + w.size > 1 << 32 for i in project.ir.instances for w in i.windows
    ):
        raise ContractError("RTL CPU requires 32-bit physical map and 256-byte boot alignment")
    cpu = CpuContract(
        "rv32imc_zicsr_zifencei", "M", "none", b["ram_base"] + 128, b["ram_base"], b["timebase_hz"]
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
                vlnv="socsim.cpu:" + m["kind"] + ":" + m["model"] + ":1",
            )
            if i.name == "rv64"
            else i
            for i in project.ir.instances
        ),
        interrupts=tuple(
            replace(i, target="cpu") if i.target == "rv64" else i
            for i in project.ir.interrupts
            if not (i.target == "rv64" and i.line == 9)
        ),
        address_spaces=tuple(replace(s, initiator="cpu") for s in project.ir.address_spaces),
        sources=tuple(sorted((*project.ir.sources, ("rtl-cpu-manifest", digest(manifest))))),
    )


def build_firmware(project, out):
    b = board_contract(project)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    for name in ("start.S", "firmware.c.in", "firmware.ld.in"):
        s = (RESOURCES / name).read_text()
        if name.endswith(".in"):
            s = Template(s).substitute(
                origin=b["ram_base"],
                length=b["ram_size"],
                gcd_irq=b["cygcd_irq"],
                gpio_irq=b["otgpio_irq"],
                timer_ticks=100,
            )
        (out / name.removesuffix(".in")).write_text(s)
    (out / "soc.h").write_text(
        "#include <stdint.h>\n"
        + "".join(
            f"#define USP_{n.upper()}_BASE 0x{b[n + '_base']:x}U\n"
            for n in ("uart", "clint", "plic", "finisher", "cygcd", "otgpio")
        )
    )
    common = [
        "riscv64-unknown-elf-gcc",
        "-march=rv32imc_zicsr_zifencei",
        "-mabi=ilp32",
        "-nostdlib",
        "-nostartfiles",
        "-O2",
        "-ffreestanding",
        "-fno-builtin",
        "-mno-relax",
        "-msmall-data-limit=0",
        "-Wl,--build-id=none",
    ]
    # Named objects remove GCC's random temporary FILE symbols from the ELF.
    for name in ("start.S", "firmware.c"):
        execute(
            common + ["-c", str(out / name), "-o", str(out / (name + ".o"))],
            out,
            out / (name + ".log"),
            60,
        )
    execute(
        common
        + [
            "-T",
            str(out / "firmware.ld"),
            "-o",
            str(out / "firmware.elf"),
            str(out / "start.S.o"),
            str(out / "firmware.c.o"),
            "-lgcc",
        ],
        out,
        out / "build.log",
        60,
    )
    write_json(
        out / "build.json",
        {
            "ir_sha256": identity(project),
            "elf_sha256": digest(out / "firmware.elf"),
            "sources": {
                n: digest(RESOURCES / n) for n in ("start.S", "firmware.c.in", "firmware.ld.in")
            },
            "compiler": tool_version("riscv64-unknown-elf-gcc")[1],
        },
    )
    return out / "firmware.elf"


def validate_elf(project, elf):
    from elftools.common.exceptions import ELFError
    from elftools.elf.elffile import ELFFile

    b = board_contract(project)
    try:
        with elf.open("rb") as f:
            e = ELFFile(f)
            if (
                e.elfclass != 32
                or not e.little_endian
                or e["e_machine"] != "EM_RISCV"
                or e["e_type"] != "ET_EXEC"
                or e["e_flags"] & ~1
                or e["e_entry"] != b["ram_base"]
            ):
                raise ContractError(
                    "RTL adapter requires RV32IMC M-mode ELF entry RAM, reset RAM+0x80; no F/D/MMU"
                )
            loads = [s for s in e.iter_segments() if s["p_type"] == "PT_LOAD" and s["p_memsz"]]
            if not loads:
                raise ContractError("empty CPU firmware")
            for s in loads:
                if (
                    s["p_paddr"] != s["p_vaddr"]
                    or s["p_filesz"] > s["p_memsz"]
                    or s["p_paddr"] < b["ram_base"]
                    or s["p_paddr"] + s["p_memsz"] > b["ram_base"] + b["ram_size"]
                ):
                    raise ContractError("RTL firmware load escapes RAM")
    except ELFError as exc:
        raise ContractError("invalid RTL CPU ELF container") from exc


def rtl_metrics(path):
    r = json.loads(path.read_text())
    n = r["rvfi_events"] - r["rvfi_traps"]
    if r["cycles"] <= 0 or n <= 0:
        raise ContractError("no actual RTL instruction retirement")
    return {
        "kind": "native-rtl-counters",
        "source": "Verilator clock edges + RVFI",
        "cycles": r["cycles"],
        "instructions": n,
        "rvfi_events": r["rvfi_events"],
        "traps": r["rvfi_traps"],
        "cpi": r["cycles"] / n,
        "ipc": n / r["cycles"],
        "calibrated_to_silicon": False,
        "interval": "reset to final-store drain; one-cycle bus responses; bounded time quantum",
    }


def engine_stats(out):
    stats = {}
    if (out / "stats.txt").exists():
        for line in (out / "stats.txt").read_text().splitlines():
            f = line.split()
            if len(f) > 1 and f[0] in ZERO_COUNTERS | {"simTicks"}:
                stats[f[0]] = float(f[1])
    return stats


def read_rows(path):
    return [json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []


def acceptance(out, engine, driver_rc, engine_rc, timed_out, markers, builtin, command):
    from .execution_plan import validate_bridge_trace

    p = out / "rtl-counters.json"
    c = json.loads(p.read_text()) if p.exists() else {}
    uart = (out / "uart.log").read_text(errors="replace")
    stats = engine_stats(out)
    checks = {
        "no_timeout": not timed_out,
        "rtl_driver_success": driver_rc == 0,
        "engine_success": engine_rc == 0,
        "actual_rvfi": c.get("rvfi_events", 0) > 0,
        "finisher": c.get("finished") == 1 and c.get("signature") == 0x5555,
        "uart_markers": bool(markers) and all(m in uart for m in markers),
        "no_iss_execution": all(stats.get(n) == 0 for n in ZERO_COUNTERS)
        if engine == "gem5"
        else "-accel" in command and command[command.index("-accel") + 1] == "qtest",
    }
    if builtin:
        rows = read_rows(out / "ip-trace.jsonl")
        checks.update(trace_checks(rows))
        checks.update(validate_bridge_trace(rows))
        bus = read_rows(out / "bus-trace.jsonl")
        checks["actual_bus_fault"] = any(t["error"] for t in bus)
        checks["byte_halfword_writes"] = all(
            any(t["write"] and t["width"] == n for t in bus) for n in (1, 2)
        )
    return checks


@cleanup_on_termination
def run_cpu(
    project,
    manifest,
    cpu_build,
    generated,
    models,
    engine,
    out,
    bridge=None,
    elf=None,
    markers=None,
    max_cycles=200000,
    clock_hz=50000000,
    quantum=16,
    timeout=60,
):
    if (
        engine not in {"qemu", "gem5"}
        or not 16 <= max_cycles <= 10000000
        or not 1 <= timeout <= 300
    ):
        raise ContractError("invalid RTL run bounds")
    if type(clock_hz) is not int or not 100000 <= clock_hz <= 500000000 or 1000000000 % clock_hz:
        raise ContractError("RTL clock requires an integral 2..10000ns period")
    if type(quantum) is not int or not 1 <= quantum <= 64:
        raise ContractError("invalid time quantum")
    cpu_build, generated, models, out = (p.resolve() for p in (cpu_build, generated, models, out))
    b = board_contract(project)
    ir = target_ir(project, manifest)
    verify_generated(project, generated)
    cpu_record = verify_cpu(manifest, cpu_build)
    model_record = verify_models(models)
    if engine == "qemu":
        binary = generated / "qemu-build/qemu-system-riscv64"
        engine_record = json.loads((generated / "qemu-build.json").read_text())
        if engine_record["binary_sha256"] != digest(binary) or engine_record[
            "generated_manifest_sha256"
        ] != digest(generated / "manifest.json"):
            raise ContractError("modified/stale QEMU memory engine")
    else:
        if bridge is None:
            raise ContractError("gem5 RTL bus-master build is required")
        bridge = bridge.resolve()
        engine_record = verify_engine(bridge)
        binary = bridge / "gem5.opt"
    out.mkdir(parents=True, exist_ok=False)
    if elf is None:
        elf = build_firmware(project, out / "firmware")
        markers = MARKERS
    else:
        elf = elf.resolve()
    validate_elf(project, elf)
    shutil.copy2(elf, out / "firmware.elf")
    write_json(out / "soc-ir.json", ir.to_dict())
    (out / "ranges.txt").write_text(
        "".join(f"{w.base} {w.size}\n" for i in ir.instances for w in i.windows)
    )
    (out / "uart.log").touch()
    commands = []
    processes = []
    logs = []
    timed_out = False
    driver = backend = None
    startup_error = None

    def start(command, log, env=None):
        commands.append(command)
        stream = (out / log).open("wb")
        logs.append(stream)
        p = subprocess.Popen(
            command, stdout=stream, stderr=subprocess.STDOUT, env=env, start_new_session=True
        )
        processes.append(p)
        return p

    def wait_socket(path, proc):
        end = time.monotonic() + 10
        while not path.exists():
            if proc.poll() is not None or time.monotonic() > end:
                raise ContractError("engine/model did not open its socket")
            time.sleep(0.01)

    with tempfile.TemporaryDirectory(prefix="usp-rtl-") as temp:
        directory = Path(temp)
        ip = directory / "ip.sock"
        bus = directory / "cpu.sock"
        try:
            model = start(
                [str(models / "rtl/Vusp_ipblocks"), str(ip), str(out / "ip-trace.jsonl")],
                "model.log",
            )
            wait_socket(ip, model)
            if engine == "qemu":
                env = os.environ.copy()
                env["USP_BRIDGE_SOCKET"] = str(ip)
                command = [
                    str(binary),
                    "-machine",
                    "usp-rv64",
                    "-cpu",
                    "rv32",
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
                b.update(
                    ram_image=str(out / "firmware.elf"),
                    rtl_socket=str(bus),
                    cpu_model="RiscvAtomicSimpleCPU",
                )
                write_json(out / "board.json", b)
                shutil.copy2(RESOURCES / "board.py.in", out / "board.py")
                command = [
                    str(binary),
                    "--outdir=" + str(out),
                    str(out / "board.py"),
                    str(out / "board.json"),
                    str(ip),
                    str(out / "firmware.elf"),
                    str(out / "exit.json"),
                ]
                env = python_environment()
            backend = start(command, "engine.log", env)
            wait_socket(bus, backend)
            driver = start(
                [
                    str(cpu_build / "sim/Vusp_rtl_cpu"),
                    engine,
                    str(bus),
                    str(out / "bus-trace.jsonl"),
                    str(out / "rtl-counters.json"),
                    str(b["ram_base"]),
                    str(max_cycles),
                    str(1000000000 // clock_hz),
                    str(b["finisher_base"]),
                    str(out / "ranges.txt"),
                    str(quantum),
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
        except ContractError as exc:
            startup_error = str(exc)
        finally:
            for p in reversed(processes):
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGTERM)
                    try:
                        p.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        os.killpg(p.pid, signal.SIGKILL)
                        p.wait()
            for f in logs:
                f.close()
    if engine == "gem5":
        terminals = list(out.glob("*.terminal"))
        if terminals:
            (out / "uart.log").write_bytes(terminals[0].read_bytes())
    cpath = out / "rtl-counters.json"
    c = json.loads(cpath.read_text()) if cpath.exists() else {}
    driver_rc = driver.returncode if driver else None
    engine_rc = backend.returncode if backend else None
    builtin = markers == MARKERS
    checks = acceptance(
        out,
        engine,
        driver_rc,
        engine_rc,
        timed_out,
        markers,
        builtin,
        commands[1] if len(commands) > 1 else [],
    )
    r = {
        "schema_version": "1",
        "status": "PASSED" if all(checks.values()) else "FAILED",
        "engine": engine,
        "cpu_provider": load_manifest(manifest)["kind"],
        "cpu_model": cpu_record["model"],
        "checks": checks,
        "performance": rtl_metrics(cpath) if c.get("rvfi_events", 0) > 0 else None,
        "engine_statistics": engine_stats(out) if engine == "gem5" else {},
        "expected_markers": markers,
        "builtin_acceptance": builtin,
        "timed_out": timed_out,
        "startup_error": startup_error,
        "driver_returncode": driver_rc,
        "engine_returncode": engine_rc,
        "clock_hz": clock_hz,
        "quantum_cycles": quantum,
        "max_cycles": max_cycles,
        "elf_sha256": digest(out / "firmware.elf"),
        "base_io_ir_sha256": identity(project),
        "cpu_build": cpu_record,
        "model_build": model_record,
        "engine_build": engine_record,
        "commands": commands,
        "returncodes": [p.returncode for p in processes],
        "limitations": [
            "one RV32IMC M-mode hart; no MMU/Linux",
            "atomic physical bus; fixed one-cycle RTL responses",
            "bounded IRQ sampling; transaction-driven peripheral RTL time; no timing correlation",
            "engine CPU is passive IRQ sink; no ISS fallback",
            "no DMA/coherence/checkpoint",
        ],
        "files": {
            p.relative_to(out).as_posix(): digest(p) for p in sorted(out.rglob("*")) if p.is_file()
        },
    }
    write_json(out / "report.json", r)
    (out / "report.md").write_text(
        f"# RTL CPU: {r['status']}\n\n{r['cpu_model']} on {engine}; actual RTL instructions.\n\n"
        + "\n".join(f"- {k}: {v}" for k, v in checks.items())
        + "\n\n"
        + "\n".join(r["limitations"])
        + "\n"
    )
    return r


def platform_plan(project, config, selected, base):
    from .execution_plan import (
        AddressRoute,
        ExecutionPlan,
        InterruptRoute,
        ModelBinding,
        TimeDomain,
    )

    ir = target_ir(project, Path(selected["rtl_manifest"]))
    engine = config["engine"]
    bindings = tuple(
        ModelBinding(
            i.name,
            i.vlnv,
            "systemc-verilator-cpu",
            selected["adapter"],
            CORE if selected["provider"] == "chipyard" else None,
        )
        if i.name == "cpu"
        else ModelBinding(i.name, i.vlnv, b["owner"], b["implementation"], b["source_commit"])
        for i in ir.instances
        for b in base["bindings"]
        if b["instance"] == ("rv64" if i.name == "cpu" else i.name)
    )
    plan = ExecutionPlan(
        "1",
        selected["status"],
        hashlib.sha256(json.dumps(ir.to_dict(), sort_keys=True).encode()).hexdigest(),
        hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        selected["provider"],
        selected["model"],
        engine,
        bindings,
        tuple(
            AddressRoute(
                "cpu",
                i.name,
                w.base,
                w.size,
                "systemc-verilator" if i.name in {"cygcd", "otgpio"} else engine,
                "obi32-to-tlm-to-" + ("qtest" if engine == "qemu" else "gem5-atomic"),
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
                "one edge per CPU cycle",
            ),
            TimeDomain(
                engine, "memory-peripherals-only", None, "explicit bounded quantum advancement"
            ),
            TimeDomain(
                "systemc-verilator", "annotated-peripheral-rtl", 100000000, "per-MMIO transaction"
            ),
        ),
        synchronization="rtl-physical-master-v1",
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
            "memory": engine,
            "native_engine_cpu": "halted-irq-sink",
        },
    }


def run_platform(project, config, generated, models, bridge, cpu_build, out):
    from .cpu_platform import cpu_selection
    from .cpu_platform import platform_plan as compile_plan

    if cpu_build is None:
        raise ContractError("real --rtl-cpu-build required; no ISS substitution")
    selection = cpu_selection(config, config["engine"])
    manifest = Path(selection["rtl_manifest"])
    artifact = config.get("artifacts", {}).get("baremetal")
    if artifact:
        if (
            "dtb" in artifact
            or not artifact["expected_markers"]
            or digest(Path(artifact["elf"])) != artifact["sha256"]
        ):
            raise ContractError("invalid RV32 external firmware artifact identity/contract")
        validate_elf(project, Path(artifact["elf"]))
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "plan.json", compile_plan(project, config))
    write_json(out / "soc-ir.json", target_ir(project, manifest).to_dict())
    row = run_cpu(
        project,
        manifest,
        cpu_build,
        generated,
        models,
        config["engine"],
        out / "baremetal",
        bridge,
        elf=Path(artifact["elf"]) if artifact else None,
        markers=artifact["expected_markers"] if artifact else None,
        clock_hz=selection["clock_hz"],
    )
    r = {
        "schema_version": "1",
        "profile": "rtl-cpu-platform-v1",
        "status": row["status"],
        "cpu": selection,
        "results": {"baremetal": {k: row[k] for k in ("status", "checks", "performance")}},
        "full_architecture_complete": False,
        "files": {
            p.relative_to(out).as_posix(): digest(p) for p in sorted(out.rglob("*")) if p.is_file()
        },
    }
    write_json(out / "report.json", r)
    return r


def verify_run(out):
    r = json.loads((out / "report.json").read_text())
    for n, sha in r["files"].items():
        if digest(inside(out, n)) != sha:
            raise ContractError("modified RTL CPU run evidence")
    cpath = out / "rtl-counters.json"
    c = json.loads(cpath.read_text()) if cpath.exists() else {}
    if r["performance"] != (rtl_metrics(cpath) if c.get("rvfi_events", 0) > 0 else None):
        raise ContractError("invalid RTL CPI/IPC evidence")
    rvfi = read_rows(out / "rtl-counters.json.rvfi.jsonl")
    if len(rvfi) != c.get("rvfi_events", 0) or sum(e["trap"] for e in rvfi) != c.get(
        "rvfi_traps", 0
    ):
        raise ContractError("RVFI retirement evidence mismatch")
    if any(e["cycle"] >= c["cycles"] for e in rvfi):
        raise ContractError("invalid RVFI cycle interval")
    if r["engine"] == "gem5" and r["engine_statistics"] != engine_stats(out):
        raise ContractError("modified native engine statistics")
    if r["elf_sha256"] != digest(out / "firmware.elf") or r["checks"] != acceptance(
        out,
        r["engine"],
        r["driver_returncode"],
        r["engine_returncode"],
        r["timed_out"],
        r["expected_markers"],
        r["builtin_acceptance"],
        r["commands"][1] if len(r["commands"]) > 1 else [],
    ):
        raise ContractError("invalid RTL acceptance evidence")
    if r["status"] != ("PASSED" if all(r["checks"].values()) else "FAILED"):
        raise ContractError("invalid RTL CPU acceptance status")
    return r


def verify_platform(out):
    r = json.loads((out / "report.json").read_text())
    plan = json.loads((out / "plan.json").read_text())
    for n, sha in r["files"].items():
        if digest(inside(out, n)) != sha:
            raise ContractError("modified RTL platform evidence")
    ir = json.loads((out / "soc-ir.json").read_text())
    if (
        r["full_architecture_complete"] is not False
        or r["cpu"] != plan["cpu"]
        or plan["ir_sha256"] != hashlib.sha256(json.dumps(ir, sort_keys=True).encode()).hexdigest()
        or plan["ownership"]["instructions"] != "systemc-verilator-cpu"
    ):
        raise ContractError("invalid RTL execution ownership/IR")
    row = verify_run(out / "baremetal")
    if (
        r["results"] != {"baremetal": {k: row[k] for k in ("status", "checks", "performance")}}
        or r["status"] != row["status"]
    ):
        raise ContractError("mixed RTL platform results")
    return r


def main(argv):
    parser = argparse.ArgumentParser(prog="usp rtlcpu")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build")
    for n in ("manifest", "out", "chipyard", "wrapper", "core"):
        p.add_argument("--" + n, type=Path, required=True)
    p.add_argument("--jobs", type=int, default=2)
    p = sub.add_parser("build-engine")
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--jobs", type=int, default=2)
    sub.add_parser("verify").add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            r = build_cpu(
                args.manifest, args.out, args.chipyard, args.wrapper, args.core, args.jobs
            )
        elif args.command == "build-engine":
            r = build_engine(args.source, args.out, args.jobs)
        else:
            r = verify_run(args.run_dir.resolve())
        print(json.dumps(r, indent=2))
        return 0 if r.get("status", "PASSED") == "PASSED" else 1
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}))
        return 2
