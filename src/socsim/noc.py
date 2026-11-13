"""Pinned, independent Garnet mesh experiments; no live CPU/RTL coupling claim."""

import argparse
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Any

from .domain import ContractError
from .execution import execute_build
from .gem5 import (
    GEM5_SHA,
    configure_external_systemc,
    python_environment,
    record_gem5_configuration,
    verify_gem5_configuration,
)
from .ipxact import digest
from .pipeline import execute, verify_source
from .project import keys
from .runner import write_json

PATCH = Path(__file__).parent / "resources/noc/garnet-datablock-init.patch"


def validate_noc(data):
    keys(
        data,
        {
            "engine",
            "topology",
            "mesh_rows",
            "endpoints",
            "clock",
            "simulation_ticks",
            "injection_rate",
            "router_latency",
            "link_latency",
            "link_width_bits",
            "vcs_per_vnet",
        },
        "NoC",
    )
    if (data["engine"], data["topology"], data["clock"]) != (
        "garnet-standalone",
        "Mesh_XY",
        "1GHz",
    ):
        raise ContractError("unsupported NoC engine/topology/clock")
    endpoints = data["endpoints"]
    if (
        not isinstance(endpoints, list)
        or not 2 <= len(endpoints) <= 64
        or any(not isinstance(e, str) or not e.isidentifier() or not e.isascii() for e in endpoints)
        or len(set(endpoints)) != len(endpoints)
    ):
        raise ContractError("NoC requires 2..64 unique ASCII endpoint names")
    if len(endpoints) & (len(endpoints) - 1):
        raise ContractError("Garnet directory count must be a power of two")
    limits = {
        "mesh_rows": (1, len(endpoints)),
        "simulation_ticks": (1000000, 1000000000),
        "router_latency": (1, 32),
        "link_latency": (1, 32),
        "vcs_per_vnet": (1, 16),
    }
    for key, (low, high) in limits.items():
        if type(data[key]) is not int or not low <= data[key] <= high:
            raise ContractError(f"invalid NoC {key}")
    if (
        len(endpoints) % data["mesh_rows"]
        or type(data["link_width_bits"]) is not int
        or data["link_width_bits"] not in {32, 64, 128, 256}
    ):
        raise ContractError("invalid rectangular mesh or link width")
    rate = data["injection_rate"]
    if type(rate) not in {int, float} or not math.isfinite(rate) or not 0 < rate <= 0.2:
        raise ContractError("NoC injection rate must be finite in (0, 0.2]")
    return data


def build_noc(source, out, jobs=4):
    source = verify_source(source.resolve(), GEM5_SHA)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    configuration = configure_external_systemc(source, "Garnet_standalone", out)
    command = ["scons", "build/Garnet_standalone/gem5.opt", f"-j{jobs}", "--without-tcmalloc"]
    config = Path(sys.base_prefix) / "bin/python3-config"
    if config.exists():
        command.append("PYTHON_CONFIG=" + str(config))
    protocol = source / "src/mem/ruby/protocol/Garnet_standalone-cache.sm"
    original = protocol.read_bytes()
    before = b"  DataBlock dummyData;"
    after = b'  DataBlock dummyData, constructor="m_ruby_system->getBlockSizeBytes()";'
    if original.count(before) != 1:
        raise ContractError("pinned Garnet standalone protocol context mismatch")
    # Apply only the reviewed initialization change while building, then restore
    # the clean upstream tree even on failure/cancellation. Routing is unchanged.
    try:
        execute(["git", "apply", "--check", str(PATCH)], source, out / "patch-check.log")
        protocol.write_bytes(original.replace(before, after))
        execute_build(command, source, out / "build.log", 7200, python_environment())
    finally:
        protocol.write_bytes(original)
    verify_source(source, GEM5_SHA)
    shutil.copy2(PATCH, out / "garnet-datablock-init.patch")
    shutil.copy2(source / "LICENSE", out / "gem5-LICENSE")
    binary = source / "build/Garnet_standalone/gem5.opt"
    record_gem5_configuration(out, binary, configuration)
    record = {
        "source_commit": GEM5_SHA,
        "configuration": configuration,
        "source": str(source),
        "binary": str(binary),
        "binary_sha256": digest(binary),
        "protocol": "Garnet_standalone",
        "initialization_patch_sha256": digest(PATCH),
    }
    write_json(out / "noc-build.json", record)
    return record


