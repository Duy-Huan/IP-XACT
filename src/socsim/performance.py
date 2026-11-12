"""Native gem5 statistics and explicitly estimated QEMU instruction-cost metrics."""

import json
import math
import os
import shlex
import subprocess
import sys
from pathlib import Path

from .domain import ContractError
from .ipxact import digest
from .pipeline import QEMU_SHA, build_qemu, execute, verify_generated, verify_source
from .runner import tool_version, write_json

RESOURCE = Path(__file__).parent / "resources/performance/qemu_counter.c"
DEFAULT_COST = {"base_cpi": 1.0, "memory_extra_cycles": 1.0}


def enable_qemu_counter(project, generated, source, jobs=2):
    """Explicit QEMU rebuild; refresh its actual normal build identity."""
    verify_generated(project, generated)
    verify_source(source, QEMU_SHA)
    derived, build = generated / "qemu-source", generated / "qemu-build"
    if digest(derived / "hw/riscv/usp_machine.c") != digest(generated / "machine.c"):
        raise ContractError("profiled QEMU requires matching generated machine source")
    argv = [
        str(derived / "configure"),
        "--target-list=riscv64-softmmu",
        "--without-default-features",
        "--without-default-devices",
        "--disable-werror",
        "--enable-fdt",
        "--enable-plugins",
        "--python=" + sys.executable,
    ]
    prefix = os.environ.get("USP_HOST_PREFIX")
    if prefix:
        argv += [
            "--extra-cflags=-I" + prefix + "/usr/include",
            "--extra-ldflags=-L" + prefix + "/usr/lib/x86_64-linux-gnu",
        ]
    execute(argv, build, generated / "logs/qemu-enable-plugins.log", 120)
    return build_qemu(project, generated, source, jobs)


def build_counter(source, out):
    source = verify_source(source, QEMU_SHA)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    cc, version = tool_version("cc")
    flags = shlex.split(subprocess.check_output(["pkg-config", "--cflags", "glib-2.0"], text=True))
    execute(
        [
            cc,
            "-shared",
            "-fPIC",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Wno-unused-parameter",
            "-Werror",
            "-I" + str(source / "include/qemu"),
            *flags,
            str(RESOURCE),
            "-o",
            str(out / "counter.so"),
        ],
        out,
        out / "build.log",
        60,
    )
    r = {
        "source_commit": QEMU_SHA,
        "source_sha256": digest(RESOURCE),
        "header_sha256": digest(source / "include/qemu/qemu-plugin.h"),
        "binary_sha256": digest(out / "counter.so"),
        "compiler": version,
    }
    write_json(out / "counter-build.json", r)
    return r


def verify_counter(out):
    r = json.loads((out / "counter-build.json").read_text())
    if (
        r["source_commit"] != QEMU_SHA
        or r["source_sha256"] != digest(RESOURCE)
        or r["binary_sha256"] != digest(out / "counter.so")
    ):
        raise ContractError("stale or modified QEMU performance counter")
    return r


def qemu_metrics(counters, cost=None):
    cost = DEFAULT_COST.copy() if cost is None else cost
    if (
        not isinstance(cost, dict)
        or set(cost) != set(DEFAULT_COST)
        or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in cost.values())
        or cost["base_cpi"] <= 0
    ):
        raise ContractError("invalid explicit QEMU instruction cost model")
    keys = {"executed_instructions", "data_accesses"}
    if (
        not isinstance(counters, dict)
        or set(counters) != keys
        or any(type(v) is not int or v < 0 for v in counters.values())
        or counters["executed_instructions"] <= 0
    ):
        raise ContractError("invalid actual QEMU event counters")
    insns = counters["executed_instructions"]
    estimated = insns * cost["base_cpi"] + counters["data_accesses"] * cost["memory_extra_cycles"]
    if not math.isfinite(estimated) or estimated <= 0:
        raise ContractError("QEMU analytical cycle estimate overflow")
    return {
        "engine": "qemu",
        "measurement": "executed-event-counts",
        "counters": counters,
        "cost_model": cost,
        "estimated_cycles": estimated,
        "estimated_cpi": estimated / insns,
        "estimated_ipc": insns / estimated,
        "native_cpu_cycles": None,
        "native_cpi": None,
        "native_ipc": None,
        "scope": "whole guest execution; instruction attempts, not retirement",
        "fidelity": "uncalibrated uniform-memory estimate; no idle/cache/IO timing or overlap",
    }


def gem5_metrics(path):
    # Later complete statistic dumps overwrite earlier values, never sum dumps.
    stats = {}
    for line in path.read_text().splitlines():
        f = line.split()
        if len(f) >= 2 and not line.startswith("#"):
            try:
                stats[f[0]] = float(f[1])
            except ValueError:
                continue
    names = {
        "instructions": "system.cpu.commitStats0.numInsts",
        "cycles": "system.cpu.numCycles",
        "ticks": "simTicks",
        "seconds": "simSeconds",
    }
    selected = {k: stats.get(v) for k, v in names.items()}
    if any(v is None or not math.isfinite(v) or v <= 0 for v in selected.values()):
        raise ContractError("missing, mixed or invalid native gem5 CPU statistics")
    instructions, cycles = stats[names["instructions"]], stats[names["cycles"]]
    return {
        "engine": "gem5",
        "measurement": "native-simulator-statistics",
        "counters": selected,
        "native_cpi": cycles / instructions,
        "native_ipc": instructions / cycles,
        "source_keys": names,
        "global_sim_instructions": stats.get("simInsts"),
        "raw_stats_sha256": digest(path),
        "scope": "whole run, including boot and idle",
        "fidelity": "native gem5 model cycles; not calibrated silicon performance",
    }
