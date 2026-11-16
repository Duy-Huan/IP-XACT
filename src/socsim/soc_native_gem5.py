"""Build a native gem5 per-instance device adapter without changing upstream."""

import fcntl
import json
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


def prepare_run(spec, generated, out, sockets):
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
    rom = struct.pack(
        "<6IQ",
        0x00000297,
        0xF1402573,
        0x00000593,
        0x0182B283,
        0x00028067,
        0x00000013,
        spec["boot"]["entry"],
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
            "max_ticks": 2000000000000,
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
        metrics = gem5_metrics(out / "stats.txt")
        checks["actual_cpu_execution"] = metrics["counters"]["instructions"] > 0
    except (OSError, ContractError):
        checks["actual_cpu_execution"] = False
    return checks, metrics
