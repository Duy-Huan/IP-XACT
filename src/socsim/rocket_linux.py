"""Verified kernel inputs, explicit Rocket boot ABI, source-built OpenSBI."""

import json
import os
import shutil
from pathlib import Path

from elftools.elf.elffile import ELFFile

from .domain import ContractError
from .ipxact import digest
from .layers import software
from .linux import LINUX_SHA, OPENSBI_SHA
from .pipeline import execute, identity, verify_source
from .project import inside
from .rocket_build import CPU_CONTRACT
from .rocket_build import verify_build as verify_cpu
from .rocket_cpu import target_ir, validate_elf
from .runner import tool_version, write_json

MARKERS = ["USP_SHARED_WORKLOAD_OK", "USP_LINUX_USERSPACE_OK"]


def adapter_identity():
    return {
        "module": digest(Path(__file__)),
        "kernel_adapter": digest(Path(__file__).with_name("linux.py")),
    }


def build(
    project,
    cpu_build,
    generated,
    linux_source,
    opensbi_source,
    out,
    memory_limit=32 * 1024**2,
    jobs=2,
):
    cpu = verify_cpu(cpu_build)
    ir = target_ir(project, cpu)
    linux_source = verify_source(linux_source, LINUX_SHA)
    opensbi_source = verify_source(opensbi_source, OPENSBI_SHA)
    _, kernel = software(project, generated, "linux")
    if kernel["linux_source_commit"] != LINUX_SHA or kernel["opensbi_source_commit"] != OPENSBI_SHA:
        raise ContractError("Rocket Linux requires the verified pinned kernel source")
    ram = cpu["ram"]
    if (
        ram["base"] % 0x200000
        or ram["size"] < 16 * 1024**2
        or type(jobs) is not int
        or not 1 <= jobs <= 4
    ):
        raise ContractError("Rocket Linux boot needs 2MiB aligned RAM, >=16MiB and 1..4 jobs")
    if memory_limit is not None and (
        type(memory_limit) is not int
        or not 16 * 1024**2 <= memory_limit <= ram["size"]
        or memory_limit % 1024**2
    ):
        raise ContractError("Linux memory limit must be explicit whole MiB within hardware RAM")
    config = set((generated / "linux/kernel/.config").read_text().splitlines())
    if (
        any(x not in config for x in ("CONFIG_64BIT=y", "CONFIG_MMU=y", "CONFIG_RISCV_SBI=y"))
        or any(x in config for x in ("CONFIG_FPU=y", "CONFIG_SMP=y"))
        or digest(generated / "linux/kernel/.config") != kernel["config_sha256"]
    ):
        raise ContractError("kernel MMU/hart/FPU configuration does not match Rocket")
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    for name, source in (
        ("Image", generated / "linux/kernel/arch/riscv/boot/Image"),
        ("linux.config", generated / "linux/kernel/.config"),
        ("init", generated / "linux/init"),
        ("init.c", generated / "linux/init.c"),
        ("workload", generated / "linux/workload"),
        ("workload.S", generated / "linux/workload.S"),
        ("Linux-COPYING", linux_source / "COPYING"),
        ("Linux-GPL-2.0", linux_source / "LICENSES/preferred/GPL-2.0"),
        ("OpenSBI-LICENSE", opensbi_source / "COPYING.BSD"),
    ):
        shutil.copy2(source, out / name)
    header = (out / "Image").read_bytes()[:64]
    if header[48:56] != b"RISCV\0\0\0":
        raise ContractError("unsupported Linux Image header")
    firmware = ram["base"] + 0x40000
    payload = ram["base"] + 0x200000
    dtb = ram["base"] + ram["size"] - 0x200000
    if payload + (out / "Image").stat().st_size >= dtb:
        raise ContractError("kernel payload overlaps reserved boot DTB")
    text = (generated / "linux.dts").read_text()
    old = 'riscv,isa = "rv64imac_zicsr_zifencei";'
    if text.count(old) != 1 or text.count('rdinit=/init";') != 1:
        raise ContractError("unsupported input Linux DT/boot contract")
    text = text.replace(
        old,
        old + ' riscv,isa-base = "rv64i"; '
        'riscv,isa-extensions = "i", "m", "a", "c", "zicsr", "zifencei";',
    )
    if memory_limit is not None:
        text = text.replace('rdinit=/init";', f'rdinit=/init mem={memory_limit // 1024**2}M";')
    (out / "soc.dts").write_text(text)
    execute(["dtc", "-I", "dts", "-O", "dtb", "-o", "soc.dtb", "soc.dts"], out, out / "dtc.log", 60)
    cross = os.environ.get("CROSS_COMPILE_LINUX", "riscv64-linux-gnu-")
    compiler, version = tool_version(cross + "gcc")
    if "14.2.0" not in version:
        raise ContractError("Rocket OpenSBI requires pinned GCC 14.2.0")
    execute(
        [
            "make",
            "-C",
            str(opensbi_source),
            "O=" + str(out / "opensbi"),
            "PLATFORM=generic",
            "CROSS_COMPILE=" + cross,
            "PLATFORM_RISCV_ISA=" + str(CPU_CONTRACT["isa"]),
            "PLATFORM_RISCV_ABI=lp64",
            "FW_TEXT_START=" + hex(firmware),
            "FW_PAYLOAD_PATH=" + str(out / "Image"),
            "FW_PAYLOAD_OFFSET=0x1c0000",
            "-j" + str(jobs),
        ],
        out,
        out / "opensbi.log",
        600,
    )
    sbi = out / "opensbi/platform/generic/firmware/fw_payload.elf"
    with sbi.open("rb") as stream:
        symbols = ELFFile(stream).get_section_by_name(".symtab")
        if symbols is None or any(
            not symbols.get_symbol_by_name(n) for n in ("_fw_start", "_fw_rw_start")
        ):
            raise ContractError("OpenSBI image is missing firmware domain symbols")
        addresses = {
            n: symbols.get_symbol_by_name(n)[0]["st_value"] for n in ("_fw_start", "_fw_rw_start")
        }
    size = addresses["_fw_rw_start"] - addresses["_fw_start"]
    if addresses["_fw_start"] != firmware or size <= 0 or size & (size - 1) or firmware % size:
        raise ContractError("OpenSBI firmware/RW domain alignment is not valid")
    boot_cross = os.environ.get("CROSS_COMPILE", "riscv64-unknown-elf-")
    boot_compiler, boot_version = tool_version(boot_cross + "gcc")
    if "14.2.0" not in boot_version:
        raise ContractError("Rocket boot trampoline requires pinned GCC 14.2.0")
    execute(
        [boot_cross + "objcopy", "-O", "binary", str(sbi), "sbi-payload.bin"],
        out,
        out / "objcopy.log",
        60,
    )
    (out / "boot.S").write_text(
        '/* SPDX-License-Identifier: Apache-2.0 */\n.section .text.boot,"ax"\n'
        ".global _start\n_start:\n csrw mie,zero\n csrw mstatus,zero\n csrr a0,mhartid\n"
        f" li a1,{hex(dtb)}\n li t0,{hex(firmware)}\n jr t0\n"
        '.section .sbi,"ax"\n.incbin "sbi-payload.bin"\n'
        '.section .dtb,"a"\n.incbin "soc.dtb"\n'
    )
    (out / "boot.ld").write_text(
        "OUTPUT_ARCH(riscv)\nENTRY(_start)\n"
        "PHDRS { sbi PT_LOAD FLAGS(7); dtb PT_LOAD FLAGS(4); boot PT_LOAD FLAGS(5); }\nSECTIONS {\n"
        f" . = {hex(firmware)}; .sbi : {{ *(.sbi) }} :sbi\n"
        f" . = {hex(dtb)}; .dtb : {{ *(.dtb) }} :dtb\n"
        f" . = {hex(ram['base'])}; .boot : {{ *(.text.boot) }} :boot\n"
        " /DISCARD/ : { *(.riscv.attributes) *(.comment) *(.note*) }\n}\n"
    )
    execute(
        [
            boot_compiler,
            "-march=" + str(CPU_CONTRACT["isa"]),
            "-mabi=lp64",
            "-mcmodel=medany",
            "-mno-relax",
            "-nostdlib",
            "-nostartfiles",
            "-Wl,--build-id=none",
            "-T",
            "boot.ld",
            "boot.S",
            "-o",
            "firmware.elf",
        ],
        out,
        out / "link.log",
        60,
    )
    validate_elf(out / "firmware.elf", ram)
    verify_source(opensbi_source, OPENSBI_SHA)
    write_json(out / "soc-ir.json", ir.to_dict())
    write_json(out / "kernel-origin.json", kernel)
    files = [p for p in out.iterdir() if p.is_file()]
    record = {
        "contract": "rocket-linux-boot-v1",
        "kind": "linux",
        "case": "smoke",
        "adapter_identity": adapter_identity(),
        "base_io_ir_sha256": identity(project),
        "cpu_build_sha256": digest(cpu_build / "rocket-build.json"),
        "linux_source_commit": LINUX_SHA,
        "opensbi_source_commit": OPENSBI_SHA,
        "compiler_sha256": digest(Path(compiler)),
        "boot_compiler_sha256": digest(Path(boot_compiler)),
        "hardware_ram": ram,
        "software_memory_limit_bytes": memory_limit,
        "boot": {
            "reset": ram["base"],
            "opensbi": firmware,
            "linux": payload,
            "dtb": dtb,
            "arguments": "a0=actual mhartid; a1=owned DTB; M -> S -> U",
        },
        "expected_markers": MARKERS,
        "kernel_reuse": "verified original Image/config/init/workload",
        "files": {p.relative_to(out).as_posix(): digest(p) for p in files},
    }
    write_json(out / "software-build.json", record)
    return record


