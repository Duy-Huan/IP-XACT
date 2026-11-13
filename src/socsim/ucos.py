"""Pinned µC/OS-II/III RV32 ports on instruction-owning RTL CPUs."""

import argparse
import json
import re
import shutil
from pathlib import Path
from string import Template

from .domain import ContractError
from .ipxact import digest
from .pipeline import execute, identity, verify_generated, verify_source
from .project import load_project
from .rtl_cpu import run_cpu, validate_elf, verify_run
from .runner import tool_version, write_json
from .unified import board_contract

RESOURCES = Path(__file__).parent / "resources/ucos"
PINS = {
    "ucos2": "bfc6120f20cb27a927a9fc5dd667f9e455495777",
    "ucos3": "9a3fc5f45d75c0b4c3b6eaecf903720c74329747",
    "uc-cpu": "ae5796e2049b043f69ef4f29fc330f29217b6344",
    "uc-lib": "71b5f65da27859028a89c4ceac1df53c880916dc",
}
CASES = {"smoke": 0, "assert-failure": 1, "tick-disabled": 2}
MARKERS = [
    "USP_UCOS_BOOT_OK",
    "USP_UCOS_TIMER_PREEMPTION_REGISTERS_OK",
    "USP_UCOS_UART_PLIC_IRQ_OK",
    "USP_CHIPYARD_GCD_IRQ_OK",
    "USP_OPENTITAN_GPIO_IRQ_OK",
    "USP_TEST_PASS",
]


def adapter_identity():
    paths = [Path(__file__), *RESOURCES.iterdir(), RESOURCES.parent / "freertos/linker.ld.in"]
    return {str(p.relative_to(RESOURCES.parent.parent)): digest(p) for p in paths if p.is_file()}


def override(text: str, name: str, value: int) -> str:
    text, count = re.subn(
        r"(#define\s+" + re.escape(name) + r"\s+)\w+",
        lambda m: str(m[1]) + str(value) + "u",
        text,
    )
    if count != 1:
        raise ContractError(f"unexpected upstream configuration declaration: {name}")
    return text


def files_verified(directory, files):
    for name, sha in files.items():
        p = directory / name
        if p.resolve().parent != directory.resolve() or digest(p) != sha:
            raise ContractError("modified µC/OS software artifact")


