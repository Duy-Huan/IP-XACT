"""Live Ruby/Garnet cache-memory traffic in the unified SystemC SoC."""

import argparse
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

from .domain import ContractError
from .gem5 import GEM5_SHA
from .ipxact import digest
from .pipeline import verify_source
from .project import inside, load_project
from .runner import write_json
from .unified import MARKERS, RESOURCES, run_system, trace_checks


def prepare(source, out, contract):
    source = verify_source(source, GEM5_SHA)
    shutil.copytree(
        source / "configs", out / "gem5-configs", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copy2(source / "LICENSE", out / "gem5-LICENSE")
    contract["gem5_config_dir"] = str(out / "gem5-configs")
    write_json(
        out / "fabric-source.json",
        dict(
            source_commit=GEM5_SHA,
            config_inputs={
                p.relative_to(out).as_posix(): digest(p)
                for p in (out / "gem5-configs").rglob("*")
                if p.is_file()
            },
        ),
    )


def inspect_fabric(out):
    config = json.loads((out / "config.json").read_text())["system"]
    ruby = config.get("ruby", {})
    cpu = config["cpu"][0]
    network = ruby.get("network", {})
    stats = {}
    wanted = {
        "simInsts",
        "system.cpu.numCycles",
        "system.cpu.commitStats0.cpi",
        "system.cpu.commitStats0.ipc",
        "system.cpu.exec_context.thread_0.numIdleCycles",
        *(
            "system.ruby.network." + s
            for s in (
                "packets_injected::total",
                "packets_received::total",
                "flits_injected::total",
                "flits_received::total",
                "average_packet_network_latency",
                "average_hops",
            )
        ),
    }
    for line in (out / "stats.txt").read_text().splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] in wanted:
            try:
                stats[fields[0]] = float(fields[1])
            except ValueError:
                pass
    prefix = "system.ruby.network."
    packets = stats.get(prefix + "packets_received::total", 0)
    flits = stats.get(prefix + "flits_received::total", 0)
    sequencer = "system.ruby.l1_cntrl0.sequencer.in_ports"
    controllers = config.get("mem_ctrls", [])
    checks = dict(
        native_timing_cpu=cpu["type"] == "BaseTimingSimpleCPU"
        and cpu["isa"][0]["type"] == "RiscvISA"
        and cpu["isa"][0]["riscv_type"] == "RV64",
        native_cpu_execution=stats.get("simInsts", 0) > 0
        and stats.get("system.cpu.numCycles", 0) > 0,
        actual_garnet=network.get("type") == "GarnetNetwork"
        and len(network.get("routers", [])) == 4,
        instruction_path=cpu["icache_port"]["peer"] == sequencer + "[0]",
        data_path=cpu["dcache_port"]["peer"] == sequencer + "[1]",
        single_ram_owner="mem" not in config
        and len(controllers) == 1
        and controllers[0]["port"]["peer"] == "system.ruby.dir_cntrl0.memory_out_port",
        boot_rom_directory="memory_out_port" in config["rom"]["port"]["peer"],
        real_packets=0 < packets <= stats.get(prefix + "packets_injected::total", 0),
        real_flits=0 < flits <= stats.get(prefix + "flits_injected::total", 0),
        inter_router_hops=stats.get(prefix + "average_hops", 0) > 0,
        finite_statistics=bool(stats) and all(math.isfinite(v) for v in stats.values()),
    )
    return dict(
        checks=checks,
        statistics=stats,
        topology="Pt2Pt",
        routers=4,
        protocol="MI_example",
        scope="one hart, one RAM directory, one ROM directory, one I/O controller",
        performance_scope=(
            "native gem5 counters for the whole boot, including WFI idle; uncalibrated"
        ),
    )


