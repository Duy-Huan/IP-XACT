"""Pinned native gem5 DMA extension, preserving every existing engine binary."""

import fcntl
import json
import shutil
import sys
from pathlib import Path

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
from .pipeline import verify_source
from .project import inside
from .runner import write_json
from .unified import RESOURCES as SHARED
from .unified import copy_extension, native_identity

RESOURCES = Path(__file__).parent / "resources/coherent"
NATIVE = ("UspDma.py.in", "usp_dma.cc", "usp_dma.hh")


def native_inputs():
    return dict(
        builder=digest(Path(__file__)),
        shared=native_identity(),
        dma={n: digest(RESOURCES / n) for n in NATIVE},
    )


def build(source, out, jobs=2):
    source = verify_source(source, GEM5_SHA)
    out = out.resolve()
    if type(jobs) is not int or not 1 <= jobs <= 4:
        raise ContractError("unsupported native coherent build concurrency")
    out.mkdir(parents=True, exist_ok=False)
    expected = native_inputs()
    config = configure_external_systemc(source, "RISCV", out)
    extension = out / "extension"
    extension.mkdir()
    copy_extension(SHARED, native_identity(), extension)
    for name in NATIVE:
        text = (RESOURCES / name).read_text()
        if name == "UspDma.py.in":
            text = text.replace("'usp_dma.hh'", repr(str(extension / "usp_dma.hh")))
        (extension / name.removesuffix(".in")).write_text(text)
    with (extension / "SConscript").open("a") as f:
        f.write("\nSimObject('UspDma.py', sim_objects=['UspDma'])\nSource('usp_dma.cc')\n")
    binary = source / "build/RISCV/gem5.opt"
    with (source / "build/.usp-build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old = digest(binary)
        shutil.copy2(binary, out / "base-gem5.opt")
        args = [
            "scons",
            "build/RISCV/gem5.opt",
            f"-j{jobs}",
            "--without-tcmalloc",
            "EXTRAS=" + str(extension),
        ]
        config_tool = Path(sys.base_prefix) / "bin/python3-config"
        if config_tool.exists():
            args.append("PYTHON_CONFIG=" + str(config_tool))
        try:
            execute_build(args, source, out / "build.log", 7200, python_environment())
            shutil.copy2(binary, out / "gem5.opt")
        finally:
            shutil.copy2(out / "base-gem5.opt", binary)
            if digest(binary) != old:
                raise ContractError("original gem5 engine was not restored")
    verify_source(source, GEM5_SHA)
    if expected != native_inputs():
        raise ContractError("native DMA source changed during compilation")
    record_gem5_configuration(out, out / "gem5.opt", config)
    shutil.copy2(source / "LICENSE", out / "gem5-LICENSE")
    row = dict(
        contract="coherent-garnet-dma-v1",
        source_commit=GEM5_SHA,
        configuration=config,
        inputs=expected,
        binary_sha256=digest(out / "gem5.opt"),
        base_binary_sha256=old,
        files={p.relative_to(out).as_posix(): digest(p) for p in extension.iterdir()},
    )
    write_json(out / "coherent-build.json", row)
    return row


def verify(out):
    row = json.loads((out / "coherent-build.json").read_text())
    verify_gem5_configuration(out, row)
    if (
        row["contract"] != "coherent-garnet-dma-v1"
        or row["source_commit"] != GEM5_SHA
        or row["inputs"] != native_inputs()
        or row["binary_sha256"] != digest(out / "gem5.opt")
    ):
        raise ContractError("stale native coherent DMA engine")
    for name, sha in row["files"].items():
        if digest(inside(out, name)) != sha:
            raise ContractError("modified native coherent extension")
    return row