def verify_build(project, cpu_build, out):
    cpu = verify_cpu(cpu_build)
    target_ir(project, cpu)
    record = json.loads((out / "software-build.json").read_text())
    if (
        record["contract"] != "rocket-linux-boot-v1"
        or record["kind"] != "linux"
        or record["case"] != "smoke"
        or record["adapter_identity"] != adapter_identity()
        or record["base_io_ir_sha256"] != identity(project)
        or record["cpu_build_sha256"] != digest(cpu_build / "rocket-build.json")
        or record["linux_source_commit"] != LINUX_SHA
        or record["opensbi_source_commit"] != OPENSBI_SHA
        or record["hardware_ram"] != cpu["ram"]
        or record["expected_markers"] != MARKERS
    ):
        raise ContractError("stale/mixed Rocket Linux CPU/kernel/map identity")
    required = {
        "Image",
        "firmware.elf",
        "soc-ir.json",
        "soc.dtb",
        "soc.dts",
        "boot.S",
        "boot.ld",
        "sbi-payload.bin",
        "kernel-origin.json",
        "init",
        "workload",
        "linux.config",
    }
    if not required.issubset(record["files"]):
        raise ContractError("incomplete Rocket Linux boot dependency closure")
    for name, sha in record["files"].items():
        if digest(inside(out, name)) != sha:
            raise ContractError("modified Rocket Linux boot/kernel artifact")
    kernel = json.loads((out / "kernel-origin.json").read_text())
    if (
        kernel["linux_source_commit"] != LINUX_SHA
        or kernel["opensbi_source_commit"] != OPENSBI_SHA
        or any(
            digest(out / name) != kernel[key]
            for name, key in (
                ("Image", "image_sha256"),
                ("linux.config", "config_sha256"),
                ("init", "init_sha256"),
                ("workload", "workload_sha256"),
            )
        )
        or json.loads((out / "soc-ir.json").read_text())
        != json.loads(json.dumps(target_ir(project, cpu).to_dict()))
    ):
        raise ContractError("mixed Rocket Linux image/config/userspace/IR provenance")
    limit = record["software_memory_limit_bytes"]
    if limit is not None and (
        type(limit) is not int or not 16 * 1024**2 <= limit <= cpu["ram"]["size"] or limit % 1024**2
    ):
        raise ContractError("invalid Rocket Linux software memory profile")
    validate_elf(out / "firmware.elf", cpu["ram"])
    return record
