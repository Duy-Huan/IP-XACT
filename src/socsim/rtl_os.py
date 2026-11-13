"""Source-built RV32 FreeRTOS executed by actual external RTL CPUs."""

import argparse
import json
import shutil
from pathlib import Path
from string import Template

from .domain import ContractError
from .freertos import FREERTOS_SHA, MARKERS, RESOURCES, TEST_CASES, acceptance, adapter_identity
from .ipxact import digest
from .pipeline import execute, identity, verify_generated, verify_source
from .project import load_project
from .rtl_cpu import run_cpu, validate_elf, verify_run
from .runner import tool_version, write_json
from .unified import board_contract


def build(project, generated, source, out, cross_compile="riscv64-unknown-elf-", case="smoke"):
    verify_generated(project, generated)
    source = verify_source(source, FREERTOS_SHA)
    if case not in TEST_CASES:
        raise ContractError("unknown RTOS conformance case")
    board = board_contract(project)
    if any(w.base + w.size > 1 << 32 for i in project.ir.instances for w in i.windows):
        raise ContractError("RV32 RTOS requires a 32-bit physical address map")
    compiler, version = tool_version(cross_compile + "gcc")
    if "14.2.0" not in version:
        raise ContractError("RV32 FreeRTOS requires pinned GCC 14.2.0")
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    values = dict(
        timebase_hz=board["timebase_hz"],
        mtime=hex(board["clint_base"] + 0xBFF8),
        mtimecmp=hex(board["clint_base"] + 0x4000),
        uart_irq=board["uart_irq"],
        entry=hex(board["ram_base"]),
    )
    for resource in RESOURCES.iterdir():
        if resource.is_file():
            text = resource.read_text()
            if resource.suffix == ".in":
                text = Template(text).substitute(values)
            if resource.name == "app.c.in":
                if text.count("1UL << 63") != 2:
                    raise ContractError("unexpected upstream acceptance application")
                text = text.replace("1UL << 63", "1UL << 31")
            if resource.name == "FreeRTOSConfig.h.in":
                text = text.replace("TICK_TYPE_WIDTH_64_BITS", "TICK_TYPE_WIDTH_32_BITS")
                text = text.replace("configTICK_RATE_HZ 1000", "configTICK_RATE_HZ 10000")
                text = text.replace("RV64", "RV32")
            if resource.name == "start.S":
                text = text.replace(
                    "_start:\n",
                    "_start:\n    .option push\n    .option norvc\n"
                    "    j rtl_reset\n    .space 124\n    .option pop\nrtl_reset:\n",
                )
                text = text.replace("sd ", "sw ").replace("ld ", "lw ")
                text = text.replace("addi t0, t0, 8", "addi t0, t0, 4")
                text = text.replace("8(s0)", "4(s0)")
                text = text.replace(
                    "la t0, freertos_risc_v_trap_handler", "la t0, rtl_vectors\n    ori t0, t0, 1"
                )
                text += (
                    '\n.section .text.rtl_vectors,"ax"\n.balign 256\n.option norvc\n'
                    "rtl_vectors:\n.rept 32\n    j freertos_risc_v_trap_handler\n.endr\n"
                )
            (out / resource.name.removesuffix(".in")).write_text(text)
    for name in ("soc.h", "memory.ld"):
        shutil.copy2(generated / name, out / name)
    shutil.copy2(source / "LICENSE.md", out / "FreeRTOS-LICENSE.md")
    port = source / "portable/GCC/RISC-V"
    # RV32 explicitly narrows the upstream 64-bit MTIMECMP address through
    # uintptr_t. Keep the pinned kernel clean and retain both source identities.
    original_port = port / "port.c"
    text = original_port.read_text()
    old = "( volatile uint64_t * ) ( ullMachineTimerCompareRegisterBase"
    if text.count(old) != 1:
        raise ContractError("unexpected upstream timer pointer declaration")
    (out / "port.c").write_text(
        text.replace(
            old, "( volatile uint64_t * ) ( uintptr_t ) ( ullMachineTimerCompareRegisterBase"
        )
    )
    sources = [
        out / "start.S",
        out / "app.c",
        out / "memory.c",
        *(source / n for n in ("tasks.c", "queue.c", "list.c")),
        out / "port.c",
        port / "portASM.S",
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
        "-DUSP_TEST_CASE=" + str(TEST_CASES[case]),
        *(
            "-I" + str(p)
            for p in (
                out,
                source / "include",
                port,
                port / "chip_specific_extensions/RISCV_MTIME_CLINT_no_extensions",
            )
        ),
    ]
    objects = []
    for index, path in enumerate(sources):
        obj = out / f"{index}.o"
        execute(
            [compiler, *flags, "-c", str(path), "-o", str(obj)], out, out / f"compile-{index}.log"
        )
        objects.append(obj)
    elf = out / "firmware.elf"
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
            str(elf),
        ],
        out,
        out / "link.log",
    )
    validate_elf(project, elf)
    record = dict(
        profile="rv32-rtl-freertos-v1",
        kernel_commit=FREERTOS_SHA,
        kernel_source=str(source),
        adapter_sha256=digest(Path(__file__)),
        shared_adapter=adapter_identity(),
        ir_sha256=identity(project),
        case=case,
        tick_hz=10000,
        compiler=version,
        compiler_sha256=digest(Path(compiler)),
        flags=flags,
        kernel_inputs={
            str(p): digest(p) for p in [*sources, original_port] if p.is_relative_to(source)
        },
        files={p.name: digest(p) for p in out.iterdir() if p.is_file()},
    )
    write_json(out / "os-build.json", record)
    return record


