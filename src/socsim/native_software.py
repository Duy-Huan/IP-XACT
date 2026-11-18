"""IR-derived RV64 software BSPs, independent of legacy instance names."""

import json
import os
import shutil
from pathlib import Path
from string import Template

from .domain import ContractError
from .execution import execute_build
from .freertos import FREERTOS_SHA, TEST_CASES, acceptance
from .freertos import RESOURCES as FREERTOS_RESOURCES
from .ipxact import digest
from .native_ir import read_ir
from .pipeline import ZEPHYR_SHA, verify_source
from .project import inside
from .runner import tool_version, write_json
from .soc_native import verify_generated
from .soc_spec import devices, selected

ROOT = Path(__file__).parent
ZEPHYR_MARKERS = ("USP_ZEPHYR_BOOT_OK", "USP_TIMER_IRQ_OK", "USP_UART_PLIC_IRQ_OK", "USP_TEST_PASS")


def identity():
    paths = [
        Path(__file__),
        ROOT / "native_ucos.py",
        ROOT / "templates/app.c.in",
        *FREERTOS_RESOURCES.iterdir(),
        *(ROOT / "resources/ucos").iterdir(),
    ]
    return {str(p.relative_to(ROOT)): digest(p) for p in paths if p.is_file()}


def contract(generated):
    spec = verify_generated(generated)
    ir = read_ir(generated / "native-ir.json")
    if len(ir.cpus) != 1:
        raise ContractError("native RTOS/Zephyr BSP currently requires exactly one RV64 hart")
    return spec, ir


def freertos_bsp(generated, out, spec):
    clint = devices(spec, "clint")[0]["base"]
    values = {
        "timebase_hz": spec["cpu"]["timebase_hz"],
        "mtime": hex(clint + 0xBFF8),
        "mtimecmp": hex(clint + 0x4000),
        "entry": hex(spec["boot"]["entry"]),
        "uart_irq": selected(spec, "console")["irq"],
    }
    bsp = out / "bsp"
    bsp.mkdir()
    for p in FREERTOS_RESOURCES.iterdir():
        if p.is_file():
            target = bsp / (p.stem if p.suffix == ".in" else p.name)
            target.write_text(
                Template(p.read_text()).substitute(values) if p.suffix == ".in" else p.read_text()
            )
    (bsp / "soc.h").write_text(
        (generated / "firmware/soc.h").read_text() + "\n#define USP_UART_BASE USP_CONSOLE_BASE\n"
    )
    ram = selected(spec, "ram")
    (bsp / "memory.ld").write_text(
        f"MEMORY {{ RAM (rwx) : ORIGIN = {hex(spec['boot']['entry'])}, "
        f"LENGTH = {hex(ram['base'] + ram['size'] - spec['boot']['entry'])} }}\n"
    )
    return bsp


