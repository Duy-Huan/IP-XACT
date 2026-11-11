"""Pinned custom QEMU/Zephyr vertical slice; no implicit upstream-board fallback."""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from string import Template

from .domain import ContractError
from .ipxact import digest
from .runner import write_json

QEMU_SHA = "7c949c53e936aa3a658d84ab53bae5cadaa5d59c"
ZEPHYR_SHA = "413b789deb391d3a37d06b463288a5fe765ee57e"


def execute(argv, cwd, log, timeout=600, env=None):
    """Record argv and bounded process output; keep the real return code."""
    log.parent.mkdir(parents=True, exist_ok=True)
    write_json(log.with_suffix(".command.json"), {"argv": list(map(str, argv)), "cwd": str(cwd)})
    with log.open("wb") as stream:
        process = subprocess.Popen(
            list(map(str, argv)),
            cwd=cwd,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            rc = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise ContractError(f"command timeout; see {log}") from exc
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
    if rc:
        raise ContractError(
            f"command failed ({rc}); see {log}:\n{log.read_text(errors='replace')[-2500:]}"
        )
    return rc


def verify_source(path, sha):
    path = path.resolve()
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    if result.stdout.strip() != sha:
        raise ContractError(f"upstream revision mismatch: {path.name}; expected {sha}")
    status = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
    )
    if status.stdout.strip():
        raise ContractError(f"upstream checkout must be clean: {path.name}")
    return path


def resolve(project):
    if (
        project.manifest["profile"] not in {"usp-rv64-v1", "usp-rv64-hybrid-v1"}
        or project.ir.cpu is None
    ):
        raise ContractError("custom pipeline requires the usp-rv64-v1 hardware contract")
    models = {}
    for b in project.models["bindings"]:
        if not isinstance(b, dict) or set(b) != {"vlnv", "backend", "implementation"}:
            raise ContractError("invalid model descriptor binding")
        if not all(isinstance(v, str) for v in b.values()):
            raise ContractError("binding values must be strings")
        key = (b["vlnv"], b["backend"])
        if key in models:
            raise ContractError("ambiguous model binding")
        models[key] = b["implementation"]
    result = []
    catalog = json.loads((Path(__file__).parent / "schemas/model-catalog-v1.json").read_text())
    descriptors = {m["role"]: m for m in catalog["models"]}
    for instance in project.ir.instances:
        descriptor = descriptors[instance.name]
        expected = descriptor["id"]
        if instance.vlnv != descriptor["vlnv"]:
            raise ContractError(f"{instance.name}: no exact catalog VLNV match")
        if models.get((instance.vlnv, "qemu")) != expected:
            raise ContractError(f"{instance.name}: incompatible or unbound QEMU model")
        result.append(
            {
                "instance": instance.name,
                "vlnv": instance.vlnv,
                "implementation": expected,
                "model_version": descriptor["version"],
                "contract_id": descriptor["contract_id"],
                "source": descriptor["source"],
                "upstream_commit": QEMU_SHA,
                "fidelity": "functional",
                "capabilities": ["execute"] if instance.name == "rv64" else ["mmio"],
                "interrupts": instance.name in {"uart", "clint", "plic"},
            }
        )
    return result


def compiler_hashes():
    modules = (
        "__init__.py",
        "domain.py",
        "ipxact.py",
        "project.py",
        "contracts.py",
        "pipeline.py",
        "yocto.py",
    )
    root = Path(__file__).parent
    return {name: digest(root / name) for name in modules}


def identity(project):
    return hashlib.sha256(json.dumps(project.ir.to_dict(), sort_keys=True).encode()).hexdigest()