def verify_build(project, firmware):
    r = json.loads((firmware / "os-build.json").read_text())
    verify_source(Path(r["kernel_source"]), FREERTOS_SHA)
    if (
        r["profile"] != "rv32-rtl-freertos-v1"
        or r["kernel_commit"] != FREERTOS_SHA
        or r["adapter_sha256"] != digest(Path(__file__))
        or r["shared_adapter"] != adapter_identity()
        or r["ir_sha256"] != identity(project)
        or r["case"] not in TEST_CASES
        or r["tick_hz"] != 10000
    ):
        raise ContractError("stale RTL RTOS build identity")
    for name, sha in r["files"].items():
        p = firmware / name
        if p.resolve().parent != firmware.resolve() or digest(p) != sha:
            raise ContractError("modified RTL RTOS build artifact")
    for name, sha in r["kernel_inputs"].items():
        if digest(Path(name)) != sha:
            raise ContractError("modified kernel source")
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
    max_cycles=1000000,
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
        markers=list(MARKERS),
        max_cycles=max_cycles,
        timeout=300,
    )
    checks, metrics = acceptance((out / "uart.log").read_text())
    shutil.copytree(firmware, out / "os-firmware")
    result = dict(
        profile="rv32-rtl-freertos-v1",
        os_build=record,
        os_build_sha256=digest(out / "os-firmware/os-build.json"),
        metrics=metrics,
        checks=checks,
        rtl_report_sha256=digest(out / "report.json"),
        status="PASSED" if row["status"] == "PASSED" and all(checks.values()) else "FAILED",
        scope="RV32 M-mode FreeRTOS; no MMU/Linux or custom-IP drivers claimed",
    )
    write_json(out / "os-report.json", result)
    return result


def verify(out):
    r = json.loads((out / "os-report.json").read_text())
    row = verify_run(out)
    checks, metrics = acceptance((out / "uart.log").read_text())
    expected = "PASSED" if row["status"] == "PASSED" and all(checks.values()) else "FAILED"
    fw = out / "os-firmware"
    software = json.loads((fw / "os-build.json").read_text())
    if (
        r["os_build"] != software
        or r["os_build_sha256"] != digest(fw / "os-build.json")
        or software["kernel_commit"] != FREERTOS_SHA
        or software["profile"] != "rv32-rtl-freertos-v1"
        or software["adapter_sha256"] != digest(Path(__file__))
        or software["shared_adapter"] != adapter_identity()
        or software["ir_sha256"] != row["base_io_ir_sha256"]
        or software["files"]["firmware.elf"] != row["elf_sha256"]
    ):
        raise ContractError("modified RTL RTOS software provenance")
    for name, sha in software["files"].items():
        p = fw / name
        if p.resolve().parent != fw.resolve() or digest(p) != sha:
            raise ContractError("modified RTL RTOS software artifact")
    if (
        r["rtl_report_sha256"] != digest(out / "report.json")
        or r["checks"] != checks
        or r["metrics"] != metrics
        or r["status"] != expected
    ):
        raise ContractError("modified RTL RTOS acceptance evidence")
    return r


def main(argv=None):
    parser = argparse.ArgumentParser(prog="usp rtl-os")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build")
    p.add_argument("project", type=Path)
    p.add_argument("--generated", type=Path, required=True)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--case", choices=TEST_CASES, default="smoke")
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
                a.source.resolve(),
                a.out,
                case=a.case,
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