def zephyr_bsp(out, spec):
    board = out / "zephyr/boards/usp/usp_native"
    board.mkdir(parents=True)
    ram, uart = selected(spec, "ram"), selected(spec, "console")
    clint, plic = (devices(spec, k)[0] for k in ("clint", "plic"))

    def cells(value):
        return f"{value >> 32:#x} {value & 0xFFFFFFFF:#x}"

    def reg(base, size):
        return f"<{cells(base)} {cells(size)}>"

    dts = f"""/dts-v1/;
/ {{ #address-cells = <2>; #size-cells = <2>;
    model = "Native IR RV64"; compatible = "socsim,native-rv64";
    chosen {{ zephyr,console = &uart0; zephyr,sram = &ram0; }};
    cpus {{ #address-cells = <1>; #size-cells = <0>;
        timebase-frequency = <{spec["cpu"]["timebase_hz"]}>;
        cpu@0 {{ device_type = "cpu"; reg = <0>; status = "okay";
            compatible = "qemu,riscv-virt", "riscv";
            riscv,isa = "rv64imac_zicsr_zifencei";
            hlic: interrupt-controller {{ compatible = "riscv,cpu-intc";
                #address-cells = <0>; #interrupt-cells = <1>; interrupt-controller; }};
        }};
    }};
    ram0: memory@{spec["boot"]["entry"]:x} {{ device_type = "memory";
        reg = {reg(spec["boot"]["entry"], ram["base"] + ram["size"] - spec["boot"]["entry"])}; }};
    uart0: uart@{uart["base"]:x} {{ compatible = "ns16550";
        reg = {reg(uart["base"], uart["size"])}; clock-frequency = <3686400>;
        current-speed = <115200>; reg-shift = <0>;
        interrupt-parent = <&plic>; interrupts = <{uart["irq"]} 1>; status = "okay"; }};
    plic: interrupt-controller@{plic["base"]:x} {{ compatible = "sifive,plic-1.0.0";
        reg = {reg(plic["base"], plic["size"])}; #address-cells = <0>;
        #interrupt-cells = <2>; interrupt-controller;
        riscv,max-priority = <7>; riscv,ndev = <31>;
        interrupts-extended = <&hlic 11 &hlic 9>; }};
    mtimer: timer@{clint["base"] + 0xBFF8:x} {{ compatible = "riscv,machine-timer";
        reg = <{cells(clint["base"] + 0xBFF8)} 0 8 {cells(clint["base"] + 0x4000)} 0 8>;
        reg-names = "mtime", "mtimecmp"; interrupts-extended = <&hlic 7>; }};
}};
"""
    (board / "usp_native.dts").write_text(dts)
    (board / "board.yml").write_text(
        "board:\n  name: usp_native\n  vendor: others\n  socs:\n    - name: qemu_virt_riscv64\n"
    )
    (board / "Kconfig.usp_native").write_text(
        "config BOARD_USP_NATIVE\n\tselect SOC_QEMU_VIRT_RISCV64\n"
    )
    (board / "Kconfig.defconfig").write_text(
        "if BOARD_USP_NATIVE\nconfig BUILD_OUTPUT_BIN\n\tdefault n\nendif\n"
    )
    (board / "board.cmake").write_text("# Runtime uses the IR-generated machine.\n")
    (board / "usp_native_defconfig").write_text(
        "CONFIG_SERIAL=y\nCONFIG_CONSOLE=y\nCONFIG_UART_CONSOLE=y\nCONFIG_XIP=n\nCONFIG_RISCV_PMP=y\n"
        f"CONFIG_SYS_CLOCK_HW_CYCLES_PER_SEC={spec['cpu']['timebase_hz']}\n"
    )
    app = out / "app"
    app.mkdir()
    (app / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.20.0)\n"
        "find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})\n"
        "project(native_irq)\ntarget_sources(app PRIVATE main.c)\n"
    )
    (app / "prj.conf").write_text(
        "CONFIG_PRINTK=y\nCONFIG_UART_INTERRUPT_DRIVEN=y\nCONFIG_ASSERT=y\nCONFIG_MAIN_STACK_SIZE=2048\n"
    )
    (app / "main.c").write_text(
        Template((ROOT / "templates/app.c.in").read_text()).substitute(
            bridge_declarations="",
            bridge_test="",
            finisher_base=hex(devices(spec, "finisher")[0]["base"]),
        )
    )


