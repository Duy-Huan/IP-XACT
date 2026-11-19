"""Source-built RV64IMAC kernels for the explicit Rocket physical/boot contract."""

import json
import os
import shutil
from pathlib import Path
from string import Template

from .domain import ContractError
from .freertos import FREERTOS_SHA, MARKERS, RESOURCES, TEST_CASES, adapter_identity
from .ipxact import digest
from .pipeline import execute, identity, verify_generated, verify_source
from .rocket_build import CPU_CONTRACT, verify_build
from .rocket_cpu import target_ir, validate_elf
from .runner import tool_version, write_json
from .unified import board_contract

MMU_MARKERS = [
    "USP_ROCKET_MMU_BOOT_OK",
    "USP_ROCKET_SV39_OK",
    "USP_ROCKET_USER_PROTECTION_OK",
    "USP_ROCKET_USER_ATOMIC_OK",
    "USP_TEST_PASS",
]
MMU_RESOURCE = Path(__file__).parent / "resources/rocket_software/mmu.c"


def software_identity():
    return {
        "module": digest(Path(__file__)),
        "freertos": adapter_identity(),
        "mmu_resource": digest(MMU_RESOURCE),
    }


def build_freertos(project, cpu_build, generated, source, out, case="smoke"):
    build = verify_build(cpu_build)
    ir = target_ir(project, build)
    verify_generated(project, generated)
    source = verify_source(source, FREERTOS_SHA)
    if case not in TEST_CASES:
        raise ContractError("unsupported Rocket FreeRTOS conformance case")
    b = board_contract(project)
    compiler, version = tool_version(
        os.environ.get("CROSS_COMPILE", "riscv64-unknown-elf-") + "gcc"
    )
    if "14.2.0" not in version:
        raise ContractError("Rocket FreeRTOS requires pinned GCC 14.2.0")
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    values = {
        "timebase_hz": b["timebase_hz"],
        "mtime": hex(b["clint_base"] + 0xBFF8),
        "mtimecmp": hex(b["clint_base"] + 0x4000),
        "uart_irq": b["uart_irq"],
        "entry": hex(b["ram_base"]),
    }
    for resource in RESOURCES.iterdir():
        if resource.is_file():
            text = resource.read_text()
            if resource.suffix == ".in":
                text = Template(text).substitute(values)
            (out / resource.name.removesuffix(".in")).write_text(text)
    for name in ("soc.h", "memory.ld"):
        shutil.copy2(generated / name, out / name)
    shutil.copy2(source / "LICENSE.md", out / "FreeRTOS-LICENSE.md")
    port = source / "portable/GCC/RISC-V"
    sources = [
        out / "start.S",
        out / "app.c",
        out / "memory.c",
        *(source / n for n in ("tasks.c", "queue.c", "list.c")),
        port / "port.c",
        port / "portASM.S",
    ]
    flags = [
        "-march=" + str(CPU_CONTRACT["isa"]),
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
        "-DUSP_TEST_CASE=" + str(TEST_CASES[case]),
        "-I" + str(out),
        "-I" + str(source / "include"),
        "-I" + str(port),
        "-I" + str(port / "chip_specific_extensions/RISCV_MTIME_CLINT_no_extensions"),
    ]
    objects = []
    for index, path in enumerate(sources):
        obj = out / f"{index}-{path.stem}.o"
        execute(
            [compiler, *flags, "-c", str(path), "-o", str(obj)],
            out,
            out / f"compile-{index}.log",
            60,
        )
        objects.append(str(obj))
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
        60,
    )
    validate_elf(out / "firmware.elf", build["ram"])
    write_json(out / "soc-ir.json", ir.to_dict())
    record = {
        "contract": "rocket-software-v1",
        "kind": "freertos",
        "case": case,
        "software_identity": software_identity(),
        "base_io_ir_sha256": identity(project),
        "cpu_build_sha256": digest(cpu_build / "rocket-build.json"),
        "source_commit": FREERTOS_SHA,
        "license": "MIT",
        "compiler": version,
        "compile_flags": flags,
        "expected_markers": list(MARKERS),
        "boot": "direct RAM reset, RV64IMAC LP64 M-mode, no FPU context",
        "tick_hz": 1000,
        "mtime_hz": b["timebase_hz"],
        "files": {p.relative_to(out).as_posix(): digest(p) for p in out.rglob("*") if p.is_file()},
    }
    write_json(out / "software-build.json", record)
    return record