def generate(project, out):
    bindings = resolve(project)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    ir_hash = identity(project)
    values = {
        "ir_hash": ir_hash,
        "entry": hex(project.ir.cpu.entry),
        "timebase_hz": project.ir.cpu.timebase_hz,
    }
    for instance in project.ir.instances:
        if instance.windows:
            w = instance.windows[0]
            values.update(
                {
                    instance.name + "_base": hex(w.base),
                    instance.name + "_size": hex(w.size),
                    instance.name + "_unit": f"{w.base:x}",
                }
            )
    catalog = json.loads((Path(__file__).parent / "schemas/model-catalog-v1.json").read_text())
    uart = next(m for m in catalog["models"] if m["role"] == "uart")
    values["uart_clock_hz"] = uart["clock_hz"]
    values["uart_baudbase"] = uart["clock_hz"] // 16
    values["uart_irq"] = next(r.line for r in project.ir.interrupts if r.source == "uart")
    clint = int(values["clint_base"], 16)
    values.update(
        mtime=hex(clint + 0xBFF8), mtimecmp=hex(clint + 0x4000), mtime_unit=f"{clint + 0xBFF8:x}"
    )
    templates = Path(__file__).parent / "templates"
    values.update(bridge_code="", bridge_init="", bridge_dts="")
    hybrid = project.manifest["profile"] == "usp-rv64-hybrid-v1"
    if hybrid:
        values["fifo_irq"] = next(r.line for r in project.ir.interrupts if r.source == "scfifo")
        values["bridge_code"] = Template((templates / "bridge.c.in").read_text()).substitute(values)
        values["bridge_init"] = "usp_fifo_init(memory, plic);"
        values["bridge_dts"] = (
            f'scfifo: fifo@{values["scfifo_unit"]} {{ compatible = "socsim,scfifo"; '
            f"reg = <{values['scfifo_base']} {values['scfifo_size']}>; "
            f"interrupt-parent = <&plic>; interrupts = <{values['fifo_irq']} 1>; }};"
        )
        binding = out / "zephyr/dts/bindings/misc"
        binding.mkdir(parents=True)
        (binding / "socsim,scfifo.yaml").write_text(
            'description: USP explicit FIFO bridge contract\ncompatible: "socsim,scfifo"\n'
            "include: base.yaml\nproperties:\n  reg:\n    required: true\n"
            "  interrupts:\n    required: true\n"
        )
    (out / "machine.c").write_text(
        Template((templates / "machine.c.in").read_text()).substitute(values)
    )
    (out / "linux.dts").write_text(
        Template((templates / "linux.dts.in").read_text()).substitute(values)
    )
    board = out / "zephyr/boards/usp/usp_rv64"
    board.mkdir(parents=True)
    (board / "usp_rv64.dts").write_text(
        Template((templates / "board.dts.in").read_text()).substitute(values)
    )
    (board / "board.yml").write_text(
        "board:\n  name: usp_rv64\n  vendor: others\n  socs:\n    - name: qemu_virt_riscv64\n"
    )
    (board / "Kconfig.usp_rv64").write_text(
        "config BOARD_USP_RV64\n\tselect SOC_QEMU_VIRT_RISCV64\n"
    )
    (board / "Kconfig.defconfig").write_text(
        "if BOARD_USP_RV64\nconfig BUILD_OUTPUT_BIN\n\tdefault n\nendif\n"
    )
    (board / "usp_rv64_defconfig").write_text(
        "CONFIG_SERIAL=y\nCONFIG_CONSOLE=y\nCONFIG_UART_CONSOLE=y\nCONFIG_XIP=n\nCONFIG_RISCV_PMP=y\n"
    )
    (board / "board.cmake").write_text(
        "# Direct execution uses the generated machine, not upstream virt.\n"
    )
    app = out / "app"
    app.mkdir()
    (app / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.20.0)\n"
        "find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})\n"
        "project(usp_irq_smoke)\ntarget_sources(app PRIVATE main.c)\n"
    )
    (app / "prj.conf").write_text(
        "CONFIG_PRINTK=y\nCONFIG_UART_INTERRUPT_DRIVEN=y\nCONFIG_ASSERT=y\nCONFIG_MAIN_STACK_SIZE=2048\n"
    )
    values.update(bridge_declarations="", bridge_test="")
    if hybrid:
        declarations = (
            "#include <zephyr/irq.h>\n"
            "static K_SEM_DEFINE(fifo_done, 0, 1);\n"
            "static volatile uint32_t fifo_value;\n"
            "static void fifo_isr(const void *arg) { ARG_UNUSED(arg); "
            f"fifo_value = sys_read32({values['scfifo_base']}); k_sem_give(&fifo_done); }}\n"
        )
        test = (
            "IRQ_CONNECT(DT_IRQN(DT_NODELABEL(scfifo)), 1, fifo_isr, NULL, 0);\n"
            "irq_enable(DT_IRQN(DT_NODELABEL(scfifo)));\n"
            f"if (sys_read32({values['scfifo_base']} + 4)) {{ goto fail; }}\n"
            f"sys_write32(0x5a3c1234, {values['scfifo_base']});\n"
            "if (k_sem_take(&fifo_done, K_SECONDS(2)) || fifo_value != 0x5a3c1234) { goto fail; }\n"
            'printk("USP_TLM_RTL_IRQ_OK\\n");\n'
        )
        values.update(bridge_declarations=declarations, bridge_test=test)
    (app / "main.c").write_text(Template((templates / "app.c.in").read_text()).substitute(values))
    from .yocto import generate_layer

    if not hybrid:
        generate_layer(out)
    write_json(
        out / "software-contract.json",
        {
            "schema_version": "1",
            "required_isa": project.ir.cpu.isa,
            "abi": "lp64",
            "reset_vector": project.ir.cpu.reset_vector,
            "entry": project.ir.cpu.entry,
            "timebase_hz": project.ir.cpu.timebase_hz,
            "uart_clock_hz": values["uart_clock_hz"],
            "zephyr_uart_compatible": "ns16550",
            "linux_uart_compatible": "ns16550a",
            "interrupts": [
                {"source": r.source, "target": r.target, "line": r.line}
                for r in project.ir.interrupts
            ],
            "boot": {
                "zephyr": "direct M-mode ELF",
                "linux": "ROM -> OpenSBI -> S-mode Linux -> initramfs",
            },
        },
    )
    header = [
        "/* Generated from validated IR; SPDX-License-Identifier: Apache-2.0 */",
        "#pragma once",
    ]
    for instance in project.ir.instances:
        if instance.windows:
            window = instance.windows[0]
            header.extend(
                [
                    f"#define USP_{instance.name.upper()}_BASE {hex(window.base)}UL",
                    f"#define USP_{instance.name.upper()}_SIZE {hex(window.size)}UL",
                ]
            )
    (out / "soc.h").write_text("\n".join(header) + "\n")
    ram = next(i.windows[0] for i in project.ir.instances if i.name == "ram")
    (out / "memory.ld").write_text(
        f"MEMORY {{ RAM (rwx) : ORIGIN = {hex(ram.base)}, LENGTH = {hex(ram.size)} }}\n"
    )
    write_json(out / "soc-ir.json", project.ir.to_dict())
    write_json(out / "models.lock.json", {"policy": "strict", "models": bindings})
    write_json(
        out / "manifest.json",
        {
            "schema_version": "1",
            "ir_sha256": ir_hash,
            "compiler_sha256": compiler_hashes(),
            "templates": {p.name: digest(p) for p in sorted(templates.glob("*.in"))},
            "catalog_sha256": digest(Path(__file__).parent / "schemas/model-catalog-v1.json"),
            "qemu_commit": QEMU_SHA,
            "zephyr_commit": ZEPHYR_SHA,
            "files": {
                p.relative_to(out).as_posix(): digest(p)
                for p in sorted(out.rglob("*"))
                if p.is_file()
            },
        },
    )
    return out


