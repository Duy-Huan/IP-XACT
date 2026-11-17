"""Build a native gem5 per-instance device adapter without changing upstream."""

import fcntl
import json
import math
import shutil
import struct
import sys
from pathlib import Path

from .domain import ContractError
from .execution import cleanup_on_termination, execute_build
from .gem5 import (
    GEM5_SHA,
    configure_external_systemc,
    python_environment,
    record_gem5_configuration,
    verify_gem5_configuration,
)
from .ipxact import digest
from .pipeline import verify_source
from .project import inside
from .runner import write_json
from .soc_spec import selected

RESOURCES = Path(__file__).parent / "resources/native"
FILES = ("NativeSocDevice.py.in", "native_soc_device.hh", "native_soc_device.cc", "SConscript")


def native_identity():
    return {n: digest(RESOURCES / n) for n in FILES}


@cleanup_on_termination
def build_engine(source, out, jobs=2):
    source = verify_source(source, GEM5_SHA)
    out = out.resolve()
    if not 1 <= jobs <= 4:
        raise ContractError("native gem5 jobs must be 1..4")
    out.mkdir(parents=True, exist_ok=False)
    preflight = ["scons", "--config=force", "setconfig", "build/RISCV", "USE_SYSTEMC=n"]
    config = Path(sys.base_prefix) / "bin/python3-config"
    if config.exists():
        preflight.append("PYTHON_CONFIG=" + str(config))
    execute_build(preflight, source, out / "configure-preflight.log", 600, python_environment())
    configuration = configure_external_systemc(source, "RISCV", out)
    identity = native_identity()
    extension = out / "extension"
    extension.mkdir()
    for n in FILES:
        text = (RESOURCES / n).read_text()
        if n.endswith(".py.in"):
            text = text.replace(
                "cxx_header = 'native_soc_device.hh'",
                "cxx_header = " + repr(str(extension / "native_soc_device.hh")),
            )
        (extension / n.removesuffix(".in")).write_text(text)
    binary = source / "build/RISCV/gem5.opt"
    with (source / "build/.usp-build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        backup = out / "base-gem5.opt"
        old = digest(binary) if binary.exists() else None
        if old:
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
            execute_build(command, source, out / "build.log", 14400, python_environment())
            shutil.copy2(binary, out / "gem5.opt")
        finally:
            if old:
                shutil.copy2(backup, binary)
                if digest(binary) != old:
                    raise ContractError("could not restore original gem5 engine")
    verify_source(source, GEM5_SHA)
    if identity != native_identity():
        raise ContractError("native gem5 source changed during build")
    shutil.copy2(source / "LICENSE", out / "gem5-LICENSE")
    record_gem5_configuration(out, out / "gem5.opt", configuration)
    record = {
        "contract": "native-soc-gem5-v1",
        "configuration": configuration,
        "source_commit": GEM5_SHA,
        "native_identity": identity,
        "binary_sha256": digest(out / "gem5.opt"),
        "base_binary_sha256": old,
        "files": {p.relative_to(out).as_posix(): digest(p) for p in extension.iterdir()},
    }
    write_json(out / "native-build.json", record)
    return record


def verify_engine(out):
    out = out.resolve()
    record = json.loads((out / "native-build.json").read_text())
    verify_gem5_configuration(out, record)
    if (
        record["contract"] != "native-soc-gem5-v1"
        or record["source_commit"] != GEM5_SHA
        or record["native_identity"] != native_identity()
        or record["binary_sha256"] != digest(out / "gem5.opt")
    ):
        raise ContractError("stale or modified native gem5 engine")
    for n, sha in record["files"].items():
        if digest(inside(out, n)) != sha:
            raise ContractError("modified native gem5 build source")
    return record


def prepare_run(spec, generated, out, sockets, dtb=None):
    from elftools.elf.elffile import ELFFile

    with (out / "firmware.elf").open("rb") as stream:
        elf = ELFFile(stream)
        ram = selected(spec, "ram")
        if (
            elf.elfclass != 64
            or elf["e_machine"] != "EM_RISCV"
            or elf["e_entry"] != spec["boot"]["entry"]
        ):
            raise ContractError("ELF does not match native entry/CPU contract")
        for segment in elf.iter_segments():
            if segment["p_type"] == "PT_LOAD" and not (
                ram["base"] <= segment["p_paddr"]
                and segment["p_paddr"] + segment["p_memsz"] <= ram["base"] + ram["size"]
            ):
                raise ContractError("ELF load segment outside native executable RAM")
    dtb_address = 0
    ram_image = ""
    if dtb is not None:
        dtb_address = ram["base"] + ram["size"] - 0x200000
        with (out / "firmware.elf").open("rb") as stream:
            elf = ELFFile(stream)
            if any(
                seg["p_type"] == "PT_LOAD" and seg["p_paddr"] + seg["p_memsz"] > dtb_address
                for seg in elf.iter_segments()
            ):
                raise ContractError("native firmware overlaps reserved DTB region")
        shutil.copy2(dtb, out / "software.dtb")
        execute_build(
            [
                "riscv64-unknown-elf-objcopy",
                "-I",
                "binary",
                "-O",
                "elf64-littleriscv",
                "-B",
                "riscv",
                "--rename-section",
                ".data=.dtb",
                "--change-section-vma",
                ".dtb=" + hex(dtb_address),
                str(out / "software.dtb"),
                str(out / "dtb.o"),
            ],
            out,
            out / "dtb-objcopy.log",
            60,
        )
        (out / "dtb.ld").write_text(
            "SECTIONS { . = " + hex(dtb_address) + "; .dtb : { *(.dtb) } }\n"
        )
        execute_build(
            [
                "riscv64-unknown-elf-ld",
                "-T",
                str(out / "dtb.ld"),
                str(out / "dtb.o"),
                "-o",
                str(out / "dtb.elf"),
            ],
            out,
            out / "dtb-link.log",
            60,
        )
        ram_image = str(out / "dtb.elf")
    rom = struct.pack(
        "<6IQQ",
        0x00000297,
        0xF1402573,
        0x0202B583,
        0x0182B283,
        0x00028067,
        0x00000013,
        spec["boot"]["entry"],
        dtb_address,
    )
    (out / "rom.bin").write_bytes(rom)
    shutil.copy2(generated / "gem5-board.py", out / "gem5-board.py")
    write_json(
        out / "board.json",
        {
            "spec": spec,
            "sockets": sockets,
            "elf": str(out / "firmware.elf"),
            "rom_image": str(out / "rom.bin"),
            "max_ticks": 10000000000000 if dtb else 2000000000000,
            "ram_image": ram_image,
            "uart_index": str(out / "uart-index.json"),
            "exit_file": str(out / "exit.json"),
        },
    )


def collect_run(spec, out):
    from .performance import gem5_metrics

    index = out / "uart-index.json"
    if index.exists():
        for name, filename in json.loads(index.read_text()).items():
            source = inside(out, filename)
            if source.exists():
                shutil.copy2(source, out / (name + ".uart.log"))
    status = out / "exit.json"
    exit_record = json.loads(status.read_text()) if status.exists() else {}
    checks = {
        "gem5_guest_finisher": exit_record.get("cause") == "Native SoC guest finisher"
        and exit_record.get("code") == 0
    }
    metrics = None
    try:
        metrics = (
            gem5_metrics(out / "stats.txt")
            if spec["cpu"]["harts"] == 1
            else multicore_metrics(out / "stats.txt", spec["cpu"]["harts"])
        )
        for h, counters in enumerate(metrics.get("per_hart", [])):
            checks[f"hart{h}.actual_cpu_execution"] = counters["instructions"] > 0
        checks["actual_cpu_execution"] = metrics["counters"]["instructions"] > 0
    except (OSError, ContractError):
        checks["actual_cpu_execution"] = False
    return checks, metrics


def multicore_metrics(path, harts):
    stats = {}
    for line in path.read_text().splitlines():
        fields = line.split()
        if len(fields) >= 2:
            try:
                stats[fields[0]] = float(fields[1])
            except ValueError:
                continue
    counters, sources = [], []
    for hart in range(harts):
        names = {
            "cycles": f"system.cpu{hart}.numCycles",
            "instructions": f"system.cpu{hart}.commitStats0.numInsts",
        }
        values = {k: stats.get(n, 0) for k, n in names.items()}
        if any(not math.isfinite(v) or v <= 0 for v in values.values()):
            raise ContractError(f"missing native counters for hart {hart}")
        counters.append(
            dict(
                values,
                native_cpi=values["cycles"] / values["instructions"],
                native_ipc=values["instructions"] / values["cycles"],
            )
        )
        sources.append(names)
    instructions = sum(v["instructions"] for v in counters)
    cycles = sum(v["cycles"] for v in counters)
    return {
        "engine": "gem5",
        "measurement": "native-simulator-statistics",
        "counters": {"instructions": instructions, "cycles": cycles},
        "per_hart": counters,
        "native_cpi": cycles / instructions,
        "native_ipc": instructions / cycles,
        "source_keys": sources,
        "raw_stats_sha256": digest(path),
        "scope": "whole run including boot and idle; cycles=sum of core cycles",
        "fidelity": "native model counters, not calibrated silicon or system throughput",
    }
