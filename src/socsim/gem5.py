"""Pinned RISC-V syscall-emulation workload adapter, explicitly not a full SoC."""

import json
import os
import shutil
import sys
import time
from pathlib import Path
from string import Template

from .domain import ContractError
from .ipxact import digest
from .pipeline import execute, identity, verify_generated, verify_source
from .runner import write_json

GEM5_SHA = "b9da2bfe1e21f6efa2730edd4e80e58c5d965458"


def python_environment():
    env = os.environ.copy()
    library = Path(sys.base_prefix) / "lib"
    env["LD_LIBRARY_PATH"] = str(library) + ":" + env.get("LD_LIBRARY_PATH", "")
    return env


def configure_external_systemc(source, target, out):
    """Use upstream Kconfig; SystemC executes in our separately built process."""
    if target not in {"RISCV", "Garnet_standalone"}:
        raise ContractError("unsupported gem5 configuration target")
    command = ["scons", "setconfig", "build/" + target, "USE_SYSTEMC=n"]
    config = Path(sys.base_prefix) / "bin/python3-config"
    if config.exists():
        command.append("PYTHON_CONFIG=" + str(config))
    execute(command, source, out / "configure-external-systemc.log", 600, python_environment())
    native = source / "build" / target / "gem5.build/config"
    contents = native.read_text().splitlines()
    required = ["# USE_SYSTEMC is not set"]
    required += (
        ["BUILD_ISA=y", "USE_RISCV_ISA=y"]
        if target == "RISCV"
        else ["RUBY_PROTOCOL_Garnet_standalone=y"]
    )
    if not set(required).issubset(contents):
        raise ContractError("gem5 native Kconfig does not implement the selected profile")
    shutil.copy2(native, out / "gem5.config")
    return {
        "embedded_systemc": False,
        "kconfig_sha256": digest(out / "gem5.config"),
        "adapter_sha256": digest(Path(__file__)),
    }


def record_gem5_configuration(out, binary, configuration):
    if configuration["adapter_sha256"] != digest(Path(__file__)):
        raise ContractError("gem5 build adapter changed during compilation")
    execute([str(binary), "--build-info"], out, out / "build-info.log", 30, python_environment())
    if "    USE_SYSTEMC = False" not in (out / "build-info.log").read_text().splitlines():
        raise ContractError("compiled gem5 does not match external SystemC profile")
    configuration["build_info_sha256"] = digest(out / "build-info.log")


def verify_gem5_configuration(out, record):
    # Retain preexisting source-pinned binaries; new records additionally bind
    # native Kconfig and the executable's own build-info output.
    c = record.get("configuration")
    if c is None:
        return
    if (
        c.get("embedded_systemc") is not False
        or c.get("adapter_sha256") != digest(Path(__file__))
        or c.get("kconfig_sha256") != digest(out / "gem5.config")
        or c.get("build_info_sha256") != digest(out / "build-info.log")
        or "# USE_SYSTEMC is not set" not in (out / "gem5.config").read_text().splitlines()
        or "    USE_SYSTEMC = False" not in (out / "build-info.log").read_text().splitlines()
    ):
        raise ContractError("modified/stale gem5 configuration evidence")


def build_gem5(out, source, jobs=4):
    source = verify_source(source, GEM5_SHA)
    out.mkdir(parents=True, exist_ok=True)
    configuration = configure_external_systemc(source, "RISCV", out)
    command = ["scons", "build/RISCV/gem5.opt", "-j" + str(jobs), "--without-tcmalloc"]
    config = Path(sys.base_prefix) / "bin/python3-config"
    if config.exists():
        command.append("PYTHON_CONFIG=" + str(config))
    execute(
        command,
        source,
        out / "gem5-build.log",
        3600,
        python_environment(),
    )
    binary = source / "build/RISCV/gem5.opt"
    record_gem5_configuration(out, binary, configuration)
    write_json(
        out / "gem5-build.json",
        {
            "source_commit": GEM5_SHA,
            "binary_sha256": digest(binary),
            "binary": str(binary),
            "configuration": configuration,
        },
    )
    return binary


