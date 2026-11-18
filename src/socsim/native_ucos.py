"""Explicit local RV64 adaptations of the pinned public µC/OS RV32 ports."""

import re
import shutil
from pathlib import Path
from string import Template

from .domain import ContractError
from .execution import execute_build
from .ipxact import digest
from .pipeline import verify_source
from .runner import tool_version, write_json
from .soc_spec import devices, selected
from .ucos import CASES, PINS, RESOURCES, override


def port_assembly(text, clint):
    """Widen registers/pointers; retain the two 32-bit CLINT MSIP writes."""
    if text.count("0x44000000") != 1:
        raise ContractError("unexpected pinned µC/OS software interrupt register")
    text = text.replace("0x44000000", hex(clint))
    mmio = []
    lines = []
    for line in text.splitlines():
        if re.match(r"\s+sw\s+", line) and "0x0(t0)" in line:
            mmio.append(line)
        else:
            line = re.sub(r"^(\s+)lw(\s+)", r"\1ld\2", line)
            line = re.sub(r"^(\s+)sw(\s+)", r"\1sd\2", line)
        line = re.sub(r"\*\s*4\b", "* 8", line)
        lines.append(line)
    if len(mmio) != 2:
        raise ContractError("unexpected pinned MSIP store layout")
    return "\n".join(lines) + "\n"


def startup():
    text = (RESOURCES / "start.S").read_text()
    text = re.sub(r"\bsw\b", "sd", text)
    text = re.sub(r"\blw\b", "ld", text)
    text = text.replace("sp, sp, -128", "sp, sp, -256").replace("sp, sp, 128", "sp, sp, 256")
    text = text.replace("124(sp)", "248(sp)").replace("t0, 4(sp)", "t0, 8(sp)")
    text = text.replace("*4(sp)", "*8(sp)").replace("t0, t0, 4", "t0, t0, 8")
    text = text.replace("0x80000003", "0x8000000000000003")
    text = text.replace("s1, 4(s0)", "s1, 8(s0)").replace("t0, 4(s0)", "t0, 8(s0)")
    return (
        "/* Local RV64 LP64 adaptation; upstream notices retained in copied port files. */\n" + text
    )


def application(spec):
    text = (RESOURCES / "app.c.in").read_text()
    text = text.replace("uart_irqs, gcd_irqs, gpio_irqs, gcd_result", "uart_irqs")
    text = text.replace(
        "volatile uint32_t loops; uint32_t seed;", "volatile uint64_t loops; uint64_t seed;"
    )
    text = text.replace("0x12340000", "0x1234567812340000ULL")
    text = text.replace("void usp_interrupt(uint32_t cause)", "void usp_interrupt(uintptr_t cause)")
    text = text.replace("0x80000007U", "0x8000000000000007ULL").replace(
        "0x8000000bU", "0x800000000000000bULL"
    )
    text = text.replace(
        "volatile uint32_t loops; uint32_t seed;", "volatile uint64_t loops; uint64_t seed;"
    )
    text = text.replace("0x12340000", "0x1234567812340000ULL")
    text = text.replace("void usp_interrupt(uint32_t cause)", "void usp_interrupt(uintptr_t cause)")
    text = text.replace("0x80000007U", "0x8000000000000007UL").replace(
        "0x8000000bU", "0x800000000000000bUL"
    )
    a = text.index("        } else if (claim==$gcd_irq)")
    b = text.index("        } else usp_fail();", a)
    text = text[:a] + text[b:]
    a = text.index("    PLIC($uart_irq*4)=1;")
    b = text.index("    UART(1)=2;", a)
    text = (
        text[:a]
        + "    PLIC($uart_irq*4)=1; PLIC(0x2000)=1u<<$uart_irq; PLIC(0x200000)=0;\n"
        + text[b:]
    )
    a = text.index("    const uint32_t vectors[4][3]")
    b = text.index("\nint main(void)", a)
    text = (
        text[:a]
        + """    puts_uart("USP_UCOS_METRICS ticks="); number(ticks);
    puts_uart(" switches="); number(switches); puts_uart(" wakes="); number(wakes);
    puts_uart(" worker="); number((uint32_t)worker.loops);
    puts_uart(" uart_irqs="); number(uart_irqs);
    puts_uart("\\nUSP_TEST_PASS\\n"); finish(0x5555);
}
"""
        + text[b:]
    )
    return Template(text).substitute(
        timer_ticks=spec["cpu"]["timebase_hz"] // 10000, uart_irq=selected(spec, "console")["irq"]
    )


