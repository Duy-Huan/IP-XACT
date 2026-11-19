"""Original Rocket RV64IMAC Zephyr BSP, source-built without an ISS board."""

import json
import os
import shutil
from pathlib import Path
from string import Template

from .domain import ContractError
from .ipxact import digest
from .pipeline import ZEPHYR_SHA, execute, identity, verify_generated, verify_source
from .project import inside
from .rocket_build import verify_build as verify_cpu
from .rocket_cpu import target_ir, validate_elf
from .runner import tool_version, write_json
from .unified import MARKERS, board_contract

RESOURCES = Path(__file__).parent / "resources/rocket_software/zephyr"
CASES = ("smoke", "tick-disabled")


def adapter_identity():
    return {
        "module": digest(Path(__file__)),
        "resources": {
            p.relative_to(RESOURCES).as_posix(): digest(p)
            for p in RESOURCES.rglob("*")
            if p.is_file()
        },
    }


def build(project, cpu_build, generated, source, out, case="smoke", jobs=2):
    cpu = verify_cpu(cpu_build)
    ir = target_ir(project, cpu)
    verify_generated(project, generated)
    source = verify_source(source, ZEPHYR_SHA)
    if case not in CASES or type(jobs) is not int or not 1 <= jobs <= 4:
        raise ContractError("unsupported Rocket Zephyr build case/concurrency")
    b = board_contract(project)
    compiler, version = tool_version(
        os.environ.get("CROSS_COMPILE", "riscv64-unknown-elf-") + "gcc"
    )
    if "14.2.0" not in version:
        raise ContractError("Rocket Zephyr requires pinned GCC 14.2.0")
    native = adapter_identity()
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    board = out / "board-root"
    for p in RESOURCES.rglob("*"):
        if p.is_file():
            target = board / p.relative_to(RESOURCES).as_posix().removesuffix(".in")
            target.parent.mkdir(parents=True, exist_ok=True)
            text = p.read_text()
            target.write_text(Template(text).substitute(b) if p.suffix == ".in" else text)
    dts = (generated / "zephyr/boards/usp/usp_rv64/usp_rv64.dts").read_text()
    for old, new in (
        ("Generated from IR SHA256", "Rocket derived from base I/O IR SHA256"),
        ("USP generated RV64", "Generated Rocket RV64IMAC"),
        ("socsim,usp-rv64", "socsim,usp-rocket-rv64"),
        ("qemu,riscv-virt", "socsim,usp-rocket-rv64"),
    ):
        if old not in dts:
            raise ContractError("unexpected generated Rocket Zephyr I/O contract: " + old)
        dts = dts.replace(old, new)
    (board / "boards/usp/usp_rocket_rv64/usp_rocket_rv64.dts").write_text(dts)
    shutil.copytree(generated / "zephyr/dts", board / "dts")
    shutil.copytree(generated / "app", out / "app")
    if case == "tick-disabled":
        app = out / "app/main.c"
        text = app.read_text()
        old = "k_timer_start(&timer, K_MSEC(20), K_NO_WAIT);"
        if text.count(old) != 1:
            raise ContractError("unexpected Zephyr timer conformance application")
        app.write_text(text.replace(old, "irq_disable(7); " + old))
    env = dict(
        os.environ,
        ZEPHYR_BASE=str(source),
        ZEPHYR_TOOLCHAIN_VARIANT="cross-compile",
        CROSS_COMPILE=compiler.removesuffix("gcc"),
        XDG_CACHE_HOME=str(out / "cache"),
    )
    ninja = shutil.which("ninja")
    if not ninja:
        raise ContractError("ninja is required for the source-built Rocket kernel")
    execute(
        [
            "west",
            "build",
            "-s",
            str(out / "app"),
            "-d",
            str(out / "zephyr-build"),
            "-b",
            "usp_rocket_rv64",
            "-o=-j" + str(jobs),
            "--",
            "-DBOARD_ROOT=" + str(board),
            "-DSOC_ROOT=" + str(board),
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
    validate_elf(out / "firmware.elf", cpu["ram"])
    config = (out / "zephyr.config").read_text()
    if (
        any(
            x not in config
            for x in (
                "CONFIG_RISCV_ISA_RV64I=y",
                "CONFIG_64BIT=y",
                "CONFIG_RISCV_ISA_EXT_A=y",
                "CONFIG_SOC_USP_ROCKET_RV64=y",
            )
        )
        or "CONFIG_FPU=y" in config
        or "CONFIG_CPU_HAS_FPU=y" in config
    ):
        raise ContractError("Rocket Zephyr kernel ISA/context differs from RV64IMAC")
    verify_source(source, ZEPHYR_SHA)
    if native != adapter_identity():
        raise ContractError("Rocket Zephyr adapter changed during compilation")
    write_json(out / "soc-ir.json", ir.to_dict())
    files = [p for d in (board, out / "app") for p in d.rglob("*") if p.is_file()]
    files += [p for p in out.iterdir() if p.is_file()]
    record = {
        "contract": "rocket-zephyr-v1",
        "kind": "zephyr",
        "case": case,
        "source_commit": ZEPHYR_SHA,
        "source": str(source),
        "adapter_identity": native,
        "base_io_ir_sha256": identity(project),
        "cpu_build_sha256": digest(cpu_build / "rocket-build.json"),
        "compiler": version,
        "compiler_sha256": digest(Path(compiler)),
        "expected_markers": MARKERS,
        "boot": "RV64IMAC direct M-mode RAM reset; no FPU",
        "files": {p.relative_to(out).as_posix(): digest(p) for p in files},
    }
    write_json(out / "software-build.json", record)
    return record


def verify_build(project, cpu_build, out):
    cpu = verify_cpu(cpu_build)
    target_ir(project, cpu)
    record = json.loads((out / "software-build.json").read_text())
    if (
        record["contract"] != "rocket-zephyr-v1"
        or record["kind"] != "zephyr"
        or record["case"] not in CASES
        or record["source_commit"] != ZEPHYR_SHA
        or record["adapter_identity"] != adapter_identity()
        or record["base_io_ir_sha256"] != identity(project)
        or record["cpu_build_sha256"] != digest(cpu_build / "rocket-build.json")
        or record["expected_markers"] != MARKERS
    ):
        raise ContractError("mixed/stale Rocket Zephyr source/CPU/map identity")
    verify_source(Path(record["source"]), ZEPHYR_SHA)
    if not {
        "firmware.elf",
        "soc-ir.json",
        "Zephyr-LICENSE",
        "zephyr.config",
        "zephyr.dts",
    }.issubset(record["files"]):
        raise ContractError("incomplete Rocket Zephyr BSP evidence")
    for name, sha in record["files"].items():
        if digest(inside(out, name)) != sha:
            raise ContractError("modified Rocket Zephyr kernel/BSP artifact")
    validate_elf(out / "firmware.elf", cpu["ram"])
    return record