def build_software(generated, out, kind, source, cross_compile, jobs=2, test_case="smoke"):
    generated, out = generated.resolve(), out.resolve()
    if kind in {"ucos2", "ucos3"}:
        from .native_ucos import build as build_ucos

        return build_ucos(generated, out, source, kind, cross_compile, test_case)
    spec, ir = contract(generated)
    if kind not in {"freertos", "zephyr"} or test_case not in TEST_CASES:
        raise ContractError("unsupported native software/test case")
    if kind == "zephyr" and test_case != "smoke":
        raise ContractError("native Zephyr negative firmware requires a separate application")
    source = verify_source(source, FREERTOS_SHA if kind == "freertos" else ZEPHYR_SHA)
    compiler, version = tool_version(cross_compile + "gcc")
    before = identity()
    out.mkdir(parents=True, exist_ok=False)
    if kind == "freertos":
        bsp = freertos_bsp(generated, out, spec)
        shutil.copy2(source / "LICENSE.md", bsp / "FreeRTOS-LICENSE.md")
        port = source / "portable/GCC/RISC-V"
        sources = [
            bsp / "start.S",
            bsp / "app.c",
            bsp / "memory.c",
            source / "tasks.c",
            source / "queue.c",
            source / "list.c",
            port / "port.c",
            port / "portASM.S",
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
            "-DUSP_TEST_CASE=" + str(TEST_CASES[test_case]),
            "-I" + str(bsp),
            "-I" + str(source / "include"),
            "-I" + str(port),
            "-I" + str(port / "chip_specific_extensions/RISCV_MTIME_CLINT_no_extensions"),
        ]
        objects = []
        for n, p in enumerate(sources):
            obj = out / f"{n}.o"
            execute_build(
                [compiler, *flags, "-c", str(p), "-o", str(obj)], out, out / f"compile-{n}.log", 120
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
                str(bsp / "linker.ld"),
                *objects,
                "-lgcc",
                "-o",
                str(elf),
            ],
            bsp,
            out / "link.log",
            120,
        )
    else:
        zephyr_bsp(out, spec)
        env = dict(
            os.environ,
            ZEPHYR_BASE=str(source),
            ZEPHYR_TOOLCHAIN_VARIANT="cross-compile",
            CROSS_COMPILE=compiler.removesuffix("gcc"),
            XDG_CACHE_HOME=str(out / "cache"),
        )
        if not 1 <= jobs <= 8:
            raise ContractError("native software jobs must be 1..8")
        execute_build(
            [
                "west",
                "build",
                "-s",
                str(out / "app"),
                "-d",
                str(out / "build"),
                "-b",
                "usp_native",
                "-o=-j" + str(jobs),
                "--",
                "-DBOARD_ROOT=" + str(out / "zephyr"),
                "-DZEPHYR_MODULES=",
                "-DBUILD_VERSION=usp-native-pinned",
                "-DCMAKE_MAKE_PROGRAM=" + str(shutil.which("ninja")),
                "-DCROSS_COMPILE=" + compiler.removesuffix("gcc"),
            ],
            source,
            out / "build.log",
            600,
            env,
        )
        elf = out / "build/zephyr/zephyr.elf"
        shutil.copy2(source / "LICENSE", out / "Zephyr-LICENSE")
    verify_source(source, FREERTOS_SHA if kind == "freertos" else ZEPHYR_SHA)
    if before != identity():
        raise ContractError("software adapter changed during build")
    record = {
        "contract": "native-software-1",
        "kind": kind,
        "test_case": test_case,
        "adapter": before,
        "generated_manifest_sha256": digest(generated / "manifest.json"),
        "native_ir_sha256": digest(generated / "native-ir.json"),
        "source_commit": FREERTOS_SHA if kind == "freertos" else ZEPHYR_SHA,
        "compiler": {"version": version, "sha256": digest(Path(compiler))},
        "elf": elf.relative_to(out).as_posix(),
        "elf_sha256": digest(elf),
        "hart_count": 1,
        "timebase_hz": spec["cpu"]["timebase_hz"],
        "scope": "RV64 M-mode single-hart timer/preemption/UART IRQ BSP; no generic IP drivers",
        "files": {
            p.relative_to(out).as_posix(): digest(p)
            for p in out.rglob("*")
            if p.is_file() and not p.is_symlink()
        },
    }
    write_json(out / "software-build.json", record)
    return verify_software(generated, out)


def verify_software(generated, out):
    verify_generated(generated)
    record = json.loads((out / "software-build.json").read_text())
    from .ucos import PINS

    expected_pins = {
        "freertos": FREERTOS_SHA,
        "zephyr": ZEPHYR_SHA,
        "ucos2": PINS["ucos2"],
        "ucos3": PINS["ucos3"],
    }
    if (
        record.get("contract") != "native-software-1"
        or record.get("adapter") != identity()
        or record.get("generated_manifest_sha256") != digest(generated / "manifest.json")
        or record.get("native_ir_sha256") != digest(generated / "native-ir.json")
        or record.get("kind") not in expected_pins
        or record.get("source_commit") != expected_pins[record["kind"]]
    ):
        raise ContractError("stale/mixed native software build")
    for name, sha in record["files"].items():
        if digest(inside(out.resolve(), name)) != sha:
            raise ContractError("modified native software artifact")
    if digest(inside(out.resolve(), record["elf"])) != record["elf_sha256"]:
        raise ContractError("modified native software ELF")
    return record


def check_run(kind, text):
    if kind in {"ucos2", "ucos3"}:
        from .native_ucos import check_run as check_ucos

        return check_ucos(text)
    if kind == "freertos":
        return acceptance(text)[0]
    if kind == "zephyr":
        return {m: m in text.splitlines() for m in ZEPHYR_MARKERS} | {
            "no_failure": "USP_TEST_FAIL" not in text
        }
    raise ContractError("unsupported native software acceptance")