def run_gem5(project, generated, build_dir, result_dir):
    verify_generated(project, generated)
    if project.manifest["profile"] != "usp-rv64-v1":
        raise ContractError("SE workload comparison requires the explicit non-hybrid profile")
    record = json.loads((build_dir / "gem5-build.json").read_text())
    verify_gem5_configuration(build_dir, record)
    binary = Path(record["binary"])
    if record["source_commit"] != GEM5_SHA or digest(binary) != record["binary_sha256"]:
        raise ContractError("gem5 binary identity mismatch")
    workload = generated / "linux/workload"
    linux_record = json.loads((generated / "linux-build.json").read_text())
    if digest(workload) != linux_record["workload_sha256"]:
        raise ContractError("the shared QEMU/Linux workload was modified")
    with workload.open("rb") as stream:
        header = stream.read(20)
    if (
        len(header) != 20
        or header[:6] != b"\x7fELF\x02\x01"
        or header[7] != 3
        or header[18:20] != b"\xf3\x00"
    ):
        raise ContractError("SE profile requires an explicit Linux-ABI little-endian RV64 ELF")
    result_dir.mkdir(parents=True, exist_ok=False)
    ram_size = next(i.windows[0].size for i in project.ir.instances if i.name == "ram")
    template = Path(__file__).parent / "templates/gem5-se.py.in"
    config = result_dir / "config.py"
    config.write_text(Template(template.read_text()).substitute(ram_size=ram_size))
    command = [
        str(binary),
        "--outdir=" + str(result_dir),
        str(config),
        str(workload),
        str(result_dir / "exit.json"),
    ]
    started = time.monotonic()
    execute(command, result_dir, result_dir / "stdout.log", 60, python_environment())
    exit_info = json.loads((result_dir / "exit.json").read_text())
    log = (result_dir / "stdout.log").read_text(errors="replace")
    stats = {}
    for line in (result_dir / "stats.txt").read_text().splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] in {
            "simInsts",
            "simTicks",
            "simSeconds",
            "simFreq",
            "system.cpu.numCycles",
        }:
            stats[fields[0]] = float(fields[1])
    passed = (
        "USP_SHARED_WORKLOAD_OK" in log
        and exit_info["code"] == 0
        and "last active thread" in exit_info["cause"]
        and stats.get("simInsts", 0) > 0
    )
    report = {
        "status": "PASSED" if passed else "FAILED",
        "profile": "architecture-gem5-se",
        "scope": "syscall emulation; CPU/memory only, not full-system MMIO/OS emulation",
        "fidelity": "timing-uncalibrated",
        "source_commit": GEM5_SHA,
        "binary_sha256": digest(binary),
        "workload_sha256": digest(workload),
        "qemu_shared_workload_sha256": linux_record["workload_sha256"],
        "ir_sha256": identity(project),
        "exit": exit_info,
        "statistics": stats,
        "wall_time_seconds": time.monotonic() - started,
        "assumptions": [
            "TimingSimpleCPU; 1GHz; no caches; SimpleMemory 10ns; one hart",
            "CPU may enable extra ISA extensions; shared workload is restricted to RV64IMAC",
        ],
        "unmodeled_instances": [
            i.name for i in project.ir.instances if i.name not in {"rv64", "ram"}
        ],
    }
    write_json(result_dir / "report.json", report)
    (result_dir / "report.md").write_text(
        "# gem5 SE shared workload\n\nStatus: **"
        + report["status"]
        + "**\n\n"
        + report["scope"]
        + "\n\nFidelity: timing-uncalibrated.\n\n"
        + "Actual statistics:\n\n"
        + "\n".join(f"- {k}: {v}" for k, v in stats.items())
        + "\n\nShared ELF SHA256: `"
        + report["workload_sha256"]
        + "`\n"
    )
    return report
