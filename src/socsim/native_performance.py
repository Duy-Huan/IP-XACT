"""Per-hart actual QEMU events; analytical costs remain distinct from gem5 cycles."""

import json
import math
import shlex
import subprocess
from pathlib import Path

from .domain import ContractError
from .execution import execute_build
from .ipxact import digest
from .performance import DEFAULT_COST, qemu_metrics
from .pipeline import QEMU_SHA, verify_source
from .runner import tool_version, write_json

RESOURCE = Path(__file__).parent / "resources/performance/native_qemu_counter.c"


def validate_cost(cost):
    if (
        not isinstance(cost, dict)
        or set(cost) != set(DEFAULT_COST)
        or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in cost.values())
        or cost["base_cpi"] <= 0
    ):
        raise ContractError("invalid explicit native QEMU cost model")
    return dict(cost)


def build_counter(source, out):
    source = verify_source(source, QEMU_SHA)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    compiler, version = tool_version("cc")
    flags = shlex.split(
        subprocess.check_output(["pkg-config", "--cflags", "glib-2.0"], text=True, timeout=15)
    )
    execute_build(
        [
            compiler,
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
        120,
    )
    record = {
        "contract": "native-qemu-events-1",
        "source_commit": QEMU_SHA,
        "source_sha256": digest(RESOURCE),
        "api_header_sha256": digest(source / "include/qemu/qemu-plugin.h"),
        "binary_sha256": digest(out / "counter.so"),
        "compiler": version,
    }
    write_json(out / "counter-build.json", record)
    return verify_counter(out)


def verify_counter(out):
    record = json.loads((out / "counter-build.json").read_text())
    if (
        record.get("contract") != "native-qemu-events-1"
        or record.get("source_commit") != QEMU_SHA
        or record.get("source_sha256") != digest(RESOURCE)
        or record.get("binary_sha256") != digest(out / "counter.so")
    ):
        raise ContractError("stale or modified native QEMU counter")
    return record


def metrics(path, harts, cost):
    cost = validate_cost(cost)
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict) or set(raw) != {
        "executed_instructions",
        "data_accesses",
        "per_hart",
    }:
        raise ContractError("incomplete native QEMU event evidence")
    per_hart = raw["per_hart"]
    if not isinstance(per_hart, list) or len(per_hart) != harts:
        raise ContractError("native QEMU counters do not cover all declared harts")
    rows = []
    for hart, events in enumerate(per_hart):
        if (
            not isinstance(events, dict)
            or set(events) != {"hart_id", "executed_instructions", "data_accesses"}
            or type(events["hart_id"]) is not int
            or events["hart_id"] != hart
        ):
            raise ContractError("native QEMU hart ownership mismatch")
        rows.append(
            dict(
                hart_id=hart,
                **qemu_metrics(
                    {k: events[k] for k in ("executed_instructions", "data_accesses")}, cost
                ),
            )
        )
    for key in ("executed_instructions", "data_accesses"):
        if type(raw[key]) is not int or sum(row[key] for row in per_hart) != raw[key]:
            raise ContractError("native QEMU aggregate and per-hart events differ")
    result = qemu_metrics({k: raw[k] for k in ("executed_instructions", "data_accesses")}, cost)
    result.update(
        per_hart=rows,
        raw_counters_sha256=digest(path),
        roi="whole-guest-including-boot-and-idle",
        calibration="uncalibrated; no silicon CPI or cross-engine timing equivalence",
    )
    return result
