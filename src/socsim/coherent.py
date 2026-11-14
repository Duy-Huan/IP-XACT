"""Explicit multihart/Ruby/Garnet/DMA lowering of a verified source-IP I/O design."""

import argparse
import hashlib
import json
import math
import os
import shutil
import signal
import struct
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from string import Template

from . import coherent_build
from .domain import AddressSpace, ContractError, Instance, InterruptRoute, Window
from .execution import cleanup_on_termination
from .fabric import prepare
from .gem5 import python_environment
from .ipblocks import verify_models
from .ipxact import digest
from .pipeline import execute, identity, verify_generated
from .project import inside, load_project, read_yaml
from .runner import tool_version, write_json
from .unified import board_contract, trace_checks

RESOURCES = coherent_build.RESOURCES
MARKERS = [
    "USP_COHERENT_BOOT_OK",
    "USP_MULTIHART_COHERENCE_OK",
    "USP_DIRTY_CACHE_DMA_READ_WRITE_OK",
    "USP_DMA_PLIC_IRQ_OK",
    "USP_DMA_RANGE_GUARD_OK",
    "USP_CHIPYARD_GCD_IRQ_OK",
    "USP_OPENTITAN_GPIO_IRQ_OK",
    "USP_TEST_PASS",
]


def adapter_identity():
    return dict(
        module=digest(Path(__file__)),
        resources={
            n: digest(RESOURCES / n)
            for n in ("board.py.in", "start.S", "linker.ld.in", "firmware.c.in")
        },
    )


def contract(project, profile):
    data = read_yaml(profile.resolve())
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "harts", "dma"}
        or data["schema_version"] != "coherent-garnet-v1"
        or type(data["harts"]) is not int
        or data["harts"] != 2
        or not isinstance(data["dma"], dict)
        or set(data["dma"]) != {"base", "irq"}
    ):
        raise ContractError("expected explicit two-hart coherent-garnet-v1 profile")
    base, irq = data["dma"]["base"], data["dma"]["irq"]
    if (
        type(base) is not int
        or base < 0
        or base % 4096
        or base + 4096 > 1 << 64
        or type(irq) is not int
        or not 1 <= irq < 32
    ):
        raise ContractError("invalid DMA aperture or interrupt")
    board = board_contract(project)
    for node in project.ir.instances:
        if node.name in {"dma", "rv64_1", "noc", "l1_0", "l1_1"}:
            raise ContractError("coherent instance name collision")
        for w in node.windows:
            if base < w.base + w.size and w.base < base + 4096:
                raise ContractError("DMA window overlaps the verified base design")
    if irq in [i.line for i in project.ir.interrupts if i.target == "plic"]:
        raise ContractError("DMA interrupt conflicts with the verified base design")
    board.update(dma_base=base, dma_size=4096, dma_irq=irq, harts=2)
    cpu = next(i for i in project.ir.instances if i.name == "rv64")
    nodes = tuple(
        replace(i, parameters=(*i.parameters, ("hart_id", 0))) if i.name == cpu.name else i
        for i in project.ir.instances
    )
    extra = (
        replace(cpu, name="rv64_1", parameters=(*cpu.parameters, ("hart_id", 1))),
        Instance("dma", "usp:device:coherent-copy-dma:1", (Window("registers", base, 4096, 32),)),
        Instance("noc", "usp:interconnect:garnet-pt2pt:1", (), (("routers", 6),)),
        *(
            Instance(
                f"l1_{i}",
                "usp:cache:ruby-mi:1",
                (),
                (("bytes", 32768), ("line_bytes", 64), ("ways", 2)),
            )
            for i in range(2)
        ),
    )
    ir = replace(
        project.ir,
        instances=(*nodes, *extra),
        sources=(*project.ir.sources, ("coherent_profile", digest(profile))),
        interrupts=(
            *project.ir.interrupts,
            InterruptRoute("dma", "plic", irq, "level-high"),
            *(replace(i, target="rv64_1") for i in project.ir.interrupts if i.target == "rv64"),
        ),
        address_spaces=(
            *(
                replace(s, targets=(*s.targets, "dma")) if s.initiator == "rv64" else s
                for s in project.ir.address_spaces
            ),
            *(
                (
                    AddressSpace(
                        "hart0", "rv64", tuple(i.name for i in (*nodes, *extra) if i.windows)
                    ),
                )
                if not any(s.initiator == "rv64" for s in project.ir.address_spaces)
                else ()
            ),
            AddressSpace("hart1", "rv64_1", tuple(i.name for i in (*nodes, *extra) if i.windows)),
            AddressSpace("dma-aperture", "dma", ("ram",)),
        ),
    )
    return board, ir