def build(generated, out, upstream, kind, cross_compile, test_case="smoke"):
    from .native_software import contract, identity, verify_software

    spec, ir = contract(generated)
    if kind not in {"ucos2", "ucos3"} or test_case not in CASES:
        raise ContractError("unsupported native µC/OS software profile")
    if spec["cpu"]["timebase_hz"] % 10000:
        raise ContractError("µC/OS qualification needs an integral 10 kHz timebase divisor")
    dependencies = [kind] + (["uc-cpu", "uc-lib"] if kind == "ucos3" else [])
    repos = {n: verify_source(upstream / n, PINS[n]) for n in dependencies}
    source = repos[kind]
    port = source / "Ports/RISC-V/RV32/GCC"
    compiler, version = tool_version(cross_compile + "gcc")
    if "14.2.0" not in version:
        raise ContractError("native µC/OS requires the pinned GCC 14.2.0")
    before = identity()
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    original = {}
    for name in ("os_cpu.h", "os_cpu_c.c", "os_cpu_a.S"):
        original[name] = digest(port / name)
    header = (port / "os_cpu.h").read_text()
    if kind == "ucos2":
        header, n1 = re.subn(
            r"typedef\s+unsigned\s+int(\s+OS_STK;)", r"typedef unsigned long\1", header
        )
        header, n2 = re.subn(
            r"typedef\s+unsigned\s+int(\s+OS_CPU_SR;)", r"typedef unsigned long\1", header
        )
        if (n1, n2) != (1, 1):
            raise ContractError("unexpected µC/OS-II stack/status type declarations")
    (out / "os_cpu.h").write_text(header)
    text = (port / "os_cpu_c.c").read_text()
    text, count = re.subn(
        r'#include\s+(?:<ucos_ii\.h>|"\.\./\.\./\.\./\.\./Source/os\.h")',
        '#include "ucos_ii.h"' if kind == "ucos2" else '#include "os.h"',
        text,
    )
    if count != 1 or text.count("0xFFFFFFF0u") != 1:
        raise ContractError("unexpected µC/OS stack initializer/include")
    text = text.replace("0xFFFFFFF0u", "0xFFFFFFFFFFFFFFF0ULL")
    (out / "os_cpu_c.c").write_text(text)
    (out / "os_cpu_a.S").write_text(
        port_assembly((port / "os_cpu_a.S").read_text(), devices(spec, "clint")[0]["base"])
    )
    (out / "start.S").write_text(startup())
    (out / "app.c").write_text(application(spec))
    (out / "soc.h").write_text(
        (generated / "firmware/soc.h").read_text() + "\n#define USP_UART_BASE USP_CONSOLE_BASE\n"
    )
    ram = selected(spec, "ram")
    (out / "memory.ld").write_text(
        f"MEMORY {{ RAM (rwx) : ORIGIN = {hex(spec['boot']['entry'])}, "
        f"LENGTH = {hex(ram['base'] + ram['size'] - spec['boot']['entry'])} }}\n"
    )
    (out / "linker.ld").write_text(
        Template((RESOURCES.parent / "freertos/linker.ld.in").read_text()).substitute(
            entry=hex(spec["boot"]["entry"])
        )
    )
    cfg = (source / "Cfg/Template/os_cfg.h").read_text()
    updates = (
        dict(OS_TMR_EN=0, OS_TASK_STAT_EN=0, OS_DEBUG_EN=0, OS_TICKS_PER_SEC=10000)
        if kind == "ucos2"
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
    inputs = [out / "start.S", out / "app.c", out / "os_cpu_a.S", out / "os_cpu_c.c"]
    includes = [out, source / "Source"]
    if kind == "ucos2":
        local_kernel = out / "kernel"
        shutil.copytree(source / "Source", local_kernel)
        memory_source = local_kernel / "os_mem.c"
        memory_text = memory_source.read_text()
        if memory_text.count("(INT32U)addr") != 1:
            raise ContractError("unexpected µC/OS-II pointer alignment implementation")
        memory_source.write_text(memory_text.replace("(INT32U)addr", "(OS_STK)addr"))
        original["Source/os_mem.c"] = digest(source / "Source/os_mem.c")
        includes = [out, local_kernel]
        inputs.append(local_kernel / "ucos_ii.c")
    else:
        cpu, lib = repos["uc-cpu"], repos["uc-lib"]
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
        cpu_header = (cpu / "RISC-V/GCC/cpu.h").read_text()
        cpu_header, count = re.subn(
            r"^#define\s+CPU_CFG_(LEAD|TRAIL)_ZEROS_ASM_PRESENT.*$", "", cpu_header, flags=re.M
        )
        if count != 2:
            raise ContractError("unexpected µC/CPU zero count implementation")
        cpu_header = re.sub(
            r"(#define\s+CPU_CFG_(?:ADDR_SIZE|DATA_SIZE)\s+)CPU_WORD_SIZE_32",
            r"\1CPU_WORD_SIZE_64",
            cpu_header,
        )
        for type_name in ("CPU_STK", "CPU_SR"):
            cpu_header, count = re.subn(
                r"typedef\s+CPU_INT32U(\s+" + type_name + r";)", r"typedef CPU_INT64U\1", cpu_header
            )
            if count != 1:
                raise ContractError("unexpected µC/CPU stack/status declaration")
        (out / "cpu.h").write_text(cpu_header)
        inputs += [
            *sorted((source / "Source").glob("os_*.c")),
            cpu / "cpu_core.c",
            cpu / "RISC-V/GCC/cpu_a.S",
            *(lib / n for n in ("lib_mem.c", "lib_str.c", "lib_math.c", "lib_ascii.c")),
        ]
    flags = [
        "-march=" + ir.cpus[0].contract.isa,
        "-mabi=lp64",
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
        "-DUSP_UCOS=" + kind[-1],
        "-DUSP_TEST_CASE=" + str(CASES[test_case]),
        *("-I" + str(p) for p in includes),
    ]
    objects = []
    for n, path in enumerate(inputs):
        obj = out / f"{n}.o"
        execute_build(
            [compiler, *flags, "-c", str(path), "-o", str(obj)], out, out / f"compile-{n}.log", 120
        )
        objects.append(str(obj))
    elf = out / "firmware.elf"
    execute_build(
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
        120,
    )
    inventories = {}
    for name, repo in repos.items():
        verify_source(repo, PINS[name])
        inventories[name] = {
            p.relative_to(repo).as_posix(): digest(p)
            for p in repo.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(repo).parts
        }
        shutil.copy2(repo / "LICENSE", out / (name + "-LICENSE"))
        if (repo / "NOTICE").exists():
            shutil.copy2(repo / "NOTICE", out / (name + "-NOTICE"))
    if before != identity():
        raise ContractError("native software adapter changed during µC/OS build")
    write_json(
        out / "port-adaptation.json",
        {
            "upstream_port": "RISC-V/RV32/GCC",
            "local_abi": "RV64 LP64 M-mode",
            "original_port_files": original,
            "sources": {n: PINS[n] for n in dependencies},
            "kernel_inputs": inventories,
            "limitations": "one hart; no FPU/vector/TLS/MMU context",
        },
    )
    record = {
        "contract": "native-software-1",
        "kind": kind,
        "test_case": test_case,
        "adapter": before,
        "generated_manifest_sha256": digest(generated / "manifest.json"),
        "native_ir_sha256": digest(generated / "native-ir.json"),
        "source_commit": PINS[kind],
        "compiler": {"version": version, "sha256": digest(Path(compiler))},
        "elf": "firmware.elf",
        "elf_sha256": digest(elf),
        "hart_count": 1,
        "timebase_hz": spec["cpu"]["timebase_hz"],
        "scope": "local RV64 LP64 µC/OS adaptation; timer/preemption/register/UART acceptance; "
        "other IP drivers need independent qualification",
        "files": {
            p.relative_to(out).as_posix(): digest(p)
            for p in out.rglob("*")
            if p.is_file() and not p.is_symlink()
        },
    }
    write_json(out / "software-build.json", record)
    return verify_software(generated, out)


def check_run(text):
    markers = (
        "USP_UCOS_BOOT_OK",
        "USP_UCOS_TIMER_PREEMPTION_REGISTERS_OK",
        "USP_UCOS_UART_PLIC_IRQ_OK",
        "USP_TEST_PASS",
    )
    match = re.search(
        r"USP_UCOS_METRICS ticks=(\d+) switches=(\d+) wakes=(\d+) worker=(\d+) uart_irqs=(\d+)",
        text,
    )
    valid = bool(
        match
        and int(match[1]) >= 30
        and int(match[2]) >= 30
        and int(match[3]) == 30
        and int(match[4]) > 0
        and int(match[5]) == 1
    )
    return {m: m in text.splitlines() for m in markers} | {
        "actual_preemption_context_irqs": valid,
        "no_failure": "USP_UCOS_FAIL" not in text,
    }
