"""Pinned, source-backed RTL CPU and gem5 physical-master builds."""

import fcntl
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .domain import ContractError
from .execution import cleanup_on_termination
from .gem5 import (
    GEM5_SHA,
    configure_external_systemc,
    python_environment,
    record_gem5_configuration,
    verify_gem5_configuration,
)
from .ipxact import digest
from .pipeline import execute, verify_source
from .project import inside, read_yaml
from .runner import tool_version, write_json
from .unified import RESOURCES as PERIPHERALS
from .unified import native_identity

RESOURCES = Path(__file__).parent / "resources/rtl_cpu"
CHIPYARD = "69eba860a352343e4ac6b6df0f3638a79a86ec78"
WRAPPER = "1a030b1d20e0bfb3f8ca250549f4826f1d2b2c32"
CORE = "48f11c673348908c37d3eaa9bd1cd0a5b5c69253"
MASTER_FILES = ("UspRtlMaster.py.in", "usp_rtl_master.hh", "usp_rtl_master.cc")


def load_manifest(path):
    m = read_yaml(path.resolve())
    common = {"schema_version", "interface", "kind", "model", "files"}
    if not isinstance(m, dict):
        raise ContractError("invalid RTL CPU manifest")
    builtin = m.get("kind") == "chipyard-ibex"
    fields = common | ({"writeback", "branch_target_alu"} if builtin else set())
    if (
        set(m) != fields
        or m["schema_version"] != "1"
        or m["interface"] != "obi-rvfi32-v1"
        or m["kind"] not in {"chipyard-ibex", "custom-rtl"}
        or not isinstance(m["model"], str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", m["model"])
        or not isinstance(m["files"], list)
    ):
        raise ContractError("unsupported RTL CPU interface/manifest")
    if builtin and (
        m["model"] != "Ibex"
        or m["files"]
        or type(m["writeback"]) is not bool
        or type(m["branch_target_alu"]) is not bool
    ):
        raise ContractError("built-in Chipyard Ibex source/model cannot be overridden")
    if not builtin and not m["files"]:
        raise ContractError("custom RTL CPU needs real source files")
    for f in m["files"]:
        if (
            not isinstance(f, dict)
            or set(f) != {"path", "sha256"}
            or not isinstance(f["path"], str)
            or not isinstance(f["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", f["sha256"])
            or digest(inside(path.resolve().parent, f["path"])) != f["sha256"]
        ):
            raise ContractError("custom CPU source identity mismatch")
    if len({f["path"] for f in m["files"]}) != len(m["files"]):
        raise ContractError("duplicate custom CPU source")
    return m


def validate_ports(header):
    ports = {
        n: {"direction": d, "width": int(h) - int(low) + 1}
        for d, n, h, low in re.findall(
            r"VL_(IN|OUT|INOUT)(?:8|16|64|W)?\(&([A-Za-z0-9_]+),(\d+),(\d+)\)", header
        )
    }
    inputs = (
        "clk_i rst_ni boot_i i_gnt_i i_valid_i i_error_i i_data_i "
        "d_gnt_i d_valid_i d_error_i d_data_i irq_sw_i irq_timer_i irq_ext_i"
    )
    outputs = (
        "i_req_o i_addr_o d_req_o d_write_o d_be_o d_addr_o d_data_o "
        "retired_o trap_o alert_o pc_o instruction_o"
    )
    wide = {
        "boot_i",
        "i_data_i",
        "i_addr_o",
        "d_data_i",
        "d_addr_o",
        "d_data_o",
        "pc_o",
        "instruction_o",
    }
    expected = {
        n: {"direction": d, "width": 4 if n == "d_be_o" else 32 if n in wide else 1}
        for d, names in [("IN", inputs), ("OUT", outputs)]
        for n in names.split()
    }
    if ports != expected:
        raise ContractError("CPU top ports do not implement obi-rvfi32-v1")
    return ports


@cleanup_on_termination
def build_cpu(manifest, out, chipyard, wrapper, core, jobs=2):
    if not 1 <= jobs <= 4:
        raise ContractError("invalid RTL build parallelism")
    m = load_manifest(manifest)
    chipyard = verify_source(chipyard, CHIPYARD)
    wrapper = verify_source(wrapper, WRAPPER)
    core = verify_source(core, CORE)
    for root, relative, pin in [
        (chipyard, "generators/ibex", WRAPPER),
        (wrapper, "src/main/resources/vsrc/ibex", CORE),
    ]:
        tree = subprocess.check_output(
            ["git", "-C", str(root), "ls-tree", "HEAD", relative], text=True
        )
        if tree.split()[2] != pin:
            raise ContractError("Chipyard CPU transitive pin mismatch")
    _, version = tool_version("verilator")
    if not version.startswith("Verilator 5.032"):
        raise ContractError("RTL master requires Verilator 5.032")
    vroot = subprocess.check_output(["verilator", "--getenv", "VERILATOR_ROOT"], text=True).strip()
    standard = Path(vroot) / "include/verilated_std.sv"
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    flags = shlex.split(
        subprocess.check_output(["pkg-config", "--cflags", "--libs", "systemc"], text=True)
    )
    packages = [
        core / "rtl/ibex_pkg.sv",
        core / "vendor/lowrisc_ip/ip/prim/rtl/prim_ram_1p_pkg.sv",
        core / "vendor/lowrisc_ip/ip/prim/rtl/prim_secded_pkg.sv",
        core / "dv/uvm/core_ibex/common/prim/prim_pkg.sv",
    ]
    dirs = [
        core / "rtl",
        core / "vendor/lowrisc_ip/ip/prim/rtl",
        core / "vendor/lowrisc_ip/ip/prim_generic/rtl",
        core / "dv/uvm/core_ibex/common/prim",
        core / "vendor/lowrisc_ip/dv/sv/dv_utils",
    ]
    if m["kind"] == "chipyard-ibex":
        text = (
            (RESOURCES / "ibex.sv.in")
            .read_text()
            .replace("@WRITEBACK@", str(int(m["writeback"])))
            .replace("@BRANCH@", str(int(m["branch_target_alu"])))
        )
        (out / "cpu.sv").write_text(text)
        extra = [out / "cpu.sv"]
    else:
        extra = [inside(manifest.resolve().parent, f["path"]) for f in m["files"]]
        dirs += sorted({p.parent for p in extra})
    command = [
        "verilator",
        "--cc",
        "--exe",
        "--assert",
        "-DRVFI",
        "--timescale",
        "1ns/1ps",
        "--top-module",
        "usp_rtl_cpu",
        "--Mdir",
        str(out / "sim"),
        "-Wno-fatal",
    ]
    for d in dirs:
        command += ["-I" + str(d), "-y", str(d)]
    command += [
        "-CFLAGS",
        " ".join(["-std=c++17", *[f for f in flags if f.startswith("-I")]]),
        "-LDFLAGS",
        " ".join(f for f in flags if not f.startswith("-I")),
    ]
    command += list(
        map(
            str,
            packages
            + [
                wrapper / "src/main/resources/vsrc/IbexCoreBlackbox.sv",
                *extra,
                RESOURCES / "master.cpp",
            ],
        )
    )
    execute(command, out, out / "verilate.log", 60)
    ports = validate_ports((out / "sim/Vusp_rtl_cpu.h").read_text())
    inputs = {}
    for line in (out / "sim/Vusp_rtl_cpu__verFiles.dat").read_text().splitlines():
        if line.startswith("S "):
            p = Path(line.split('"')[1])
            if (
                m["kind"] == "custom-rtl"
                and p.suffix in {".sv", ".svh", ".v", ".vh"}
                and not p.is_relative_to(core)
                and not p.is_relative_to(wrapper)
                and p not in extra
                and p != standard
            ):
                raise ContractError("undeclared custom RTL source/include in closure")
            inputs[str(p)] = digest(p)
    execute(
        ["make", "-C", str(out / "sim"), "-f", "Vusp_rtl_cpu.mk", "-j" + str(jobs)],
        out,
        out / "build.log",
        600,
    )
    licenses = out / "licenses"
    licenses.mkdir()
    for root, name in [(chipyard, "Chipyard"), (wrapper, "Chipyard-Ibex"), (core, "Ibex")]:
        shutil.copy2(root / "LICENSE", licenses / (name + "-LICENSE"))
    r = {
        "contract": "obi-rvfi32-v1",
        "model": m["model"],
        "manifest": m,
        "manifest_sha256": digest(manifest),
        "chipyard_commit": CHIPYARD,
        "wrapper_commit": WRAPPER,
        "core_commit": CORE,
        "verilator": version,
        "systemc": subprocess.check_output(
            ["pkg-config", "--modversion", "systemc"], text=True
        ).strip(),
        "binary_sha256": digest(out / "sim/Vusp_rtl_cpu"),
        "ports": ports,
        "inputs": inputs,
        "driver_sha256": digest(RESOURCES / "master.cpp"),
        "adapter_template_sha256": digest(RESOURCES / "ibex.sv.in"),
        "builder_sha256": digest(Path(__file__)),
    }
    write_json(out / "cpu-build.json", r)
    return r


def verify_cpu(manifest, out):
    m = load_manifest(manifest)
    r = json.loads((out / "cpu-build.json").read_text())
    if (
        r.get("contract") != "obi-rvfi32-v1"
        or r.get("builder_sha256") != digest(Path(__file__))
        or (r.get("chipyard_commit"), r.get("wrapper_commit"), r.get("core_commit"))
        != (CHIPYARD, WRAPPER, CORE)
        or r.get("model") != m["model"]
        or r["manifest"] != m
        or r["manifest_sha256"] != digest(manifest)
        or r["driver_sha256"] != digest(RESOURCES / "master.cpp")
        or r["adapter_template_sha256"] != digest(RESOURCES / "ibex.sv.in")
        or r["ports"] != validate_ports((out / "sim/Vusp_rtl_cpu.h").read_text())
        or r["binary_sha256"] != digest(out / "sim/Vusp_rtl_cpu")
    ):
        raise ContractError("stale/modified RTL CPU build")
    for p, sha in r["inputs"].items():
        if digest(Path(p)) != sha:
            raise ContractError("modified RTL CPU source closure")
    return r


@cleanup_on_termination
def build_engine(source, out, jobs=2):
    source = verify_source(source, GEM5_SHA)
    out = out.resolve()
    if not 1 <= jobs <= 4:
        raise ContractError("invalid build parallelism")
    out.mkdir(parents=True, exist_ok=False)
    configuration = configure_external_systemc(source, "RISCV", out)
    extension = out / "extension"
    extension.mkdir()
    for n in native_identity():
        shutil.copy2(PERIPHERALS / n, extension / n.removesuffix(".in"))
    for n in MASTER_FILES:
        shutil.copy2(RESOURCES / n, extension / n.removesuffix(".in"))
    with (extension / "SConscript").open("a") as f:
        f.write(
            "\nSimObject('UspRtlMaster.py', sim_objects=['UspRtlMaster'])\n"
            "Source('usp_rtl_master.cc')\n"
        )
    binary = source / "build/RISCV/gem5.opt"
    with (source / "build/.usp-build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old = digest(binary)
        backup = out / "base-gem5.opt"
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
            execute(command, source, out / "build.log", 3600, python_environment())
            shutil.copy2(binary, out / "gem5.opt")
        finally:
            shutil.copy2(backup, binary)
            if digest(binary) != old:
                raise ContractError("failed to restore gem5 SE executable")
    verify_source(source, GEM5_SHA)
    shutil.copy2(source / "LICENSE", out / "gem5-LICENSE")
    record_gem5_configuration(out, out / "gem5.opt", configuration)
    r = {
        "contract": "rtl-master-gem5-v1",
        "configuration": configuration,
        "builder_sha256": digest(Path(__file__)),
        "source_commit": GEM5_SHA,
        "base_binary_sha256": old,
        "binary_sha256": digest(out / "gem5.opt"),
        "peripherals": native_identity(),
        "master": {n: digest(RESOURCES / n) for n in MASTER_FILES},
        "files": {p.relative_to(out).as_posix(): digest(p) for p in extension.iterdir()},
    }
    write_json(out / "engine-build.json", r)
    return r


def verify_engine(out):
    r = json.loads((out / "engine-build.json").read_text())
    verify_gem5_configuration(out, r)
    if (
        r["contract"] != "rtl-master-gem5-v1"
        or r.get("builder_sha256") != digest(Path(__file__))
        or r["source_commit"] != GEM5_SHA
        or r["binary_sha256"] != digest(out / "gem5.opt")
        or r["peripherals"] != native_identity()
        or r["master"] != {n: digest(RESOURCES / n) for n in MASTER_FILES}
    ):
        raise ContractError("stale/modified gem5 RTL master")
    for n, sha in r["files"].items():
        if digest(inside(out, n)) != sha:
            raise ContractError("modified gem5 RTL extension")
    return r
