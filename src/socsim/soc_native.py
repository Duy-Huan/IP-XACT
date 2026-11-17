"""Compile a spec into native QEMU/gem5 machines and checked software bundles."""

import argparse
import concurrent.futures
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from string import Template

from .domain import ContractError
from .execution import cleanup_on_termination, execute_build
from .ipblocks import verify_models
from .ipxact import digest
from .pipeline import QEMU_SHA, verify_source
from .project import inside
from .runner import tool_version, write_json
from .soc_spec import (
    CATALOG,
    ROLE,
    cpu_instances,
    devices,
    emit_ipxact,
    load_spec,
    model_descriptor,
    model_instances,
    selected,
)

ROOT = Path(__file__).parent
RESOURCES = ROOT / "resources/native"


def compiler_identity():
    paths = [
        ROOT / p
        for p in (
            "soc_native.py",
            "soc_native_gem5.py",
            "soc_native_codegen.py",
            "native_external.py",
            "soc_spec.py",
            "domain.py",
            "ipxact.py",
            "parameters.py",
            "hierarchy.py",
            "registers.py",
            "project.py",
            "templates/machine.c.in",
        )
    ]
    paths += [CATALOG, *sorted(RESOURCES.glob("*"))]
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in paths if p.is_file()}


def generate(spec_path, out, engine="qemu"):
    identity = compiler_identity()
    if engine not in {"qemu", "gem5", "both"}:
        raise ContractError("unsupported native engine")
    spec = load_spec(spec_path)
    from .native_external import preflight, stage

    preflight(spec, spec_path.resolve().parent)
    engines = ["qemu", "gem5"] if engine == "both" else [engine]
    if "gem5" in engines and "gem5_model" not in spec["cpu"]:
        raise ContractError("native gem5 requires explicit cpu.gem5_model; no CPU substitution")
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(spec_path, out / "spec.yaml")
    if load_spec(out / "spec.yaml") != spec:
        raise ContractError("native input changed during generation")
    stage(spec, spec_path.resolve().parent, out)
    ir = emit_ipxact(spec, out)
    windows = {i.name: i.windows[0] for i in ir.instances if i.windows}
    values = {
        "ir_hash": digest(out / "soc-ir.json"),
        "entry": hex(ir.cpu.entry),
        "timebase_hz": ir.cpu.timebase_hz,
        "uart_baudbase": 230400,
    }
    for kind in ("ram", "rom", "uart", "clint", "plic", "finisher"):
        dev = (
            selected(spec, {"uart": "console"}.get(kind, kind))
            if kind in {"ram", "rom", "uart"}
            else devices(spec, kind)[0]
        )
        w = windows[dev["name"]]
        values[kind + "_base"], values[kind + "_size"] = hex(w.base), hex(w.size)
    values["uart_irq"] = selected(spec, "console")["irq"]
    init = []
    for d in devices(spec, "ram"):
        if d["name"] != spec["boot"]["ram"]:
            init.append(
                f"{{ MemoryRegion *r = g_new0(MemoryRegion, 1); "
                f'memory_region_init_ram(r, NULL, "{d["name"]}", {hex(d["size"])}, &error_fatal); '
                f"memory_region_add_subregion(memory, {hex(d['base'])}, r); }}"
            )
    rtl = model_instances(spec)
    for slot, d in enumerate(rtl):
        init.append(
            f"native_attach(memory, plic, {slot}, {hex(d['base'])}, {d['irq']}, "
            f'{int(d["model"] == "opentitan.gpio")}, "{d["name"]}");'
        )
    values.update(
        bridge_code=(RESOURCES / "bridge.c.in").read_text() if rtl else "",
        bridge_init="\n".join(init),
    )
    machine = (ROOT / "templates/machine.c.in").read_text()
    from .soc_native_codegen import emit_dts, machine_topology

    old = (
        "serial_mm_init(memory, $uart_base, 0, qdev_get_gpio_in(plic, $uart_irq),\n"
        "                   $uart_baudbase, serial_hd(0), DEVICE_LITTLE_ENDIAN);"
    )
    if machine.count(old) != 1:
        raise ContractError("native generator requires the reviewed QEMU template")
    serials = "\n".join(
        f"serial_mm_init(memory, {hex(d['base'])}, 0, qdev_get_gpio_in(plic, {d['irq']}), "
        f"230400, serial_hd({i}), DEVICE_LITTLE_ENDIAN);"
        for i, d in enumerate(devices(spec, "uart"))
    )
    (out / "machine.c").write_text(
        Template(
            machine_topology(machine, spec).replace(old, serials).replace("usp-rv64", "usp-native")
        ).substitute(values)
    )
    headers = [
        "/* Generated from soc-ir.json; byte addresses. */",
        "#pragma once",
        f"#define USP_HART_COUNT {spec['cpu']['harts']}",
    ]
    for d in spec["devices"]:
        headers += [
            f"#define USP_INSTANCE_{d['name'].upper()}_BASE {hex(d['base'])}UL",
            f"#define USP_INSTANCE_{d['name'].upper()}_SIZE {hex(d['size'])}UL",
        ]
        if "irq" in d:
            headers.append(f"#define USP_INSTANCE_{d['name'].upper()}_IRQ {d['irq']}")
    for alias, field in (
        ("CONSOLE", "console"),
        ("CLINT", "clint"),
        ("PLIC", "plic"),
        ("FINISHER", "finisher"),
    ):
        d = selected(spec, field) if field == "console" else devices(spec, field)[0]
        headers.append(f"#define USP_{alias}_BASE {hex(d['base'])}UL")
    fw = out / "firmware"
    fw.mkdir()
    (fw / "soc.h").write_text("\n".join(dict.fromkeys(headers)) + "\n")
    shutil.copy2(RESOURCES / "start.S", fw / "start.S")

    def table(kind):
        return (
            ",".join(
                f'{{{hex(d["base"])}, {d["irq"]}, "{d["name"]}"}}' for d in devices(spec, kind)
            )
            or "{0,0,0}"
        )

    for key, kind in (("uart", "uart"), ("gcd", "chipyard.gcd"), ("gpio", "opentitan.gpio")):
        values[key + "s"] = table(kind)
        values[key + "_count"] = len(devices(spec, kind))
        values[key + "_slots"] = max(1, len(devices(spec, kind)))
    irqs = [d["irq"] for d in spec["devices"] if "irq" in d]
    values.update(
        irq_mask=hex(sum(1 << i for i in irqs)),
        irq_priorities="\n".join(f"R32(USP_PLIC_BASE+{i * 4})=1;" for i in irqs),
        timer_ticks=max(1, ir.cpu.timebase_hz // 100),
        extra_ram_tests="\n".join(
            f"R64({hex(d['base'])})={hex(0x5AC00000 + i)}; "
            f"if (R64({hex(d['base'])})!={hex(0x5AC00000 + i)}) fail();"
            for i, d in enumerate(devices(spec, "ram"))
            if d["name"] != spec["boot"]["ram"]
        ),
    )
    (fw / "main.c").write_text(
        Template((RESOURCES / "firmware.c.in").read_text()).substitute(values)
    )
    ram = selected(spec, "ram")
    (fw / "linker.ld").write_text(
        "OUTPUT_ARCH(riscv)\nENTRY(_start)\n"
        f"MEMORY {{ RAM (rwx) : ORIGIN = {hex(ir.cpu.entry)}, "
        f"LENGTH = {hex(ram['base'] + ram['size'] - ir.cpu.entry)} }}\n"
        "SECTIONS { .text : { KEEP(*(.text.init)) *(.text .text.*) } > RAM\n"
        ".rodata : { *(.rodata .rodata.*) } > RAM\n"
        ".data : { *(.data .data.* .sdata .sdata.*) } > RAM\n"
        "__global_pointer$ = . + 0x800;\n"
        ".bss (NOLOAD) : { . = ALIGN(16); __bss_start = .; *(.bss .bss.* .sbss .sbss.*) *(COMMON)\n"
        ". = ALIGN(16); __bss_end = .; } > RAM\n"
        "__stack_top = ORIGIN(RAM)+LENGTH(RAM)-16;\n"
        "/DISCARD/ : { *(.comment .note .note.* .eh_frame .eh_frame.*) } }\n"
        f'ASSERT(__bss_end <= __stack_top - {4096 * spec["cpu"]["harts"]}, "RAM stack overlap")\n'
    )
    (out / "soc.dts").write_text(emit_dts(spec))
    shutil.copy2(RESOURCES / "gem5-board.py.in", out / "gem5-board.py")
    from .gem5 import GEM5_SHA

    implementations = {
        "ram": "SimpleMemory",
        "rom": "SimpleMemory+native-reset",
        "uart": "RiscvUart8250",
        "clint": "Clint+RiscvRTC",
        "plic": "Plic",
        "finisher": "NativeSocDevice.finisher",
        "chipyard.gcd": "NativeSocDevice+SystemC.GCD",
        "opentitan.gpio": "NativeSocDevice+SystemC.GPIO",
    }
    engine_bindings = {}
    for e in engines:
        engine_bindings[e] = [
            {
                "instance": cpu,
                "implementation": spec["cpu"]["model" if e == "qemu" else "gem5_model"],
                "source_commit": QEMU_SHA if e == "qemu" else GEM5_SHA,
            }
            for cpu in cpu_instances(spec)
        ]
        engine_bindings[e] += [
            {
                "instance": d["name"],
                "model": d["model"],
                "implementation": model_descriptor(spec, d["model"])["id"]
                if e == "qemu"
                else implementations.get(d["model"], "NativeSocDevice+external.usp-mmio32-v1"),
                "source": model_descriptor(spec, d["model"])["source"]
                if e == "qemu"
                or d["model"] in {"chipyard.gcd", "opentitan.gpio"} | set(spec["external_models"])
                else {"commit": GEM5_SHA, "engine": "gem5"},
            }
            for d in spec["devices"]
        ]
    write_json(out / "engine-bindings.json", engine_bindings)
    write_json(
        out / "hardware-graph.json",
        {
            "contract": "native-flat-functional-v1",
            "cpu_instances": cpu_instances(spec),
            "harts": spec["cpu"]["harts"],
            "cpu_clock_hz": spec["cpu"]["clock_hz"],
            "memory_latency_ns": spec["cpu"]["memory_latency_ns"],
            "qemu_timing": "functional; clock metadata does not imply CPU cycles",
            "bus": "flat functional address-decoded fabric; gem5 SystemXBar",
            "interrupts": ir.to_dict()["interrupts"],
            "address_spaces": ir.to_dict()["address_spaces"],
        },
    )
    write_json(
        out / "hardware-graph.json",
        {
            "contract": "native-flat-functional-v1",
            "cpu_instances": cpu_instances(spec),
            "harts": spec["cpu"]["harts"],
            "cpu_clock_hz": spec["cpu"]["clock_hz"],
            "memory_latency_ns": spec["cpu"]["memory_latency_ns"],
            "qemu_timing": "functional; clock metadata does not imply CPU cycles",
            "bus": "flat functional address-decoded fabric; gem5 SystemXBar",
            "interrupts": ir.to_dict()["interrupts"],
            "address_spaces": ir.to_dict()["address_spaces"],
        },
    )
    (out / "simulate.py").write_text(
        "from pathlib import Path\nimport sys\nfrom socsim.soc_native import main\n"
        "args = ['run', '--generated', str(Path(__file__).resolve().parent), *sys.argv[1:]]\n"
        "raise SystemExit(main(args))\n"
    )
    if identity != compiler_identity():
        raise ContractError("native compiler changed during generation")
    write_json(
        out / "manifest.json",
        {
            "contract": "native-soc-qemu-v1",
            "engine": engine,
            "engines": engines,
            "compiler": identity,
            "ir_sha256": digest(out / "soc-ir.json"),
            "files": {
                p.relative_to(out).as_posix(): digest(p)
                for p in sorted(out.rglob("*"))
                if p.is_file()
            },
            "limits": [
                "1..8 homogeneous RV64 ISS harts, flat physical memory bus, functional timing",
                "bare-metal and OpenSBI/Linux; native Zephyr/RTOS BSPs and arbitrary RTL "
                "need adapters",
                "independent RTL instance processes; no coherent DMA or global cycle accuracy",
            ],
        },
    )
    return {
        "status": "GENERATED",
        "directory": str(out),
        "instances": len(ir.instances),
        "engine": engine,
        "ir_sha256": digest(out / "soc-ir.json"),
    }


def verify_generated(out):
    out = out.resolve()
    record = json.loads((out / "manifest.json").read_text())
    if record["contract"] != "native-soc-qemu-v1" or record["compiler"] != compiler_identity():
        raise ContractError("stale native compiler; regenerate the SoC")
    for filename, sha in record["files"].items():
        if digest(inside(out, filename)) != sha:
            raise ContractError("modified generated native artifact: " + filename)
    spec = load_spec(out / "spec.yaml")
    from .native_external import preflight

    preflight(spec, out)
    return spec


def build(out, source, cross_compile, jobs=2, reuse_qemu=None):
    out = out.resolve()
    reuse_qemu = reuse_qemu.resolve() if reuse_qemu is not None else None
    spec = verify_generated(out)
    source = verify_source(source, QEMU_SHA)
    if not 1 <= jobs <= 8:
        raise ContractError("native build jobs must be 1..8")
    native = out / "qemu-build"
    native.mkdir(exist_ok=True)
    reused = None
    if reuse_qemu is not None:
        previous = json.loads((reuse_qemu / "build.json").read_text())
        manifest = json.loads((reuse_qemu / "manifest.json").read_text())
        binary_name = "qemu-build/qemu-system-riscv64"
        if (
            previous["source_commit"] != QEMU_SHA
            or previous["generated_manifest_sha256"] != digest(reuse_qemu / "manifest.json")
            or manifest["files"]["machine.c"] != digest(out / "machine.c")
            or digest(reuse_qemu / "machine.c") != digest(out / "machine.c")
            or previous["files"][binary_name] != digest(reuse_qemu / binary_name)
        ):
            raise ContractError("native QEMU cache does not match generated C/source/binary")
        shutil.copy2(reuse_qemu / binary_name, out / binary_name)
        reused = {
            "build_record_sha256": digest(reuse_qemu / "build.json"),
            "machine_sha256": digest(out / "machine.c"),
            "binary_sha256": digest(out / binary_name),
        }
    derived = out / "qemu-source"
    if reused is None and not derived.exists():
        shutil.copytree(source, derived, ignore=shutil.ignore_patterns(".git"))
        meson = derived / "hw/riscv/meson.build"
        old = meson.read_text()
        if old.count("hw_arch +=") != 1:
            raise ContractError("unsupported QEMU meson structure")
        meson.write_text(
            old.replace("hw_arch +=", "riscv_ss.add(files('usp_native.c'))\n\nhw_arch +=")
        )
        with (derived / "hw/riscv/Kconfig").open("a") as stream:
            stream.write(
                "\nconfig USP_NATIVE\n    bool\n    default y\n    depends on RISCV64\n"
                "    select RISCV_ACLINT\n    select SERIAL_MM\n"
                "    select SIFIVE_PLIC\n    select SIFIVE_TEST\n"
            )
        (derived / "configs/devices/riscv64-softmmu/default.mak").write_text(
            "CONFIG_USP_NATIVE=y\n"
        )
    if reused is None:
        shutil.copyfile(out / "machine.c", derived / "hw/riscv/usp_native.c")
    if reused is None and not (native / "build.ninja").exists():
        command = [
            str(derived / "configure"),
            "--target-list=riscv64-softmmu",
            "--without-default-features",
            "--without-default-devices",
            "--disable-werror",
            "--enable-fdt",
            "--python=" + sys.executable,
        ]
        prefix = os.environ.get("USP_HOST_PREFIX")
        if prefix:
            command += [
                "--extra-cflags=-I" + prefix + "/usr/include",
                "--extra-ldflags=-L" + prefix + "/usr/lib/x86_64-linux-gnu",
            ]
        execute_build(command, native, out / "logs/configure.log", 600)
    if reused is None:
        execute_build(
            ["ninja", "-j", str(jobs), "qemu-system-riscv64"], native, out / "logs/build.log", 1800
        )
    fw = out / "firmware"
    command = [
        cross_compile + "gcc",
        "-march=rv64imac_zicsr_zifencei",
        "-mabi=lp64",
        "-mcmodel=medany",
        "-mno-relax",
        "-msmall-data-limit=0",
        "-O2",
        "-g",
        "-nostdlib",
        "-nostartfiles",
        "-ffreestanding",
        "-Wall",
        "-Werror",
        "-I",
        str(fw),
        "-T",
        str(fw / "linker.ld"),
        str(fw / "start.S"),
        str(fw / "main.c"),
        "-o",
        str(out / "firmware.elf"),
    ]
    execute_build(command, out, out / "logs/firmware-build.log", 120)
    execute_build(
        ["dtc", "-I", "dts", "-O", "dtb", "-o", str(out / "soc.dtb"), str(out / "soc.dts")],
        out,
        out / "logs/dtc.log",
        30,
    )
    verify_source(source, QEMU_SHA)
    verify_generated(out)
    compiler_path, compiler_version = tool_version(cross_compile + "gcc")
    write_json(
        out / "build.json",
        {
            "generated_manifest_sha256": digest(out / "manifest.json"),
            "source_commit": QEMU_SHA,
            "cpu": spec["cpu"]["model"],
            "reused_qemu": reused,
            "firmware_compiler": {
                "path": compiler_path,
                "version": compiler_version,
                "binary_sha256": digest(Path(compiler_path)),
            },
            "files": {
                n: digest(out / n)
                for n in ("qemu-build/qemu-system-riscv64", "firmware.elf", "soc.dtb")
            },
        },
    )
    return {"status": "BUILT", "engine": "qemu", "directory": str(out)}


def expected_markers(spec):
    markers = [
        "USP_NATIVE_BOOT_OK",
        "USP_NATIVE_MEMORY_OK",
        "USP_NATIVE_TIMER_OK",
        "USP_NATIVE_PASS",
        "USP_NATIVE_SMP_OK",
    ]
    for kind, tag in (("uart", "UART"), ("chipyard.gcd", "GCD"), ("opentitan.gpio", "GPIO")):
        markers += [f"USP_NATIVE_{tag}_OK:{d['name']}" for d in devices(spec, kind)]
    markers += [f"USP_NATIVE_HART_OK:{h}" for h in range(spec["cpu"]["harts"])]
    return markers


@cleanup_on_termination
def run(
    generated,
    models,
    out,
    timeout=30,
    firmware=None,
    engine="qemu",
    gem5_build=None,
    software="baremetal",
):
    generated, out = generated.resolve(), out.resolve()
    models = models.resolve() if models is not None else None
    gem5_build = gem5_build.resolve() if gem5_build is not None else None
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ContractError("invalid native simulation timeout")
    if software not in {"baremetal", "linux"} or (software == "linux" and firmware is not None):
        raise ContractError("select generated Linux or bare-metal/user ELF, not both")
    spec = verify_generated(generated)
    linux_record = None
    if software == "linux":
        from .soc_native_linux import verify as verify_linux

        linux_record = verify_linux(generated)
    conformance = firmware is None and software == "baremetal"
    if conformance and any(d["model"] in spec["external_models"] for d in spec["devices"]):
        raise ContractError(
            "external models require explicit user firmware; behavior is not inferred"
        )
    manifest = json.loads((generated / "manifest.json").read_text())
    if engine not in manifest["engines"]:
        raise ContractError("engine was not selected during native generation")
    gem5_record = None
    if engine == "gem5":
        from .soc_native_gem5 import verify_engine

        if gem5_build is None:
            raise ContractError("--gem5-build is required for the actual native gem5 engine")
        gem5_record = verify_engine(gem5_build)
    built = json.loads((generated / "build.json").read_text())
    if built["generated_manifest_sha256"] != digest(generated / "manifest.json"):
        raise ContractError("stale native build")
    for filename, sha in built["files"].items():
        if digest(inside(generated, filename)) != sha:
            raise ContractError("modified native build: " + filename)
    rtl = model_instances(spec)
    builtin_rtl = any(d["model"] in {"chipyard.gcd", "opentitan.gpio"} for d in rtl)
    if builtin_rtl and models is None:
        raise ContractError(
            "source IP instances need --models with verified native SystemC/RTL builds"
        )
    model_record = verify_models(models) if builtin_rtl else None
    out.mkdir(parents=True, exist_ok=False)
    elf = generated / "firmware.elf" if firmware is None else firmware.resolve()
    if linux_record:
        elf = generated / "linux/opensbi/platform/generic/firmware/fw_payload.elf"
    shutil.copy2(elf, out / "firmware.elf")
    shutil.copy2(generated / "spec.yaml", out / "spec.yaml")
    shutil.copy2(generated / "soc-ir.json", out / "soc-ir.json")
    write_json(out / "build.json", built)
    write_json(
        out / "engine-bindings.json",
        json.loads((generated / "engine-bindings.json").read_text())[engine],
    )
    if linux_record:
        write_json(out / "linux-build.json", linux_record)
    if gem5_record:
        write_json(out / "gem5-build.json", gem5_record)
        shutil.copy2(gem5_build / "gem5-LICENSE", out / "gem5-LICENSE")
    if model_record:
        write_json(out / "models-build.json", model_record)
        shutil.copytree(models / "licenses", out / "licenses")
    processes, streams, model_exits = [], [], {}
    error = None
    write_json(
        out / "engine.command.json",
        {"phase": "not-launched", "returncode": None, "timed_out": False},
    )
    with tempfile.TemporaryDirectory(prefix="usp-native-") as tmp:
        env = dict(os.environ)
        sockets = {}
        try:
            for slot, d in enumerate(rtl):
                sock = Path(tmp) / f"{slot}.sock"
                trace = out / (d["name"] + ".trace.jsonl")
                stream = (out / (d["name"] + ".model.log")).open("wb")
                streams.append(stream)
                descriptor = spec["external_models"].get(d["model"])
                executable = (
                    inside(generated, descriptor["executable"])
                    if descriptor
                    else models / "rtl/Vusp_ipblocks"
                )
                cmd = [str(executable), str(sock), str(trace)]
                write_json(
                    out / (d["name"] + ".command.json"),
                    {
                        "argv": cmd,
                        "instance": d["name"],
                        "selected_model": d["model"],
                        "executable_sha256": digest(Path(cmd[0])),
                        "unmapped_wrapper_sibling": descriptor is None,
                    },
                )
                process = subprocess.Popen(
                    cmd, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True
                )
                processes.append((d["name"], process))
                deadline = time.monotonic() + 5
                while not sock.exists():
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise ContractError("native RTL instance startup failed: " + d["name"])
                    time.sleep(0.01)
                env[f"USP_NATIVE_SOCKET_{slot}"] = str(sock)
                sockets[d["name"]] = str(sock)
            ram = selected(spec, "ram")
            cmd = [
                str(generated / "qemu-build/qemu-system-riscv64"),
                "-M",
                "usp-native",
                "-cpu",
                spec["cpu"]["model"].removeprefix("qemu."),
                "-smp",
                str(spec["cpu"]["harts"]),
                "-m",
                f"{ram['size'] // 1024}K",
                "-display",
                "none",
                "-monitor",
                "none",
                "-kernel",
                str(out / "firmware.elf"),
            ]
            for d in devices(spec, "uart"):
                cmd += ["-serial", "file:" + str(out / (d["name"] + ".uart.log"))]
            if software == "linux":
                cmd += ["-dtb", str(generated / "linux/soc.dtb")]
            if engine == "gem5":
                from .gem5 import python_environment
                from .soc_native_gem5 import prepare_run

                prepare_run(
                    spec,
                    generated,
                    out,
                    sockets,
                    generated / "linux/soc.dtb" if software == "linux" else None,
                )
                cmd = [
                    str(gem5_build / "gem5.opt"),
                    "--listener-mode=off",
                    "--outdir=" + str(out),
                    str(out / "gem5-board.py"),
                    str(out / "board.json"),
                ]
                env = python_environment()
            execute_build(cmd, out, out / "engine.log", timeout, env)
        except ContractError as exc:
            error = str(exc)
        finally:
            for label, process in processes:
                try:
                    model_exits[label] = process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    model_exits[label] = process.wait()
                except BaseException:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise
            for stream in streams:
                stream.close()
    gem5_checks, metrics = {}, None
    if engine == "gem5":
        from .soc_native_gem5 import collect_run

        gem5_checks, metrics = collect_run(spec, out)
    uart_path = out / (spec["boot"]["console"] + ".uart.log")
    uart = uart_path.read_text(errors="replace") if uart_path.exists() else ""
    checks = (
        {marker: marker in uart.splitlines() for marker in expected_markers(spec)}
        if conformance
        else {}
    )
    if software == "linux":
        from .soc_native_linux import checks as linux_checks

        checks.update(linux_checks(uart))
    checks["engine_exit"] = error is None
    checks["model_exits"] = all(rc == 0 for rc in model_exits.values())
    checks.update(gem5_checks)
    for d in rtl if conformance else []:
        p = out / (d["name"] + ".trace.jsonl")
        tx = [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []
        checks[d["name"] + ".transactions"] = bool(tx) and all(t["status"] == 0 for t in tx)
        if d["model"] == "chipyard.gcd":
            answer = 6 + 2 * devices(spec, "chipyard.gcd").index(d)
            checks[d["name"] + ".result"] = [
                t["value"] for t in tx if t["op"] == 0 and t["offset"] == 12
            ] == [answer]
            checks[d["name"] + ".irq"] = any(t["irq"] & 1 for t in tx)
        else:
            checks[d["name"] + ".irq"] = any(t["irq"] & 2 for t in tx)
    result = {
        "status": "PASSED" if all(checks.values()) else "FAILED",
        "checks": checks,
        "name": spec["name"],
        "engine": engine,
        "performance": metrics,
        "error": error,
        "model_exit_codes": model_exits,
        "generated_manifest_sha256": digest(generated / "manifest.json"),
        "ir_sha256": digest(generated / "soc-ir.json"),
        "firmware_sha256": digest(out / "firmware.elf"),
        "suite": "native-linux-boot"
        if software == "linux"
        else ("generated-conformance" if conformance else "user-firmware-execution"),
        "scope": "native spec execution; full peripheral checks only for generated conformance",
        "full_master_objective_complete": False,
        "files": {
            p.relative_to(out).as_posix(): digest(p) for p in sorted(out.rglob("*")) if p.is_file()
        },
    }
    write_json(out / "report.json", result)
    (out / "report.md").write_text(
        f"# Native SoC: {spec['name']}\n\nStatus: {result['status']}\n\n"
        + "\n".join(f"- {k}: {v}" for k, v in checks.items())
        + "\n"
    )
    return result


def verify(out):
    out = out.resolve()
    record = json.loads((out / "report.json").read_text())
    for filename, sha in record["files"].items():
        if digest(inside(out, filename)) != sha:
            raise ContractError("modified native run evidence: " + filename)
    if record.get("kind") == "native-dual-engine":
        for engine in ("qemu", "gem5"):
            verify(out / engine)
        if record["status"] != "PASSED" or not all(record["checks"].values()):
            raise ContractError("dual-engine native acceptance is incomplete")
        return record
    spec = load_spec(out / "spec.yaml")
    uart_path = out / (spec["boot"]["console"] + ".uart.log")
    text = uart_path.read_text() if uart_path.exists() else ""
    if record["suite"] == "native-linux-boot":
        from .soc_native_linux import checks as linux_checks

        if not all(linux_checks(text).values()):
            raise ContractError("native Linux boot evidence incomplete")
    if record["suite"] == "generated-conformance" and any(
        m not in text.splitlines() for m in expected_markers(spec)
    ):
        raise ContractError("native guest conformance evidence incomplete")
    command = json.loads((out / "engine.command.json").read_text())
    execution_ok = command.get("returncode") == 0 and not command.get("timed_out", True)
    if execution_ok != record["checks"]["engine_exit"]:
        raise ContractError("native engine exit evidence inconsistent")
    if (record["status"] == "PASSED") != all(record["checks"].values()):
        raise ContractError("native acceptance status inconsistent")
    return record


@cleanup_on_termination
def accept(generated, models, gem5_build, out, timeout=30, software="baremetal", firmware=None):
    """Run two independent native engines concurrently against the same IR/ELF."""
    generated, out = generated.resolve(), out.resolve()
    verify_generated(generated)
    out.mkdir(parents=True, exist_ok=False)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            e: pool.submit(
                run, generated, models, out / e, timeout, firmware, e, gem5_build, software
            )
            for e in ("qemu", "gem5")
        }
        reports = {e: task.result() for e, task in futures.items()}
    checks = {e + ".accepted": r["status"] == "PASSED" for e, r in reports.items()}
    checks["same_elf"] = reports["qemu"]["firmware_sha256"] == reports["gem5"]["firmware_sha256"]
    checks["same_ir"] = reports["qemu"]["ir_sha256"] == reports["gem5"]["ir_sha256"]
    spec = load_spec(generated / "spec.yaml")
    for d in spec["devices"] if software == "baremetal" else []:
        if d["model"] not in {"chipyard.gcd", "opentitan.gpio"} | set(spec["external_models"]):
            continue
        traces = []
        for engine in ("qemu", "gem5"):
            path = out / engine / (d["name"] + ".trace.jsonl")
            tx = (
                [json.loads(line) for line in path.read_text().splitlines()]
                if path.exists()
                else []
            )
            traces.append(
                [
                    (t["op"], t["offset"], t["data"] if t["op"] == 1 else t["value"])
                    for t in tx
                    if t["op"] in {1, 2}
                    or (
                        t["op"] == 0
                        and (t["offset"] == 12 or d["model"] in spec["external_models"])
                    )
                ]
            )
        checks[d["name"] + ".same_commands_results"] = bool(traces[0]) and traces[0] == traces[1]
        if d["model"] in spec["external_models"]:
            from .native_external import trace_checks

            for engine in ("qemu", "gem5"):
                checks[d["name"] + "." + engine + ".transactions"] = trace_checks(
                    out / engine / (d["name"] + ".trace.jsonl")
                )
    result = {
        "kind": "native-dual-engine",
        "status": "PASSED" if all(checks.values()) else "FAILED",
        "checks": checks,
        "name": spec["name"],
        "execution": "concurrent independent engines",
        "software": software,
        "full_master_objective_complete": False,
        "files": {
            p.relative_to(out).as_posix(): digest(p) for p in sorted(out.rglob("*")) if p.is_file()
        },
    }
    write_json(out / "report.json", result)
    return result


def main(argv):
    parser = argparse.ArgumentParser(prog="usp soc")
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("validate", "generate"):
        sub = commands.add_parser(action)
        sub.add_argument("spec", type=Path)
        if action == "generate":
            sub.add_argument("--out", type=Path, required=True)
            sub.add_argument("--engine", choices=["qemu", "gem5", "both"], default="both")
    sub = commands.add_parser("build")
    sub.add_argument("--generated", type=Path, required=True)
    sub.add_argument("--source-qemu", type=Path, required=True)
    sub.add_argument(
        "--cross-compile", default=os.environ.get("CROSS_COMPILE", "riscv64-unknown-elf-")
    )
    sub.add_argument("--jobs", type=int, default=2)
    sub.add_argument("--reuse-qemu", type=Path)
    sub = commands.add_parser("linux-build")
    sub.add_argument("--generated", type=Path, required=True)
    sub.add_argument("--source-linux", type=Path, required=True)
    sub.add_argument("--source-opensbi", type=Path, required=True)
    sub.add_argument(
        "--cross-compile", default=os.environ.get("CROSS_COMPILE_LINUX", "riscv64-linux-gnu-")
    )
    sub.add_argument("--jobs", type=int, default=2)
    sub.add_argument("--reuse-linux", type=Path)
    sub = commands.add_parser("run")
    sub.add_argument("--generated", type=Path, required=True)
    sub.add_argument("--models", type=Path)
    sub.add_argument("--out", type=Path, required=True)
    sub.add_argument("--timeout", type=float, default=30)
    sub.add_argument("--firmware", type=Path)
    sub.add_argument("--software", choices=["baremetal", "linux"], default="baremetal")
    sub.add_argument("--engine", choices=["qemu", "gem5"], default="qemu")
    sub.add_argument("--gem5-build", type=Path)
    sub = commands.add_parser("accept")
    sub.add_argument("--generated", type=Path, required=True)
    sub.add_argument("--models", type=Path)
    sub.add_argument("--gem5-build", type=Path, required=True)
    sub.add_argument("--out", type=Path, required=True)
    sub.add_argument("--timeout", type=float, default=30)
    sub.add_argument("--software", choices=["baremetal", "linux"], default="baremetal")
    sub.add_argument("--firmware", type=Path)
    sub = commands.add_parser("gem5-build")
    sub.add_argument("--source-gem5", type=Path, required=True)
    sub.add_argument("--out", type=Path, required=True)
    sub.add_argument("--jobs", type=int, default=2)
    commands.add_parser("models")
    commands.add_parser("verify").add_argument("out", type=Path)
    args = parser.parse_args(argv)
    result: dict
    try:
        if args.action == "gem5-build":
            from .soc_native_gem5 import build_engine

            result = build_engine(args.source_gem5, args.out, args.jobs)
        elif args.action == "models":
            result = {
                "engines": ["qemu", "gem5"],
                "models": sorted(ROLE),
                "capabilities": {
                    "homogeneous_rv64_harts": {"minimum": 1, "maximum": 8},
                    "physical_address_bits": 48,
                    "maximum_instances": 64,
                    "irq_sources": 31,
                    "fabric": "flat functional memory map; gem5 SystemXBar",
                    "external_models": {
                        "protocol": "usp-mmio32-v1",
                        "aperture_bytes": 4096,
                        "access_bits": 32,
                        "interrupt": "one level-high source; wire response bit zero",
                        "ownership": "selected ISS owns CPU/RAM; model owns peripheral state",
                        "qualification": "explicit executable, hash, license and user firmware",
                        "dma": False,
                    },
                    "software": ["generated-baremetal", "native-linux", "explicit-user-elf"],
                },
                "cpu_models": {
                    "qemu": ["qemu.rv64", "qemu.max"],
                    "gem5": ["gem5.RiscvTimingSimpleCPU", "gem5.RiscvAtomicSimpleCPU"],
                },
            }
        elif args.action == "validate":
            s = load_spec(args.spec)
            result = {
                "status": "PASSED",
                "name": s["name"],
                "instances": s["cpu"]["harts"] + len(s["devices"]),
                "scope": "native spec semantics/model capability",
            }
        elif args.action == "generate":
            result = generate(args.spec.resolve(), args.out, args.engine)
        elif args.action == "linux-build":
            from .soc_native_linux import build as build_linux

            result = build_linux(
                args.generated.resolve(),
                args.source_linux,
                args.source_opensbi,
                args.cross_compile,
                args.jobs,
                args.reuse_linux.resolve() if args.reuse_linux else None,
            )
        elif args.action == "build":
            result = build(
                args.generated.resolve(),
                args.source_qemu,
                args.cross_compile,
                args.jobs,
                args.reuse_qemu.resolve() if args.reuse_qemu else None,
            )
        elif args.action == "run":
            result = run(
                args.generated.resolve(),
                args.models.resolve() if args.models else None,
                args.out.resolve(),
                args.timeout,
                args.firmware,
                args.engine,
                args.gem5_build.resolve() if args.gem5_build else None,
                args.software,
            )
        elif args.action == "accept":
            result = accept(
                args.generated.resolve(),
                args.models.resolve() if args.models else None,
                args.gem5_build.resolve(),
                args.out.resolve(),
                args.timeout,
                args.software,
                args.firmware,
            )
        else:
            result = verify(args.out.resolve())
        print(json.dumps(result, indent=2))
        return 1 if result.get("status") == "FAILED" else 0
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}))
        return 2