def firmware(board, out, negative=False):
    compiler, version = tool_version("riscv64-unknown-elf-gcc")
    if "14.2.0" not in version:
        raise ContractError("coherent firmware requires pinned GCC14.2.0")
    for name in ("start.S", "linker.ld.in", "firmware.c.in"):
        text = (RESOURCES / name).read_text()
        if name.endswith(".in"):
            text = Template(text).substitute(board)
        (out / name.removesuffix(".in")).write_text(text)
    args = [
        compiler,
        "-march=rv64imac_zicsr_zifencei",
        "-mabi=lp64",
        "-mcmodel=medany",
        "-msmall-data-limit=0",
        "-mno-relax",
        "-O2",
        "-ffreestanding",
        "-fno-builtin",
        "-fno-stack-protector",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-nostdlib",
        "-nostartfiles",
        "-Wl,--no-relax,--build-id=none",
        f"-DUSP_COHERENT_NEGATIVE={int(negative)}",
        "-T",
        str(out / "linker.ld"),
        str(out / "start.S"),
        str(out / "firmware.c"),
        "-lgcc",
        "-o",
        str(out / "firmware.elf"),
    ]
    execute(args, out, out / "firmware-build.log")
    return dict(compiler=version, compiler_sha256=digest(Path(compiler)))


def stats(out):
    values = {}
    for line in (out / "stats.txt").read_text().splitlines():
        f = line.split()
        if len(f) >= 2:
            try:
                values[f[0]] = float(f[1])
            except ValueError:
                pass
    return values


def inspect(out):
    config = json.loads((out / "config.json").read_text())["system"]
    statistics = stats(out)
    serial = (out / "uart.log").read_text()
    dma = [json.loads(line) for line in (out / "dma-trace.jsonl").read_text().splitlines()]
    trace = [json.loads(line) for line in (out / "bridge-trace.jsonl").read_text().splitlines()]
    cpus = config["cpu"]
    cpu_metrics = []
    for cpu in cpus:
        p = cpu["path"]
        cpu_metrics.append(
            {
                n: statistics.get(p + "." + s, 0)
                for n, s in (
                    ("instructions", "commitStats0.numInsts"),
                    ("cycles", "numCycles"),
                    ("cpi", "commitStats0.cpi"),
                    ("ipc", "commitStats0.ipc"),
                )
            }
        )
    phases = [d["phase"] for d in dma]
    expected = [p for _ in range(4) for p in ("start", "read_done", "write_done", "ack")]
    expected += ["range_reject", "ack"] * 2
    sequence_ok = phases == expected
    for i in range(4):
        if len(dma) >= i * 4 + 3:
            start, read, write = dma[i * 4 : i * 4 + 3]
            sequence_ok &= (
                start["tick"] < read["tick"] < write["tick"]
                and read["checksum"] == write["checksum"] == expected_checksum(i + 1)
                and write["bytes"] == 512
                and all(d[k] == start[k] for d in (read, write) for k in ("src", "dst", "bytes"))
            )
    board = json.loads((out / "board.json").read_text())
    exit_info = json.loads((out / "exit.json").read_text())
    process = json.loads((out / "process.json").read_text())
    wiring = len(cpus) == 2
    for i, cpu in enumerate(cpus):
        seq = f"system.ruby.l1_cntrl{i}.sequencer.in_ports"
        wiring &= (
            cpu["icache_port"]["peer"] == seq + "[0]" and cpu["dcache_port"]["peer"] == seq + "[1]"
        )
    dma_peer = config["dma"]["dma"]["peer"]
    memory = config.get("mem_ctrls", [])
    checks = dict(
        markers=all(serial.count(m) == 1 for m in MARKERS),
        no_guest_failure="USP_COHERENT_FAIL" not in serial,
        actual_two_cpus=wiring
        and all(m["instructions"] > 0 and m["cycles"] > 0 for m in cpu_metrics),
        actual_garnet=config["ruby"]["network"]["type"] == "GarnetNetwork"
        and len(config["ruby"]["network"]["routers"]) == 6,
        dma_through_ruby=dma_peer == "system.ruby.dma_cntrl0.dma_sequencer.in_ports[0]"
        and config["ruby"]["dma_cntrl0"]["dma_sequencer"]["in_ports"]["peer"] == ["system.dma.dma"],
        dma_sequences=sequence_ok,
        single_memory_owner="mem" not in config
        and len(memory) == 1
        and memory[0]["port"]["peer"] == "system.ruby.dir_cntrl0.memory_out_port",
        ram_aperture=config["dma"]["ram_base"] == board["ram_base"]
        and config["dma"]["ram_size"] == board["ram_size"],
        real_network=statistics.get("system.ruby.network.packets_received::total", 0) > 0
        and statistics.get("system.ruby.network.flits_received::total", 0) > 0,
        finite_metrics=all(math.isfinite(v) and v > 0 for m in cpu_metrics for v in m.values()),
        source_ips=all(trace_checks(trace).values()),
        guest_exit=exit_info["cause"] == "USP guest finisher" and exit_info["code"] == 0,
        process_exit=process["returncode"] == 0
        and process["model_returncode"] == 0
        and not process["timed_out"],
    )
    return dict(
        checks=checks,
        cpus=cpu_metrics,
        network={
            k: v
            for k, v in statistics.items()
            if k.startswith("system.ruby.network.") and k.count(".") == 3 and math.isfinite(v)
        },
        dma_transfers=phases.count("write_done"),
        rejected_descriptors=phases.count("range_reject"),
    )