def verify_software(project, cpu_build, out):
    from .project import inside

    build = verify_build(cpu_build)
    target_ir(project, build)
    record = json.loads((out / "software-build.json").read_text())
    if (
        record["contract"] != "rocket-software-v1"
        or record["kind"] not in {"freertos", "mmu-conformance"}
        or record["case"] not in TEST_CASES
        or record["source_commit"] != (FREERTOS_SHA if record["kind"] == "freertos" else None)
        or record["expected_markers"]
        != (list(MARKERS) if record["kind"] == "freertos" else MMU_MARKERS)
        or record["software_identity"] != software_identity()
        or record["base_io_ir_sha256"] != identity(project)
        or record["cpu_build_sha256"] != digest(cpu_build / "rocket-build.json")
    ):
        raise ContractError("mixed/stale Rocket software source/CPU/map identity")
    required = {"firmware.elf", "soc-ir.json"}
    if record["kind"] == "freertos":
        required.add("FreeRTOS-LICENSE.md")
    if not required.issubset(record["files"]):
        raise ContractError("incomplete Rocket kernel evidence")
    for name, sha in record["files"].items():
        if digest(inside(out, name)) != sha:
            raise ContractError("modified Rocket kernel/BSP artifact")
    validate_elf(out / "firmware.elf", build["ram"])
    return record


def build_mmu(project, cpu_build, out):
    from .rocket_build import RESOURCES as CPU_RESOURCES
    from .rocket_cpu import unmapped_test_address

    build = verify_build(cpu_build)
    ir = target_ir(project, build)
    b = board_contract(project)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    layers = CPU_RESOURCES.parent / "layers"
    shutil.copy2(MMU_RESOURCE, out / "firmware.c")
    shutil.copy2(layers / "start.S", out / "start.S")
    shutil.copy2(layers / "firmware.ld.in", out / "firmware.ld")
    (out / "soc.h").write_text(
        "#include <stdint.h>\n"
        + f"#define USP_FAULT_ADDRESS {unmapped_test_address(project.ir)}UL\n"
        + "".join(
            f"#define USP_{n.upper()}_BASE 0x{b[n + '_base']:x}UL\n" for n in ("uart", "finisher")
        )
    )
    (out / "memory.ld").write_text(
        f"MEMORY {{ RAM (rwx) : ORIGIN = 0x{b['ram_base']:x}, LENGTH = 0x{b['ram_size']:x} }}\n"
    )
    compiler, version = tool_version(
        os.environ.get("CROSS_COMPILE", "riscv64-unknown-elf-") + "gcc"
    )
    flags = [
        "-march=" + str(CPU_CONTRACT["isa"]),
        "-mabi=lp64",
        "-mcmodel=medany",
        "-msmall-data-limit=0",
        "-mno-relax",
        "-O2",
        "-ffreestanding",
        "-nostdlib",
        "-nostartfiles",
        "-Wall",
        "-Werror",
        "-Wl,--build-id=none",
    ]
    execute(
        [compiler, *flags, "-T", "firmware.ld", "start.S", "firmware.c", "-o", "firmware.elf"],
        out,
        out / "compile.log",
        60,
    )
    validate_elf(out / "firmware.elf", build["ram"])
    write_json(out / "soc-ir.json", ir.to_dict())
    record = {
        "contract": "rocket-software-v1",
        "kind": "mmu-conformance",
        "case": "smoke",
        "software_identity": software_identity(),
        "base_io_ir_sha256": identity(project),
        "cpu_build_sha256": digest(cpu_build / "rocket-build.json"),
        "source_commit": None,
        "license": "Apache-2.0",
        "compiler": version,
        "compile_flags": flags,
        "expected_markers": MMU_MARKERS,
        "boot": "M-mode RAM reset -> PMP -> Sv39 S-mode -> user-mode -> M-mode ecall",
        "files": {p.relative_to(out).as_posix(): digest(p) for p in out.rglob("*") if p.is_file()},
    }
    write_json(out / "software-build.json", record)
    return record