def run_noc(config, build, out):
    config = validate_noc(config)
    record = json.loads((build / "noc-build.json").read_text())
    verify_gem5_configuration(build, record)
    if record["source_commit"] != GEM5_SHA or record["protocol"] != "Garnet_standalone":
        raise ContractError("NoC source/protocol identity mismatch")
    if record.get("initialization_patch_sha256") != digest(PATCH):
        raise ContractError("NoC initialization adapter changed; explicitly rebuild")
    source = verify_source(Path(record["source"]), GEM5_SHA)
    binary = Path(record["binary"])
    if digest(binary) != record["binary_sha256"]:
        raise ContractError("NoC binary identity mismatch")
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    script = source / "configs/example/garnet_synth_traffic.py"
    wrapper = out / "driver.py"
    # This pinned example omits CacheMemory's block size; its synthetic protocol
    # never calls setRubySystem(). Configure the public SimObject parameter,
    # preserving the upstream script and compiled routing/protocol implementation.
    wrapper.write_text(
        "import runpy, sys\n"
        "from pathlib import Path\n"
        "from m5.objects import RubyCache\n"
        "RubyCache.block_size = '64B'\n"
        "script = sys.argv.pop(1)\n"
        "sys.argv[0] = script\n"
        "sys.path.insert(0, str(Path(script).resolve().parents[1]))\n"
        "runpy.run_path(script, run_name='__m5_main__')\n"
    )
    nodes = len(config["endpoints"])
    argv = [
        str(binary),
        "--outdir=" + str(out),
        str(wrapper),
        str(script),
        "--network=garnet",
        "--topology=Mesh_XY",
        "--num-cpus=" + str(nodes),
        "--num-dirs=" + str(nodes),
        "--mesh-rows=" + str(config["mesh_rows"]),
        "--synthetic=uniform_random",
        "--sim-cycles=" + str(config["simulation_ticks"]),
        "--injectionrate=" + str(config["injection_rate"]),
        "--inj-vnet=0",
        "--sys-clock=1GHz",
        "--ruby-clock=1GHz",
        "--routing-algorithm=1",
    ]
    for key in ("router_latency", "link_latency", "link_width_bits", "vcs_per_vnet"):
        argv.append("--" + key.replace("_", "-") + "=" + str(config[key]))
    write_json(out / "noc.json", config)
    execute(argv, out, out / "stdout.log", 120, python_environment())
    statistics = {}
    for line in (out / "stats.txt").read_text().splitlines():
        fields = line.split()
        if len(fields) >= 2 and (
            fields[0].startswith("system.ruby.network.") or fields[0] in {"simTicks", "simFreq"}
        ):
            try:
                value = float(fields[1])
            except ValueError:
                continue
            if math.isfinite(value):
                statistics[fields[0]] = value
    prefix = "system.ruby.network."
    injected = statistics.get(prefix + "packets_injected::total", 0)
    received = statistics.get(prefix + "packets_received::total", 0)
    passed = (
        0 < received <= injected
        and statistics.get(prefix + "average_hops", 0) > 0
        and "Network Tester completed simCycles" in (out / "stdout.log").read_text()
    )
    report: dict[str, Any] = {
        "status": "PASSED" if passed else "FAILED",
        "engine": "gem5 Garnet_standalone",
        "scope": "independent synthetic network experiment; no live CPU/RTL/firmware coupling",
        "fidelity": "network timing uncalibrated",
        "source_commit": GEM5_SHA,
        "initialization_patch_sha256": digest(PATCH),
        "binary_sha256": digest(binary),
        "config_script_sha256": digest(script),
        "driver_sha256": digest(wrapper),
        "adapter_sha256": digest(Path(__file__)),
        "endpoint_ids": dict(enumerate(config["endpoints"])),
        "statistics": statistics,
        "limitations": [
            "Endpoint names label traffic generators/directory sinks, not running IP models",
            "Upstream --sim-cycles compares raw curTick; configuration uses simulation_ticks",
            "Fixed simulation boundary can leave packets in flight; no drain/completion claim",
            "Garnet_standalone has no full-system, DMA, coherence or synthesizable RTL contract",
        ],
    }
    write_json(out / "report.json", report)
    (out / "report.md").write_text(
        "# Garnet network experiment\n\nStatus: **"
        + report["status"]
        + "**\n\n"
        + report["scope"]
        + f"\n\nInjected: {injected}; received: {received}.\n"
        + "\n".join("- " + item for item in report["limitations"])
        + "\n"
    )
    return report


def main(argv):
    parser = argparse.ArgumentParser(prog="usp noc")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--source-gem5", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--jobs", type=int, choices=range(1, 5), default=2)
    args = parser.parse_args(argv)
    try:
        result = build_noc(args.source_gem5, args.out, args.jobs)
        print(json.dumps(result, indent=2))
        return 0
    except (ContractError, OSError, ValueError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}))
        return 2