def run(project, generated, models, bridge, source, out):
    out = out.resolve()
    row = run_system(project, generated, models, bridge, out, timeout=120, garnet_source=source)
    shutil.copy2(generated / "zephyr-build/zephyr/zephyr.elf", out / "firmware.elf")
    write_json(out / "soc-ir.json", project.ir.to_dict())
    bindings = json.loads((out / "engine-bindings.json").read_text())
    bindings["memory_path"] = (
        "CPU -> RubySequencer -> MI_example L1 -> Garnet -> directory -> memory"
    )
    bindings["pio_path"] = "CPU -> RubySequencer PIO -> native bus -> native or SystemC peripheral"
    bindings["fabric"] = row["fabric"]
    write_json(out / "engine-bindings.json", bindings)
    result = dict(
        profile="unified-live-garnet-v1",
        status=row["status"],
        adapter_sha256=digest(Path(__file__)),
        files={p.relative_to(out).as_posix(): digest(p) for p in out.rglob("*") if p.is_file()},
    )
    write_json(out / "fabric-report.json", result)
    verify(out)
    return result


def verify(out):
    r = json.loads((out / "fabric-report.json").read_text())
    if r["profile"] != "unified-live-garnet-v1" or r["adapter_sha256"] != digest(Path(__file__)):
        raise ContractError("stale live Garnet adapter")
    required = {
        "config.json",
        "stats.txt",
        "uart.log",
        "board.py",
        "board.json",
        "report.json",
        "bridge-trace.jsonl",
        "exit.json",
        "fabric-source.json",
        "gem5-LICENSE",
        "firmware.elf",
        "soc-ir.json",
        "engine-bindings.json",
    }
    if not required.issubset(r["files"]):
        raise ContractError("incomplete live Garnet evidence")
    for name, sha in r["files"].items():
        if digest(inside(out, name)) != sha:
            raise ContractError("modified live Garnet artifact")
    source = json.loads((out / "fabric-source.json").read_text())
    if source["source_commit"] != GEM5_SHA:
        raise ContractError("live Garnet source pin mismatch")
    for name, sha in source["config_inputs"].items():
        if name not in r["files"] or digest(inside(out, name)) != sha:
            raise ContractError("modified Ruby configuration source")
    if digest(out / "board.py") != digest(RESOURCES / "board-garnet.py.in"):
        raise ContractError("modified live Garnet topology board")
    row = json.loads((out / "report.json").read_text())
    ir_sha = hashlib.sha256(
        json.dumps(json.loads((out / "soc-ir.json").read_text()), sort_keys=True).encode()
    ).hexdigest()
    if row["ir_sha256"] != ir_sha or row["elf_sha256"] != digest(out / "firmware.elf"):
        raise ContractError("live Garnet firmware or IR provenance mismatch")
    actual = inspect_fabric(out)
    trace = [json.loads(s) for s in (out / "bridge-trace.jsonl").read_text().splitlines()]
    serial = (out / "uart.log").read_text()
    exit_info = json.loads((out / "exit.json").read_text())
    passed = (
        all(actual["checks"].values())
        and all(trace_checks(trace).values())
        and all(serial.count(s) == 1 for s in MARKERS)
        and exit_info["cause"] == "USP guest finisher"
        and exit_info["code"] == 0
        and row["returncode"] == 0
        and not row["timed_out"]
        and row["model_process"]["returncode"] == 0
    )
    expected = "PASSED" if passed else "FAILED"
    if row["fabric"] != actual or row["status"] != expected or r["status"] != expected:
        raise ContractError("modified live Garnet acceptance")
    return r


def main(argv=None):
    parser = argparse.ArgumentParser(prog="usp fabric")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run")
    p.add_argument("project", type=Path)
    for n in ("generated", "models", "bridge", "source", "out"):
        p.add_argument("--" + n, type=Path, required=True)
    sub.add_parser("verify").add_argument("out", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            r = run(
                load_project(args.project),
                args.generated,
                args.models,
                args.bridge,
                args.source,
                args.out,
            )
        else:
            r = verify(args.out.resolve())
        print(json.dumps(r, indent=2))
        return 0 if r["status"] == "PASSED" else 1
    except (ContractError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(status="ERROR", message=str(exc))))
        return 2