def build(project, generated, upstream, kernel, out, case="smoke"):
    verify_generated(project, generated)
    if kernel not in ("ucos2", "ucos3") or case not in CASES:
        raise ContractError("unsupported µC/OS kernel or conformance case")
    if any(w.base + w.size > 1 << 32 for i in project.ir.instances for w in i.windows):
        raise ContractError("µC/OS RV32 requires a 32-bit physical map")
    dependencies = [kernel] + (["uc-cpu", "uc-lib"] if kernel == "ucos3" else [])
    sources = {n: verify_source(upstream / n, PINS[n]) for n in dependencies}
    compiler, version = tool_version("riscv64-unknown-elf-gcc")
    if "14.2.0" not in version:
        raise ContractError("µC/OS RV32 requires pinned GCC 14.2.0")
    b = board_contract(project)
    if b["timebase_hz"] % 10000:
        raise ContractError("µC/OS conformance tick requires an integral 10 kHz divisor")
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = sources[kernel]
    port = source / "Ports/RISC-V/RV32/GCC"
    for path in RESOURCES.iterdir():
        if path.is_file():
            text = path.read_text()
            if path.suffix == ".in":
                text = Template(text).substitute(
                    timer_ticks=b["timebase_hz"] // 10000,
                    gcd_irq=b["cygcd_irq"],
                    gpio_irq=b["otgpio_irq"],
                    **b,
                )
            (out / path.name.removesuffix(".in")).write_text(text)
    for name in ("soc.h", "memory.ld"):
        shutil.copy2(generated / name, out / name)
    (out / "linker.ld").write_text(
        Template((RESOURCES.parent / "freertos/linker.ld.in").read_text()).substitute(
            entry=hex(b["ram_base"])
        )
    )
    cfg = (source / "Cfg/Template/os_cfg.h").read_text()
    updates = (
        dict(OS_TMR_EN=0, OS_TASK_STAT_EN=0, OS_DEBUG_EN=0, OS_TICKS_PER_SEC=10000)
        if kernel == "ucos2"
        else dict(
            OS_CFG_TMR_EN=0,
            OS_CFG_STAT_TASK_EN=0,
            OS_CFG_TASK_PROFILE_EN=0,
            OS_CFG_SCHED_ROUND_ROBIN_EN=0,
        )
    )
    for name, value in updates.items():
        cfg = override(cfg, name, value)
    (out / "os_cfg.h").write_text(cfg)
    (out / "app_cfg.h").write_text("#pragma once\n")
    asm = (port / "os_cpu_a.S").read_text()
    if asm.count("0x44000000") != 1:
        raise ContractError("unexpected upstream software interrupt base")
    # Reviewable local adapter: upstream PRCI software-IRQ address -> IR CLINT MSIP.
    (out / "os_cpu_a.S").write_text(asm.replace("0x44000000", hex(b["clint_base"])))
    includes = [out, source / "Source", port]
    inputs = [out / "start.S", out / "app.c", out / "os_cpu_a.S", port / "os_cpu_c.c"]
    if kernel == "ucos2":
        inputs.append(source / "Source/ucos_ii.c")
    else:
        cpu, lib = sources["uc-cpu"], sources["uc-lib"]
        includes += [cpu, cpu / "RISC-V/GCC", lib]
        (out / "os_cfg_app.h").write_text(
            override(
                (source / "Cfg/Template/os_cfg_app.h").read_text(), "OS_CFG_TICK_RATE_HZ", 10000
            )
        )
        (out / "cpu_cfg.h").write_text(
            "#define CPU_CFG_TS_32_EN DEF_DISABLED\n#define CPU_CFG_TS_64_EN DEF_DISABLED\n"
            "#define CPU_CFG_NAME_EN DEF_DISABLED\n#define CPU_CFG_CACHE_MGMT_EN DEF_DISABLED\n"
        )
        shutil.copy2(lib / "Cfg/Template/lib_cfg.h", out / "lib_cfg.h")
        # This upstream port declares CLZ/CTZ assembly routines absent in cpu_a.S.
        # Select the actual upstream C implementations, with the original notice.
        header, count = re.subn(
            r"^#define\s+CPU_CFG_(LEAD|TRAIL)_ZEROS_ASM_PRESENT.*$",
            "",
            (cpu / "RISC-V/GCC/cpu.h").read_text(),
            flags=re.M,
        )
        if count != 2:
            raise ContractError("unexpected upstream zero-count implementation selection")
        (out / "cpu.h").write_text(header)
        inputs += [
            *sorted((source / "Source").glob("os_*.c")),
            cpu / "cpu_core.c",
            cpu / "RISC-V/GCC/cpu_a.S",
            *(lib / n for n in ("lib_mem.c", "lib_str.c", "lib_math.c", "lib_ascii.c")),
        ]
    flags = [
        "-march=rv32imc_zicsr_zifencei",
        "-mabi=ilp32",
        "-mcmodel=medany",
        "-msmall-data-limit=0",
        "-mno-relax",
        "-O2",
        "-ffreestanding",
        "-fno-builtin",
        "-fno-stack-protector",
        "-ffunction-sections",
        "-fdata-sections",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-DUSP_UCOS=" + kernel[-1],
        "-DUSP_TEST_CASE=" + str(CASES[case]),
        *("-I" + str(p) for p in includes),
    ]
    objects = []
    for i, path in enumerate(inputs):
        obj = out / f"{i}.o"
        execute([compiler, *flags, "-c", str(path), "-o", str(obj)], out, out / f"compile-{i}.log")
        objects.append(obj)
    execute(
        [
            compiler,
            *flags,
            "-nostdlib",
            "-nostartfiles",
            "-Wl,--no-relax,--gc-sections,--build-id=none",
            "-T",
            str(out / "linker.ld"),
            *objects,
            "-lgcc",
            "-o",
            str(out / "firmware.elf"),
        ],
        out,
        out / "link.log",
    )
    validate_elf(project, out / "firmware.elf")
    kernel_inputs = {}
    for name, repo in sources.items():
        # Retain every tracked source/header identity, including transitive includes.
        inventory = out / f"{name}-inventory.log"
        execute(["git", "ls-files"], repo, inventory)
        tracked = inventory.read_text().splitlines()
        kernel_inputs[name] = {p: digest(repo / p) for p in tracked if (repo / p).is_file()}
        shutil.copy2(repo / "LICENSE", out / f"{name}-LICENSE")
        if (repo / "NOTICE").is_file():
            shutil.copy2(repo / "NOTICE", out / f"{name}-NOTICE")
    record = dict(
        profile="rv32-rtl-ucos-v1",
        kernel=kernel,
        case=case,
        tick_hz=10000,
        sources={n: dict(path=str(p), commit=PINS[n]) for n, p in sources.items()},
        kernel_inputs=kernel_inputs,
        adapter=adapter_identity(),
        ir_sha256=identity(project),
        compiler=version,
        compiler_sha256=digest(Path(compiler)),
        flags=flags,
        files={p.name: digest(p) for p in out.iterdir() if p.is_file()},
    )
    write_json(out / "os-build.json", record)
    return record