def verify_generated(project, out):
    manifest = json.loads((out / "manifest.json").read_text())
    if identity(project) != manifest["ir_sha256"]:
        raise ContractError("stale generated output: IR identity mismatch")
    compiler = compiler_hashes()
    if manifest.get("compiler_sha256") != compiler:
        raise ContractError("stale compiler revision; regenerate outputs")
    templates = Path(__file__).parent / "templates"
    if manifest.get("templates") != {p.name: digest(p) for p in sorted(templates.glob("*.in"))}:
        raise ContractError("stale generator templates; regenerate outputs")
    if manifest.get("catalog_sha256") != digest(
        Path(__file__).parent / "schemas/model-catalog-v1.json"
    ):
        raise ContractError("stale model catalog; regenerate outputs")
    if json.loads((out / "models.lock.json").read_text()) != {
        "policy": "strict",
        "models": resolve(project),
    }:
        raise ContractError("stale model resolution")
    for name, expected in manifest["files"].items():
        path = (out / name).resolve()
        if not path.is_relative_to(out.resolve()) or digest(path) != expected:
            raise ContractError(f"generated artifact changed: {name}")
    return manifest


def build_qemu(project, out, source, jobs=4):
    verify_generated(project, out)
    source = verify_source(source, QEMU_SHA)
    derived = out / "qemu-source"
    if not derived.exists():
        shutil.copytree(source, derived, ignore=shutil.ignore_patterns(".git"))
        shutil.copyfile(out / "machine.c", derived / "hw/riscv/usp_machine.c")
        meson = derived / "hw/riscv/meson.build"
        content = meson.read_text()
        meson.write_text(
            content.replace("hw_arch +=", "riscv_ss.add(files('usp_machine.c'))\n\nhw_arch +=")
        )
        with (derived / "hw/riscv/Kconfig").open("a") as stream:
            stream.write(
                "\nconfig USP_PLATFORM\n    bool\n    default y\n    depends on RISCV64\n"
                "    select RISCV_ACLINT\n    select SERIAL_MM\n"
                "    select SIFIVE_PLIC\n    select SIFIVE_TEST\n"
            )
        (derived / "configs/devices/riscv64-softmmu/default.mak").write_text(
            "CONFIG_USP_PLATFORM=y\n"
        )
    if digest(derived / "hw/riscv/usp_machine.c") != digest(out / "machine.c"):
        shutil.copyfile(out / "machine.c", derived / "hw/riscv/usp_machine.c")
    build = out / "qemu-build"
    build.mkdir(exist_ok=True)
    if not (build / "build.ninja").exists():
        command = [
            str(derived / "configure"),
            "--target-list=riscv64-softmmu",
            "--without-default-features",
            "--without-default-devices",
            "--disable-werror",
            "--enable-fdt",
            "--python=" + sys.executable,
        ]
        # Optional user-owned Debian build dependencies; no trust or source overrides.
        prefix = os.environ.get("USP_HOST_PREFIX")
        if prefix:
            command.extend(
                [
                    "--extra-cflags=-I" + prefix + "/usr/include",
                    "--extra-ldflags=-L" + prefix + "/usr/lib/x86_64-linux-gnu",
                ]
            )
        execute(command, build, out / "logs/qemu-configure.log")
    execute(
        ["ninja", "-j", str(jobs), "qemu-system-riscv64"], build, out / "logs/qemu-build.log", 1800
    )
    binary = build / "qemu-system-riscv64"
    write_json(
        out / "qemu-build.json",
        {
            "generated_manifest_sha256": digest(out / "manifest.json"),
            "binary_sha256": digest(binary),
            "source_commit": QEMU_SHA,
            "generated_machine_sha256": digest(out / "machine.c"),
        },
    )
    return binary


