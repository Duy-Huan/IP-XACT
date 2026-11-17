"""Direct OpenSBI/Linux bring-up. This is separate from a Yocto image build."""

import os
from pathlib import Path

from .domain import ContractError
from .ipxact import digest
from .pipeline import execute, verify_generated, verify_source
from .runner import write_json

LINUX_SHA = "adc218676eef25575469234709c2d87185ca223a"
OPENSBI_SHA = "bd613dd92113f683052acfb23d9dc8ba60029e0a"


def build_linux(project, out, linux_source, opensbi_source, cross_compile, jobs=4):
    verify_generated(project, out)
    if project.manifest["profile"] not in {"usp-rv64-v1", "usp-rv64-ips-v1"}:
        raise ContractError("Linux requires the non-hybrid base or source-IP profile")
    return build_payload(
        out,
        linux_source,
        opensbi_source,
        cross_compile,
        jobs,
        project.ir.cpu.entry,
        out / "linux.dts",
    )


def build_payload(out, linux_source, opensbi_source, cross_compile, jobs, entry, dts, smp=False):
    """Shared pinned kernel/OpenSBI build, independent of a hardware frontend."""
    linux_source = verify_source(linux_source, LINUX_SHA)
    opensbi_source = verify_source(opensbi_source, OPENSBI_SHA)
    directory = out / "linux"
    directory.mkdir(exist_ok=True)
    templates = Path(__file__).parent / "templates"
    probe = directory / "linker-probe.c"
    probe.write_text("int usp_linker_probe;\n")
    execute(
        [cross_compile + "gcc", "-c", str(probe), "-o", str(directory / "linker-probe.o")],
        out,
        out / "logs/linux-linker-probe-compile.log",
    )
    try:
        execute(
            [
                cross_compile + "ld",
                "-shared",
                str(directory / "linker-probe.o"),
                "-o",
                str(directory / "linker-probe.so"),
            ],
            out,
            out / "logs/linux-linker-probe.log",
        )
    except ContractError as exc:
        raise ContractError(
            "Linux vDSO requires a shared-capable linker; use the Linux GNU toolchain"
        ) from exc
    init = directory / "init.c"
    init.write_text((templates / "linux-init.c.in").read_text())
    workload = directory / "workload.S"
    workload.write_text((templates / "shared.S.in").read_text())
    flags = [
        "-march=rv64imac",
        "-mabi=lp64",
        "-nostdlib",
        "-static",
        "-fno-builtin",
        "-fno-stack-protector",
        "-msmall-data-limit=0",
        "-mno-relax",
        "-Wl,--no-relax",
        "-Wl,--build-id=none",
    ]
    for source, output in [(init, "init"), (workload, "workload")]:
        execute(
            [cross_compile + "gcc", *flags, str(source), "-o", str(directory / output)],
            out,
            out / ("logs/linux-" + output + "-build.log"),
        )
    # Declare the generated Linux syscall ABI explicitly; do not rely on gem5's OS fallback.
    for output in ("init", "workload"):
        execute(
            [cross_compile + "elfedit", "--output-osabi", "Linux", str(directory / output)],
            out,
            out / ("logs/linux-" + output + "-abi.log"),
        )
    cpio_list = directory / "initramfs.list"
    cpio_list.write_text(
        "dir /dev 0755 0 0\nnod /dev/console 0600 0 0 c 5 1\n"
        f"file /init {directory / 'init'} 0755 0 0\n"
        f"file /workload {directory / 'workload'} 0755 0 0\n"
    )
    build = directory / "kernel"
    build.mkdir(exist_ok=True)
    make = [
        "make",
        "-C",
        str(linux_source),
        "O=" + str(build),
        "ARCH=riscv",
        "CROSS_COMPILE=" + cross_compile,
    ]
    env = os.environ.copy()
    env.update(
        KBUILD_BUILD_TIMESTAMP="2026-11-11",
        KBUILD_BUILD_USER="usp",
        KBUILD_BUILD_HOST="reproducible",
    )
    if os.environ.get("USP_HOST_PREFIX"):
        env["BISON_PKGDATADIR"] = os.environ["USP_HOST_PREFIX"] + "/usr/share/bison"
        env["M4"] = os.environ["USP_HOST_PREFIX"] + "/usr/bin/m4"
    execute([*make, "tinyconfig"], out, out / "logs/linux-configure.log", env=env)
    symbols = [
        "64BIT",
        "MMU",
        "RISCV_SBI",
        "PRINTK",
        "TTY",
        "SERIAL_8250",
        "SERIAL_8250_CONSOLE",
        "SERIAL_OF_PLATFORM",
        "SERIAL_EARLYCON",
        "BINFMT_ELF",
        "BLK_DEV_INITRD",
        "RD_GZIP",
        "DEVTMPFS",
        "DEVTMPFS_MOUNT",
        "POWER_RESET",
        "POWER_RESET_SYSCON",
        "SIFIVE_PLIC",
        "RISCV_TIMER",
        "FUTEX",
        "MULTIUSER",
    ]
    configure = [str(linux_source / "scripts/config"), "--file", str(build / ".config")]
    for symbol in symbols:
        configure += ["--enable", symbol]
    configure += [
        "--set-str",
        "INITRAMFS_SOURCE",
        str(cpio_list),
        "--enable" if smp else "--disable",
        "SMP",
        "--disable",
        "WERROR",
        "--disable",
        "DEBUG_INFO",
    ]
    execute(configure, out, out / "logs/linux-options.log", env=env)
    execute([*make, "olddefconfig"], out, out / "logs/linux-olddefconfig.log", env=env)
    execute([*make, "-j", str(jobs), "Image"], out, out / "logs/linux-build.log", 1800, env)
    image = build / "arch/riscv/boot/Image"
    execute(
        ["dtc", "-I", "dts", "-O", "dtb", "-o", str(directory / "soc.dtb"), str(dts)],
        out,
        out / "logs/linux-dtc.log",
    )
    sbi_build = directory / "opensbi"
    execute(
        [
            "make",
            "-C",
            str(opensbi_source),
            "O=" + str(sbi_build),
            "PLATFORM=generic",
            "CROSS_COMPILE=" + cross_compile,
            "FW_TEXT_START=" + hex(entry),
            "FW_PAYLOAD_PATH=" + str(image),
            "FW_PAYLOAD_OFFSET=0x200000",
            "-j",
            str(jobs),
        ],
        out,
        out / "logs/opensbi-build.log",
        600,
        env,
    )
    firmware = sbi_build / "platform/generic/firmware/fw_payload.elf"
    write_json(
        out / "linux-build.json",
        {
            "generated_manifest_sha256": digest(out / "manifest.json"),
            "linux_source_commit": LINUX_SHA,
            "opensbi_source_commit": OPENSBI_SHA,
            "elf_sha256": digest(firmware),
            "image_sha256": digest(image),
            "dtb_sha256": digest(directory / "soc.dtb"),
            "init_sha256": digest(directory / "init"),
            "workload_sha256": digest(directory / "workload"),
            "config_sha256": digest(build / ".config"),
        },
    )
    return firmware