def check_provenance(record):
    kernel = record["kernel"]
    required = {kernel} | ({"uc-cpu", "uc-lib"} if kernel == "ucos3" else set())
    if (
        kernel not in ("ucos2", "ucos3")
        or record["profile"] != "rv32-rtl-ucos-v1"
        or record["case"] not in CASES
        or record["tick_hz"] != 10000
        or record["adapter"] != adapter_identity()
        or set(record["sources"]) != required
        or any(row["commit"] != PINS[name] for name, row in record["sources"].items())
    ):
        raise ContractError("stale µC/OS software provenance")


def verify_build(project, firmware):
    r = json.loads((firmware / "os-build.json").read_text())
    check_provenance(r)
    if r["ir_sha256"] != identity(project):
        raise ContractError("µC/OS firmware IR mismatch")
    files_verified(firmware, r["files"])
    for name, row in r["sources"].items():
        repo = verify_source(Path(row["path"]), PINS[name])
        for path, sha in r["kernel_inputs"][name].items():
            if digest(repo / path) != sha:
                raise ContractError("modified µC/OS kernel input")
    validate_elf(project, firmware / "firmware.elf")
    return r


def acceptance(serial):
    pattern = (
        r"USP_UCOS_METRICS ticks=(\d+) switches=(\d+) wakes=(\d+) worker=(\d+) "
        r"uart_irqs=(\d+) gcd_irqs=(\d+) gpio_irqs=(\d+)"
    )
    matches = re.findall(pattern, serial)
    metrics = (
        dict(
            zip(
                ("ticks", "switches", "wakes", "worker", "uart_irqs", "gcd_irqs", "gpio_irqs"),
                map(int, matches[0]),
                strict=True,
            )
        )
        if len(matches) == 1
        else {}
    )
    checks = dict(
        markers=all(s in serial for s in MARKERS),
        no_guest_failure="USP_UCOS_FAIL" not in serial,
        timer_preemption=bool(metrics) and metrics["ticks"] >= 30 and metrics["switches"] >= 30,
        saved_register_worker=bool(metrics) and metrics["worker"] > 0 and metrics["wakes"] == 30,
        device_irqs=bool(metrics)
        and (metrics["uart_irqs"], metrics["gcd_irqs"], metrics["gpio_irqs"]) == (1, 4, 1),
    )
    return checks, metrics


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
    max_cycles=1500000,
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
    checks, metrics = acceptance((out / "uart.log").read_text())
    shutil.copytree(firmware, out / "os-firmware")
    result = dict(
        profile="rv32-rtl-ucos-v1",
        os_build=record,
        os_build_sha256=digest(out / "os-firmware/os-build.json"),
        checks=checks,
        metrics=metrics,
        rtl_report_sha256=digest(out / "report.json"),
        status="PASSED" if row["status"] == "PASSED" and all(checks.values()) else "FAILED",
        scope=(
            "RV32 M-mode µC/OS; CLINT/PLIC/UART and actual Chipyard GCD/OpenTitan GPIO; no MMU/FPU"
        ),
    )
    write_json(out / "os-report.json", result)
    return result


def verify(out):
    r = json.loads((out / "os-report.json").read_text())
    row = verify_run(out)
    fw = out / "os-firmware"
    software = json.loads((fw / "os-build.json").read_text())
    check_provenance(software)
    files_verified(fw, software["files"])
    checks, metrics = acceptance((out / "uart.log").read_text())
    expected = "PASSED" if row["status"] == "PASSED" and all(checks.values()) else "FAILED"
    if (
        r["os_build"] != software
        or r["os_build_sha256"] != digest(fw / "os-build.json")
        or software["ir_sha256"] != row["base_io_ir_sha256"]
        or software["files"]["firmware.elf"] != row["elf_sha256"]
        or r["rtl_report_sha256"] != digest(out / "report.json")
        or r["checks"] != checks
        or r["metrics"] != metrics
        or r["status"] != expected
    ):
        raise ContractError("modified µC/OS acceptance evidence")
    return r


def main(argv=None):
    parser = argparse.ArgumentParser(prog="usp ucos")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build")
    p.add_argument("project", type=Path)
    for name in ("generated", "upstream", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--kernel", choices=("ucos2", "ucos3"), required=True)
    p.add_argument("--case", choices=CASES, default="smoke")
    p = sub.add_parser("run")
    p.add_argument("project", type=Path)
    for name in ("generated", "firmware", "manifest", "cpu", "models", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--engine", choices=("qemu", "gem5"), required=True)
    p.add_argument("--bridge", type=Path)
    sub.add_parser("verify").add_argument("out", type=Path)
    a = parser.parse_args(argv)
    try:
        if a.command == "build":
            r = build(
                load_project(a.project),
                a.generated.resolve(),
                a.upstream.resolve(),
                a.kernel,
                a.out,
                a.case,
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
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps(dict(status="ERROR", message=str(exc))))
        return 2
