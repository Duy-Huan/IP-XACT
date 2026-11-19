"""Source-built gem5 native per-instance devices plus an external RTL master."""

import errno
import fcntl
import json
import os
import shutil
import sys
from pathlib import Path
from uuid import uuid4

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
from .rtl_cpu_build import MASTER_FILES
from .rtl_cpu_build import RESOURCES as MASTER_RESOURCES
from .runner import write_json
from .soc_native_gem5 import FILES as NATIVE_FILES
from .soc_native_gem5 import RESOURCES as NATIVE_RESOURCES


def adapter_identity():
    return {
        "module": digest(Path(__file__)),
        "native": {n: digest(NATIVE_RESOURCES / n) for n in NATIVE_FILES},
        "master": {n: digest(MASTER_RESOURCES / n) for n in MASTER_FILES},
    }


MASTER_SCRIPT = (
    "Import('*')\nSimObject('UspRtlMaster.py', sim_objects=['UspRtlMaster'])\n"
    "Source('usp_rtl_master.cc')\n"
)


def stage(resources, names, out, resume=False, master=False):
    if not resume:
        out.mkdir()
    headers = [name for name in names if name.endswith(".hh")]
    for name in names:
        text = (resources / name).read_text()
        if name.endswith(".py.in"):
            for header in headers:
                text = text.replace(
                    "cxx_header = '" + header + "'", "cxx_header = " + repr(str(out / header))
                )
        if master and name == "SConscript":
            text = MASTER_SCRIPT
        target = out / name.removesuffix(".in")
        if resume:
            if target.read_text() != text:
                raise ContractError("interrupted native RTL source staging differs from adapter")
        else:
            target.write_text(text)


def capture_binary(source, target):
    """Own a complete artifact before restoring the separate source cache entry."""
    temporary = target.with_name(target.name + ".transfer-" + uuid4().hex)
    try:
        os.link(source.resolve(), temporary)
    except OSError as exc:
        if exc.errno not in {errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP}:
            raise
        shutil.copy2(source, temporary)
    if digest(temporary) != digest(source):
        raise ContractError("native RTL engine transfer changed executable bytes")
    os.replace(temporary, target)