def build_zephyr(project, out, source, cross_compile, jobs=4):
    verify_generated(project, out)
    source = verify_source(source, ZEPHYR_SHA)
    env = os.environ.copy()
    env.update(
        ZEPHYR_BASE=str(source),
        ZEPHYR_TOOLCHAIN_VARIANT="cross-compile",
        CROSS_COMPILE=str(cross_compile),
        XDG_CACHE_HOME=str(out / "cache"),
    )
    ninja = shutil.which("ninja")
    if not ninja:
        raise ContractError("ninja is required for Zephyr builds")
    build = out / "zephyr-build"
    if not (source.parent / ".west/config").exists():
        execute(["west", "init", "-l", str(source)], source.parent, out / "logs/west-init.log")
    execute(
        [
            "west",
            "build",
            "-s",
            str(out / "app"),
            "-d",
            str(build),
            "-b",
            "usp_rv64",
            "-o=-j" + str(jobs),
            "--",
            "-DBOARD_ROOT=" + str(out / "zephyr"),
            "-DZEPHYR_MODULES=",
            "-DBUILD_VERSION=usp-pinned",
            "-DCMAKE_MAKE_PROGRAM=" + ninja,
        ],
        source,
        out / "logs/zephyr-build.log",
        600,
        env,
    )
    elf = build / "zephyr/zephyr.elf"
    write_json(
        out / "zephyr-build.json",
        {
            "generated_manifest_sha256": digest(out / "manifest.json"),
            "elf_sha256": digest(elf),
            "source_commit": ZEPHYR_SHA,
            "config_sha256": digest(build / "zephyr/.config"),
            "dts_sha256": digest(build / "zephyr/zephyr.dts"),
        },
    )
    return elf


