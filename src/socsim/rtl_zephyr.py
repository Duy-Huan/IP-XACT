"""Source-built RV32 Zephyr BSP for instruction-owning RTL CPU masters."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from string import Template

from .domain import ContractError
from .ipxact import digest
from .pipeline import ZEPHYR_SHA, execute, identity, verify_generated, verify_source
from .project import inside, load_project
from .rtl_cpu import run_cpu, validate_elf, verify_run
from .runner import tool_version, write_json
from .unified import MARKERS, board_contract

RESOURCES = Path(__file__).parent / "resources/zephyr_rtl"
CASES = ("smoke", "tick-disabled")


def adapter_identity():
    return dict(
        module_sha256=digest(Path(__file__)),
        resources={
            p.relative_to(RESOURCES).as_posix(): digest(p)
            for p in RESOURCES.rglob("*")
            if p.is_file()
        },
    )


def build(project, generated, source, out, case="smoke"):
    verify_generated(project, generated)
    source = verify_source(source, ZEPHYR_SHA)
    if case not in CASES:
        raise ContractError("unsupported RTL Zephyr conformance case")
    b = board_contract(project)
    if b["plic_sources"] != 32 or any(
        w.base + w.size > 1 << 32 for i in project.ir.instances for w in i.windows
    ):
        raise ContractError("RV32 Zephyr requires the 32-source, 32-bit physical I/O contract")
    compiler, version = tool_version("riscv64-unknown-elf-gcc")
    if "14.2.0" not in version:
        raise ContractError("RV32 Zephyr requires pinned GCC 14.2.0")
    native = adapter_identity()
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    root = out / "board-root"
    for p in RESOURCES.rglob("*"):
        if p.is_file():
            target = root / p.relative_to(RESOURCES).as_posix().removesuffix(".in")
            target.parent.mkdir(parents=True, exist_ok=True)
            text = p.read_text()
            target.write_text(Template(text).substitute(b) if p.suffix == ".in" else text)
    dts = (generated / "zephyr/boards/usp/usp_rv64/usp_rv64.dts").read_text()
    for old, new in (
        ("Generated from IR SHA256", "RV32 CPU/BSP derived from base I/O IR SHA256"),
        ("RV64", "RV32 RTL"),
        ("socsim,usp-rv64", "socsim,usp-rv32"),
        ("rv64imac_zicsr_zifencei", "rv32imc_zicsr_zifencei"),
        ("qemu,riscv-virt", "socsim,usp-rv32"),
        ("interrupts-extended = <&hlic 11 &hlic 9>;", "interrupts-extended = <&hlic 11>;"),
    ):
        if old not in dts:
            raise ContractError("unexpected generated Zephyr board contract: " + old)
        dts = dts.replace(old, new)
    (root / "boards/usp/usp_rv32/usp_rv32.dts").write_text(dts)
    shutil.copytree(generated / "zephyr/dts", root / "dts")
    shutil.copytree(generated / "app", out / "app")
    if case == "tick-disabled":
        app = out / "app/main.c"
        text = app.read_text()
        old = "k_timer_start(&timer, K_MSEC(20), K_NO_WAIT);"
        if text.count(old) != 1:
            raise ContractError("unexpected generated timer conformance application")
        app.write_text(text.replace(old, "irq_disable(7); " + old))
    env = os.environ.copy()
    env.update(
        ZEPHYR_BASE=str(source),
        ZEPHYR_TOOLCHAIN_VARIANT="cross-compile",
        CROSS_COMPILE=compiler.removesuffix("gcc"),
        XDG_CACHE_HOME=str(out / "cache"),
    )
    ninja, _ = tool_version("ninja")
    if not (source.parent / ".west/config").exists():
        execute(["west", "init", "-l", str(source)], source.parent, out / "west-init.log")
    execute(
        [
            "west",
            "build",
            "-s",
            str(out / "app"),
            "-d",
            str(out / "zephyr-build"),
            "-b",
            "usp_rv32",
            "-o=-j2",
            "--",
            "-DBOARD_ROOT=" + str(root),
            "-DSOC_ROOT=" + str(root),
            "-DZEPHYR_MODULES=",
            "-DBUILD_VERSION=usp-pinned",
            "-DCMAKE_MAKE_PROGRAM=" + ninja,
        ],
        source,
        out / "build.log",
        600,
        env,
    )
    compiled = out / "zephyr-build/zephyr"
    for name, target in (
        ("zephyr.elf", "firmware.elf"),
        (".config", "zephyr.config"),
        ("zephyr.dts", "zephyr.dts"),
    ):
        shutil.copy2(compiled / name, out / target)
    shutil.copy2(source / "LICENSE", out / "Zephyr-LICENSE")
    validate_elf(project, out / "firmware.elf")
    config = (out / "zephyr.config").read_text()
    if (
        "CONFIG_RISCV_ISA_RV32I=y" not in config
        or "CONFIG_RISCV_VECTORED_MODE=y" not in config
        or "CONFIG_RISCV_ISA_EXT_A=y" in config
        or "CONFIG_FPU=y" in config
        or "CONFIG_64BIT=y" in config
    ):
        raise ContractError("Zephyr kernel ISA/context does not match the selected RTL ABI")
    verify_source(source, ZEPHYR_SHA)
    if native != adapter_identity():
        raise ContractError("RTL Zephyr adapter changed during compilation")
    files = [p for d in (root, out / "app") for p in d.rglob("*") if p.is_file()]
    files += [p for p in out.iterdir() if p.is_file()]
    record = dict(
        profile="rv32-rtl-zephyr-v1",
        source_commit=ZEPHYR_SHA,
        source=str(source),
        adapter=native,
        ir_sha256=identity(project),
        compiler=version,
        compiler_sha256=digest(Path(compiler)),
        case=case,
        files={p.relative_to(out).as_posix(): digest(p) for p in files},
    )
    write_json(out / "os-build.json", record)
    return record


def check_record(r):
    if (
        r["profile"] != "rv32-rtl-zephyr-v1"
        or r["source_commit"] != ZEPHYR_SHA
        or r["adapter"] != adapter_identity()
        or r["case"] not in CASES
    ):
        raise ContractError("stale RTL Zephyr software provenance")


def verify_build(project, firmware):
    r = json.loads((firmware / "os-build.json").read_text())
    check_record(r)
    verify_source(Path(r["source"]), ZEPHYR_SHA)
    if r["ir_sha256"] != identity(project):
        raise ContractError("RTL Zephyr firmware map mismatch")
    for name, sha in r["files"].items():
        if digest(inside(firmware, name)) != sha:
            raise ContractError("modified RTL Zephyr software artifact")
    validate_elf(project, firmware / "firmware.elf")
    return r


def run(
    project,
    generated,
    firmware,
    manifest,
    cpu,
    models,
    engine,
    out,
    bridge=None,
    max_cycles=5000000,
):
    record = verify_build(project, firmware)
    out = out.resolve()
    row = run_cpu(
        project,
        manifest,
        cpu,
        generated,
        models,
        engine,
        out,
        bridge=bridge,
        elf=firmware / "firmware.elf",
        markers=MARKERS,
        max_cycles=max_cycles,
        timeout=300,
    )
    fw = out / "os-firmware"
    fw.mkdir()
    for name in [*record["files"], "os-build.json"]:
        target = inside(fw, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(inside(firmware, name), target)
    result = dict(
        profile="rv32-rtl-zephyr-v1",
        os_build=record,
        os_build_sha256=digest(fw / "os-build.json"),
        rtl_report_sha256=digest(out / "report.json"),
        status=row["status"],
        scope=(
            "RV32 M-mode Zephyr kernel and native timer/PLIC/source-IP drivers; no A/FPU/MMU/Linux"
        ),
    )
    write_json(out / "os-report.json", result)
    verify(out)
    return result


def verify(out):
    r = json.loads((out / "os-report.json").read_text())
    row = verify_run(out)
    software = json.loads((out / "os-firmware/os-build.json").read_text())
    check_record(software)
    if (
        r["os_build"] != software
        or r["os_build_sha256"] != digest(out / "os-firmware/os-build.json")
        or software["ir_sha256"] != row["base_io_ir_sha256"]
        or software["files"]["firmware.elf"] != row["elf_sha256"]
        or r["rtl_report_sha256"] != digest(out / "report.json")
        or r["status"] != row["status"]
    ):
        raise ContractError("modified RTL Zephyr acceptance evidence")
    for name, sha in software["files"].items():
        if digest(inside(out / "os-firmware", name)) != sha:
            raise ContractError("modified RTL Zephyr software artifact")
    serial = (out / "uart.log").read_text()
    if r["status"] == "PASSED" and (
        not all(serial.count(m) == 1 for m in MARKERS) or "USP_TEST_FAIL" in serial
    ):
        raise ContractError("RTL Zephyr guest did not pass conformance")
    return r


def main(argv=None):
    parser = argparse.ArgumentParser(prog="usp rtl-zephyr")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build")
    p.add_argument("project", type=Path)
    for n in ("generated", "source", "out"):
        p.add_argument("--" + n, type=Path, required=True)
    p.add_argument("--case", choices=CASES, default="smoke")
    p = sub.add_parser("run")
    p.add_argument("project", type=Path)
    for n in ("generated", "firmware", "manifest", "cpu", "models", "out"):
        p.add_argument("--" + n, type=Path, required=True)
    p.add_argument("--engine", choices=("qemu", "gem5"), required=True)
    p.add_argument("--bridge", type=Path)
    sub.add_parser("verify").add_argument("out", type=Path)
    a = parser.parse_args(argv)
    try:
        if a.command == "build":
            r = build(
                load_project(a.project), a.generated.resolve(), a.source.resolve(), a.out, a.case
            )
        elif a.command == "run":
            r = run(
                load_project(a.project),
                a.generated,
                a.firmware.resolve(),
                a.manifest,
                a.cpu,
                a.models,
                a.engine,
                a.out,
                a.bridge,
            )
        else:
            r = verify(a.out.resolve())
        print(json.dumps(r, indent=2))
        return 0 if r.get("status", "PASSED") == "PASSED" else 1
    except (ContractError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(status="ERROR", message=str(exc))))
        return 2