@cleanup_on_termination
def build(source, out, jobs=2, resume=False, binary_cache=None):
    source = verify_source(source, GEM5_SHA)
    out = out.resolve()
    if type(resume) is not bool or type(jobs) is not int or not 1 <= jobs <= 4:
        raise ContractError("native RTL engine requires 1..4 jobs and explicit resume policy")
    if out.exists() != resume or (out / "native-rtl-build.json").exists():
        raise ContractError("preserve existing native RTL outputs; resume only an incomplete build")
    if not resume:
        out.mkdir(parents=True)
    identity = adapter_identity()
    preflight = ["scons", "--config=force", "setconfig", "build/RISCV", "USE_SYSTEMC=n"]
    python_config = Path(sys.base_prefix) / "bin/python3-config"
    if python_config.exists():
        preflight.append("PYTHON_CONFIG=" + str(python_config))
    extension = out / "extension"
    if not resume:
        extension.mkdir()
    native, master = extension / "native", extension / "master"
    stage(NATIVE_RESOURCES, NATIVE_FILES, native, resume)
    stage(MASTER_RESOURCES, MASTER_FILES, master, resume, True)
    destination = out / "gem5.opt"
    if binary_cache is not None:
        binary_cache = binary_cache.absolute()
        binary_cache.parent.mkdir(parents=True, exist_ok=True)
        if binary_cache.is_symlink() or (
            destination.is_symlink() and destination.resolve() != binary_cache.resolve()
        ):
            raise ContractError("binary cache must be an explicit owned regular-file destination")
        destination = binary_cache
    elif destination.is_symlink():
        raise ContractError("supply the explicit --binary-cache for an existing artifact alias")
    if destination.exists():
        partial = destination.with_name(destination.name + ".incomplete-" + uuid4().hex)
        os.replace(destination, partial)
        write_json(
            out / ("partial-" + uuid4().hex + ".json"),
            {
                "path": str(partial),
                "sha256": digest(partial),
                "bytes": partial.stat().st_size,
                "scope": "unqualified interrupted transfer retained; never an accepted executable",
            },
        )
    binary = source / "build/RISCV/gem5.opt"
    with (source / "build/.usp-build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if resume:
            config_path = out / "gem5.config"
            if digest(config_path) != digest(source / "build/RISCV/gem5.build/config"):
                raise ContractError("resume Kconfig differs from the interrupted source build")
            configuration = {
                "embedded_systemc": False,
                "kconfig_sha256": digest(config_path),
                "adapter_sha256": digest(Path(__file__).with_name("gem5.py")),
            }
        else:
            execute_build(preflight, source, out / "preflight.log", 600, python_environment())
            configuration = configure_external_systemc(source, "RISCV", out)
        old = digest(binary) if binary.exists() else None
        held = binary.with_name("gem5.opt.original-" + uuid4().hex)
        if binary.exists() or binary.is_symlink():
            os.replace(binary, held)
        command = [
            "scons",
            "build/RISCV/gem5.opt",
            "-j" + str(jobs),
            "--without-tcmalloc",
            "EXTRAS=" + str(native) + ":" + str(master),
        ]
        if python_config.exists():
            command.append("PYTHON_CONFIG=" + str(python_config))
        try:
            log = out / ("build-resume-" + uuid4().hex + ".log" if resume else "build.log")
            execute_build(command, source, log, 14400, python_environment())
            capture_binary(binary, destination)
            if binary_cache is not None and not (out / "gem5.opt").is_symlink():
                (out / "gem5.opt").symlink_to(binary_cache)
        except BaseException:
            if binary.exists():
                failed = binary.with_name("gem5.opt.failed-transfer-" + uuid4().hex)
                os.replace(binary, failed)
                write_json(
                    out / ("failed-transfer-" + uuid4().hex + ".json"),
                    {
                        "preserved_candidate": str(failed),
                        "sha256": digest(failed),
                        "base_sha256": old,
                        "scope": "failed/incomplete build; not qualification",
                    },
                )
            raise
        finally:
            if held.exists() or held.is_symlink():
                os.replace(held, binary)
                if digest(binary) != old:
                    raise ContractError("could not restore the original gem5 engine")
            elif binary.exists():
                # No original cache executable existed. The owned output has its
                # own directory entry; leave the source cache in its initial state.
                binary.unlink()
    verify_source(source, GEM5_SHA)
    if adapter_identity() != identity:
        raise ContractError("native RTL engine adapter changed during compilation")
    shutil.copy2(source / "LICENSE", out / "gem5-LICENSE")
    record_gem5_configuration(out, out / "gem5.opt", configuration)
    record = {
        "contract": "native-rtl-memory-engine-v1",
        "source_commit": GEM5_SHA,
        "adapter_identity": identity,
        "configuration": configuration,
        "binary_sha256": digest(out / "gem5.opt"),
        "base_binary_sha256": old,
        "artifact_storage": str(binary_cache) if binary_cache else "owned output file",
        "required_classes": ["NativeSocDevice", "UspRtlMaster"],
        "memory_contract": "external atomic physical master; IRQ context must stay halted",
        "dma_supported": False,
        "timing_correlation": False,
        "files": {
            p.relative_to(out).as_posix(): digest(p) for p in extension.rglob("*") if p.is_file()
        },
    }
    write_json(out / "native-rtl-build.json", record)
    return record


def verify(out):
    out = out.resolve()
    record = json.loads((out / "native-rtl-build.json").read_text())
    verify_gem5_configuration(out, record)
    if (
        record["contract"] != "native-rtl-memory-engine-v1"
        or record["source_commit"] != GEM5_SHA
        or record["adapter_identity"] != adapter_identity()
        or record["binary_sha256"] != digest(out / "gem5.opt")
        or record["dma_supported"] is not False
        or record["timing_correlation"] is not False
        or record["required_classes"] != ["NativeSocDevice", "UspRtlMaster"]
        or record["memory_contract"]
        != "external atomic physical master; IRQ context must stay halted"
    ):
        raise ContractError("stale/modified native RTL memory engine")
    for name, sha in record["files"].items():
        if digest(inside(out, name)) != sha:
            raise ContractError("modified native RTL extension source")
    return record