def run_custom(project, out, result_dir, timeout=20, osname="zephyr", bridge_socket=None):
    """Require boot, CLINT timer ISR and UART through PLIC, then guest success exit."""
    import math
    import time

    verify_generated(project, out)
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ContractError("invalid timeout")
    binary = out / "qemu-build/qemu-system-riscv64"
    if osname not in {"zephyr", "linux"}:
        raise ContractError("unsupported software runtime")
    elf = out / (
        "zephyr-build/zephyr/zephyr.elf"
        if osname == "zephyr"
        else "linux/opensbi/platform/generic/firmware/fw_payload.elf"
    )
    software_record = "zephyr-build.json" if osname == "zephyr" else "linux-build.json"
    for record, key, path in [
        ("qemu-build.json", "binary_sha256", binary),
        (software_record, "elf_sha256", elf),
    ]:
        artifact_record = json.loads((out / record).read_text())
        if artifact_record.get("generated_manifest_sha256") != digest(out / "manifest.json"):
            raise ContractError("stale build: regenerate/rebuild identity mismatch")
        if artifact_record[key] != digest(path):
            raise ContractError("stale/modified built artifact")
    result_dir.mkdir(parents=True, exist_ok=False)
    command = [
        str(binary),
        "-machine",
        "usp-rv64",
        "-display",
        "none",
        "-monitor",
        "none",
        "-serial",
        "stdio",
        "-m",
        "128M",
        "-kernel",
        str(elf),
        "-no-reboot",
    ]
    if osname == "linux":
        dtb = out / "linux/soc.dtb"
        record = json.loads((out / software_record).read_text())
        if digest(dtb) != record["dtb_sha256"]:
            raise ContractError("modified Linux DTB")
        command += ["-dtb", str(dtb)]
    write_json(result_dir / "command.json", {"argv": command, "cwd": str(out)})
    runtime_env = os.environ.copy()
    if bridge_socket is not None:
        runtime_env["USP_BRIDGE_SOCKET"] = str(bridge_socket)
    started = time.monotonic()
    timed_out = False
    with (
        (result_dir / "stdout.log").open("wb") as stdout,
        (result_dir / "stderr.log").open("wb") as stderr,
    ):
        process = subprocess.Popen(
            command,
            cwd=out,
            stdin=subprocess.DEVNULL,
            env=runtime_env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except BaseException:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
    serial = (result_dir / "stdout.log").read_text(errors="replace")
    markers = ["USP_ZEPHYR_BOOT_OK", "USP_TIMER_IRQ_OK", "USP_UART_PLIC_IRQ_OK", "USP_TEST_PASS"]
    if osname == "linux":
        markers = [
            "OpenSBI",
            "Linux version 6.12",
            "USP_LINUX_USERSPACE_OK",
            "USP_SHARED_WORKLOAD_OK",
        ]
    if project.manifest["profile"] == "usp-rv64-hybrid-v1":
        markers.append("USP_TLM_RTL_IRQ_OK")
    checks = {marker: marker in serial for marker in markers}
    report = {
        "status": "PASSED"
        if all(checks.values()) and process.returncode == 0 and not timed_out
        else "FAILED",
        "backend": "qemu",
        "machine": "usp-rv64",
        "os": osname,
        "fidelity": "functional",
        "assertions": checks,
        "returncode": process.returncode,
        "timed_out": timed_out,
        "wall_time_seconds": time.monotonic() - started,
        "simulated_time_seconds": None,
        "ir_sha256": identity(project),
        "generated_manifest_sha256": digest(out / "manifest.json"),
        "qemu_binary_sha256": digest(binary),
        "firmware_sha256": digest(elf),
        "qemu_source_commit": QEMU_SHA,
        "software_source": json.loads((out / software_record).read_text()),
        "bindings": resolve(project),
        "limitations": [
            "Single hart, profile-limited software bring-up",
            "No Yocto, gem5, hybrid or timing equivalence claim",
        ],
    }
    write_json(result_dir / "report.json", report)
    (result_dir / "report.md").write_text(
        "# USP software acceptance\n\nStatus: **"
        + report["status"]
        + "**\n\nMachine: usp-rv64; fidelity: functional; simulated cycles unavailable.\n\n"
        + "\n".join(f"- {k}: {v}" for k, v in checks.items())
        + "\n\nSerial:\n\n```text\n"
        + serial
        + "\n```\n"
    )
    for name in [
        "manifest.json",
        "models.lock.json",
        "soc-ir.json",
        "qemu-build.json",
        software_record,
    ]:
        shutil.copyfile(out / name, result_dir / name)
    return report


def run_zephyr(project, out, result_dir, timeout=20):
    return run_custom(project, out, result_dir, timeout, "zephyr")