def expected_checksum(round):
    value = 2166136261
    for i in range(64):
        pattern = 0x12345678ABC00000 ^ (round << 32) ^ (i * 0x12345)
        for byte in struct.pack("<Q", pattern):
            value = ((value ^ byte) * 16777619) & 0xFFFFFFFF
    return value


@cleanup_on_termination
def run(project, generated, models, bridge, source, profile, out, negative=False):
    generated, models, bridge, source, out = (
        p.resolve() for p in (generated, models, bridge, source, out)
    )
    verify_generated(project, generated)
    engine = coherent_build.verify(bridge)
    model = verify_models(models)
    board, ir = contract(project, profile)
    out.mkdir(parents=True, exist_ok=False)
    expected = adapter_identity()
    fw = firmware(board, out, negative)
    write_json(out / "base-ir.json", project.ir.to_dict())
    write_json(out / "soc-ir.json", ir.to_dict())
    shutil.copy2(profile, out / "profile.yaml")
    (out / "rom.bin").write_bytes(
        struct.pack(
            "<6IQ",
            0x00000297,
            0xF1402573,
            0x00000593,
            0x0182B283,
            0x00028067,
            0x00000013,
            board["entry"],
        )
    )
    board.update(rom_image=str(out / "rom.bin"), dma_trace=str(out / "dma-trace.jsonl"))
    prepare(source, out, board)
    write_json(out / "board.json", board)
    shutil.copy2(RESOURCES / "board.py.in", out / "board.py")
    timed_out = False
    with tempfile.TemporaryDirectory(prefix="usp-coherent-") as temp:
        sock = Path(temp) / "ip.sock"
        with (out / "model.log").open("wb") as log:
            child = subprocess.Popen(
                [str(models / "rtl/Vusp_ipblocks"), str(sock), str(out / "bridge-trace.jsonl")],
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 60
                while not sock.is_socket():
                    if child.poll() is not None or time.monotonic() > deadline:
                        raise ContractError("SystemC IP model startup failed")
                    time.sleep(0.01)
                args = [
                    str(bridge / "gem5.opt"),
                    "--listener-mode=off",
                    "--outdir=" + str(out),
                    str(out / "board.py"),
                    str(out / "board.json"),
                    str(sock),
                    str(out / "firmware.elf"),
                    str(out / "exit.json"),
                ]
                write_json(out / "command.json", dict(argv=args, cwd=str(out)))
                with (out / "engine.log").open("wb") as log:
                    guest = subprocess.Popen(
                        args,
                        cwd=out,
                        env=python_environment(),
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    try:
                        try:
                            rc = guest.wait(timeout=180)
                        except subprocess.TimeoutExpired:
                            timed_out = True
                            os.killpg(guest.pid, signal.SIGTERM)
                            rc = guest.wait(timeout=10)
                    finally:
                        if guest.poll() is None:
                            os.killpg(guest.pid, signal.SIGKILL)
                            guest.wait()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGTERM)
                    child.wait(timeout=5)
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
    write_json(
        out / "process.json",
        dict(returncode=rc, model_returncode=child.returncode, timed_out=timed_out),
    )
    (out / "uart.log").write_bytes(b"".join(p.read_bytes() for p in sorted(out.glob("*.terminal"))))
    result = inspect(out)
    result.update(
        profile="coherent-garnet-v1",
        status="PASSED" if all(result["checks"].values()) else "FAILED",
        adapter=expected,
        base_ir_sha256=identity(project),
        target_ir_sha256=hashlib.sha256(
            json.dumps(ir.to_dict(), sort_keys=True).encode()
        ).hexdigest(),
        engine_build=engine,
        model_build=model,
        firmware=fw,
        negative_fixture=negative,
        limitations=[
            "Native gem5 two-hart MI_example/Pt2Pt reference, not arbitrary CPU or NoC topology",
            "PIO bypasses Garnet; SystemC IP timing remains transaction-driven",
            "No QEMU/RTL-master DMA, Linux/SMP OS or globally calibrated hardware timing claim",
        ],
    )
    shutil.copytree(models / "licenses", out / "licenses")
    if adapter_identity() != expected:
        raise ContractError("coherent adapter changed during execution")
    result["files"] = {
        p.relative_to(out).as_posix(): digest(p) for p in out.rglob("*") if p.is_file()
    }
    write_json(out / "report.json", result)
    (out / "report.md").write_text(
        "# Unified coherent SoC\n\n"
        + result["status"]
        + "\n\n"
        + "\n".join(f"- {k}: {v}" for k, v in result["checks"].items())
        + "\n"
    )
    return result


def verify(out):
    out = out.resolve()
    r = json.loads((out / "report.json").read_text())
    if r["profile"] != "coherent-garnet-v1" or r["adapter"] != adapter_identity():
        raise ContractError("stale coherent SoC adapter")
    required = {
        "firmware.elf",
        "firmware.c",
        "start.S",
        "linker.ld",
        "board.py",
        "board.json",
        "profile.yaml",
        "base-ir.json",
        "soc-ir.json",
        "rom.bin",
        "stats.txt",
        "config.json",
        "dma-trace.jsonl",
        "bridge-trace.jsonl",
        "process.json",
        "exit.json",
        "uart.log",
    }
    if not required <= r["files"].keys():
        raise ContractError("incomplete coherent SoC artifact inventory")
    for name, sha in r["files"].items():
        if digest(inside(out, name)) != sha:
            raise ContractError("modified coherent SoC artifact")
    actual = inspect(out)
    if any(r[k] != v for k, v in actual.items()) or r["status"] != (
        "PASSED" if all(actual["checks"].values()) else "FAILED"
    ):
        raise ContractError("modified coherent SoC acceptance")
    for name, field in (("base-ir.json", "base_ir_sha256"), ("soc-ir.json", "target_ir_sha256")):
        sha = hashlib.sha256(
            json.dumps(json.loads((out / name).read_text()), sort_keys=True).encode()
        ).hexdigest()
        if r[field] != sha:
            raise ContractError("coherent IR provenance mismatch")
    return r


def main(argv=None):
    parser = argparse.ArgumentParser(prog="usp coherent")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build-engine")
    for n in ("source", "out"):
        p.add_argument("--" + n, type=Path, required=True)
    p.add_argument("--jobs", type=int, default=2)
    p = sub.add_parser("run")
    p.add_argument("project", type=Path)
    for n in ("generated", "models", "bridge", "source", "profile", "out"):
        p.add_argument("--" + n, type=Path, required=True)
    p.add_argument("--negative", action="store_true")
    sub.add_parser("verify").add_argument("out", type=Path)
    a = parser.parse_args(argv)
    try:
        if a.command == "build-engine":
            r = coherent_build.build(a.source, a.out, a.jobs)
        elif a.command == "verify":
            r = verify(a.out.resolve())
        else:
            r = run(
                load_project(a.project),
                a.generated,
                a.models,
                a.bridge,
                a.source,
                a.profile,
                a.out,
                a.negative,
            )
        print(json.dumps(r, indent=2))
        return 0 if r.get("status", "PASSED") == "PASSED" else 1
    except (ContractError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(status="ERROR", message=str(exc))))
        return 2
