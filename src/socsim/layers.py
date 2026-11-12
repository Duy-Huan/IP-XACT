"""Bare-metal/RTOS/Zephyr/Linux layers on one SystemC-backed virtual SoC."""

import argparse
import hashlib
import json
import os
import shutil
import signal
import struct
import subprocess
import tempfile
import time
from pathlib import Path
from string import Template

from .cpu_platform import cpu_selection
from .domain import ContractError
from .execution import cleanup_on_termination
from .execution_plan import compile_execution_plan, validate_bridge_trace
from .freertos import acceptance as freertos_acceptance
from .freertos import verify_freertos
from .gem5 import python_environment
from .ipblocks import verify_models
from .ipxact import digest
from .performance import (
    build_counter,
    enable_qemu_counter,
    gem5_metrics,
    qemu_metrics,
    verify_counter,
)
from .pipeline import execute, identity, verify_generated
from .project import inside, load_project
from .runner import tool_version, write_json
from .unified import MARKERS, binding_plan, board_contract, trace_checks, verify_bridge
from .unified import RESOURCES as UNIFIED_RESOURCES

RESOURCES = Path(__file__).parent / "resources/layers"
LAYERS = ("baremetal", "freertos", "zephyr", "linux", "boot-sequence")


def build_baremetal(project, generated, cross_compile="riscv64-unknown-elf-"):
    verify_generated(project, generated)
    board = board_contract(project)
    out = generated / "baremetal"
    out.mkdir(exist_ok=True)
    shutil.copy2(generated / "soc.h", out / "soc.h")
    shutil.copy2(generated / "memory.ld", out / "memory.ld")
    values = {
        "gcd_irq": board["cygcd_irq"],
        "gpio_irq": board["otgpio_irq"],
        "timer_ticks": board["timebase_hz"] // 1000,
    }
    for name in ("firmware.c.in", "firmware.ld.in"):
        (out / name.removesuffix(".in")).write_text(
            Template((RESOURCES / name).read_text()).substitute(values)
        )
    cc, version = tool_version(cross_compile + "gcc")
    command = [
        cc,
        "-march=" + project.ir.cpu.isa,
        "-mabi=lp64",
        "-mcmodel=medany",
        "-msmall-data-limit=0",
        "-mno-relax",
        "-O2",
        "-ffreestanding",
        "-nostdlib",
        "-fno-builtin",
        "-fno-stack-protector",
        "-Wl,--build-id=none,--no-relax",
        "-I",
        str(out),
        "-T",
        str(out / "firmware.ld"),
        str(RESOURCES / "start.S"),
        str(out / "firmware.c"),
        "-lgcc",
        "-o",
        str(out / "firmware.elf"),
    ]
    execute(command, out, out / "build.log", 60)
    r = {
        "generated_manifest_sha256": digest(generated / "manifest.json"),
        "resources": {p.name: digest(p) for p in RESOURCES.iterdir() if p.is_file()},
        "compiler": version,
        "compiler_sha256": digest(Path(cc)),
        "elf_sha256": digest(out / "firmware.elf"),
        "files": {
            p.name: digest(p) for p in out.iterdir() if p.is_file() and p.name != "build.json"
        },
    }
    write_json(out / "build.json", r)
    return r


def software(project, generated, layer, artifact=None):
    verify_generated(project, generated)
    board_contract(project)
    if layer not in LAYERS:
        raise ContractError("unsupported software layer")
    if artifact is not None:
        from elftools.common.exceptions import ELFError
        from elftools.elf.elffile import ELFFile

        elf = Path(artifact["elf"]).resolve()
        if digest(elf) != artifact["sha256"]:
            raise ContractError("modified external firmware")
        windows = [w for i in project.ir.instances if i.name in {"ram", "rom"} for w in i.windows]
        with elf.open("rb") as stream:
            try:
                image = ELFFile(stream)
            except ELFError as exc:
                raise ContractError("invalid external ELF container") from exc
            if (
                image.elfclass != 64
                or not image.little_endian
                or image["e_machine"] != "EM_RISCV"
                or image["e_entry"] != project.ir.cpu.entry
            ):
                raise ContractError("external ELF container/reset-entry contract mismatch")
            segments = [s for s in image.iter_segments() if s["p_type"] == "PT_LOAD"]
            if not segments or any(
                not any(
                    w.base <= s["p_paddr"] and s["p_paddr"] + s["p_memsz"] <= w.base + w.size
                    for w in windows
                )
                for s in segments
            ):
                raise ContractError("external ELF load segments escape owned ROM/RAM")
        if "dtb" in artifact and digest(Path(artifact["dtb"])) != artifact["dtb_sha256"]:
            raise ContractError("modified external DTB")
        return elf, {
            "kind": "external-artifact",
            "elf_sha256": digest(elf),
            "acceptance": artifact,
            "scope": "owner-declared markers and real finisher completion",
        }
    if layer == "baremetal":
        r = json.loads((generated / "baremetal/build.json").read_text())
        resources = {p.name: digest(p) for p in RESOURCES.iterdir() if p.is_file()}
        if r["resources"] != resources:
            raise ContractError("stale bare-metal firmware resources")
        for name, sha in r["files"].items():
            if digest(inside(generated / "baremetal", name)) != sha:
                raise ContractError("modified bare-metal artifact")
        elf = generated / "baremetal/firmware.elf"
    elif layer == "freertos":
        r = verify_freertos(project, generated)
        elf = generated / "freertos/firmware.elf"
    elif layer == "zephyr":
        r = json.loads((generated / "zephyr-build.json").read_text())
        elf = generated / "zephyr-build/zephyr/zephyr.elf"
    else:
        r = json.loads((generated / "linux-build.json").read_text())
        elf = generated / "linux/opensbi/platform/generic/firmware/fw_payload.elf"
        for relative, key in (
            ("soc.dtb", "dtb_sha256"),
            ("init", "init_sha256"),
            ("workload", "workload_sha256"),
            ("kernel/arch/riscv/boot/Image", "image_sha256"),
        ):
            if digest(generated / "linux" / relative) != r[key]:
                raise ContractError("modified Linux boot artifact")
    if (
        r["generated_manifest_sha256"] != digest(generated / "manifest.json")
        or digest(elf) != r["elf_sha256"]
    ):
        raise ContractError("stale/mixed software and hardware identity")
    return elf, r


def guest_checks(layer, serial, trace):
    if layer == "zephyr":
        return {**{m: serial.count(m) == 1 for m in MARKERS}, **trace_checks(trace)}
    if layer == "baremetal":
        markers = [
            "USP_BAREMETAL_BOOT_OK",
            "USP_BAREMETAL_MEMORY_OK",
            "USP_TIMER_IRQ_OK",
            "USP_CHIPYARD_GCD_IRQ_OK",
            "USP_OPENTITAN_GPIO_IRQ_OK",
            "USP_TEST_PASS",
        ]
        return {**{m: serial.count(m) == 1 for m in markers}, **trace_checks(trace)}
    if layer == "freertos":
        return freertos_acceptance(serial)[0]
    markers = [
        "OpenSBI v1.6",
        "Linux version 6.12",
        "Run /init as init process",
        "USP_SHARED_WORKLOAD_OK",
        "USP_LINUX_USERSPACE_OK",
    ]
    positions = [serial.find(m) for m in markers]
    return {
        **{m: serial.count(m) == 1 for m in markers},
        "boot_stage_order": all(p >= 0 for p in positions) and positions == sorted(positions),
        "no_userspace_failure": "USP_LINUX_USERSPACE_FAIL" not in serial,
    }


@cleanup_on_termination
def run_layer(
    project,
    generated,
    models,
    engine,
    layer,
    out,
    bridge=None,
    counter=None,
    cost=None,
    timeout=180,
    cpu=None,
    artifact=None,
):
    if (
        engine not in {"qemu", "gem5"}
        or type(timeout) not in (int, float)
        or not 0 < timeout <= 300
    ):
        raise ContractError("unsupported software execution engine or timeout")
    generated, models, out = generated.resolve(), models.resolve(), out.resolve()
    elf, sw = software(project, generated, layer, artifact)
    dtb = Path(artifact["dtb"]) if artifact and "dtb" in artifact else generated / "linux/soc.dtb"
    needs_dtb = "dtb" in artifact if artifact else layer in {"linux", "boot-sequence"}
    model_record = verify_models(models)
    if cpu is None:
        cpu = cpu_selection(
            {
                "engine": engine,
                "cpu": {
                    "provider": engine,
                    "model": "rv64" if engine == "qemu" else "timing-simple",
                },
            },
            engine,
        )
    else:
        expected = cpu_selection(
            {
                "engine": engine,
                "cpu": {
                    "provider": cpu["provider"],
                    "model": cpu["model"],
                    "clock_hz": cpu["clock_hz"],
                },
            },
            engine,
        )
        if expected != cpu:
            raise ContractError("unsupported or modified CPU/engine adapter")
    config = {
        "engine": engine,
        "cpu": {"provider": cpu["provider"], "model": cpu["model"], "clock_hz": cpu["clock_hz"]},
        "workloads": [layer],
    }
    execution = compile_execution_plan(
        project, config, cpu, binding_plan(project, engine)["bindings"]
    )
    execution.require_executable()
    if engine == "gem5":
        if bridge is None:
            raise ContractError("gem5 layers require --bridge")
        bridge = bridge.resolve()
        engine_record = verify_bridge(bridge)
    else:
        if counter is None:
            raise ContractError("QEMU layers require --counter")
        counter = counter.resolve()
        engine_record = json.loads((generated / "qemu-build.json").read_text())
        if engine_record["generated_manifest_sha256"] != digest(
            generated / "manifest.json"
        ) or engine_record["binary_sha256"] != digest(generated / "qemu-build/qemu-system-riscv64"):
            raise ContractError("modified QEMU layer engine")
        verify_counter(counter)
    out.mkdir(parents=True, exist_ok=False)
    if needs_dtb:
        if dtb.stat().st_size > 0x200000 or dtb.read_bytes()[:4] != bytes.fromhex("d00dfeed"):
            raise ContractError("invalid or oversized boot DTB")
        shutil.copy2(dtb, out / "software.dtb")
        dtb = out / "software.dtb"
        if digest(dtb) != (artifact["dtb_sha256"] if artifact else sw["dtb_sha256"]):
            raise ContractError("DTB changed during software staging")
    write_json(out / "execution-plan.json", execution.to_dict())
    counter_record = verify_counter(counter) if engine == "qemu" else None
    if counter_record:
        write_json(out / "counter-build.json", counter_record)
    contract = board_contract(project, 10000000000000)
    if engine == "gem5":
        contract.update(
            cpu_model=cpu["gem5_cpu"], mem_mode=cpu["mem_mode"], cpu_clock_hz=cpu["clock_hz"]
        )
    with tempfile.TemporaryDirectory(prefix="usp-layers-") as name:
        sock = Path(name) / "bus.sock"
        with (out / "model.log").open("wb") as stream:
            model = subprocess.Popen(
                [str(models / "rtl/Vusp_ipblocks"), str(sock), str(out / "bridge-trace.jsonl")],
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 5
                while not sock.exists():
                    if model.poll() is not None or time.monotonic() > deadline:
                        raise ContractError("software layer SystemC model not ready")
                    time.sleep(0.01)
                env = python_environment()
                if engine == "qemu":
                    env["USP_BRIDGE_SOCKET"] = str(sock)
                    argv = [
                        str(generated / "qemu-build/qemu-system-riscv64"),
                        "-machine",
                        "usp-rv64",
                        "-cpu",
                        cpu["qemu_cpu"],
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
                        "-plugin",
                        str(counter / "counter.so") + ",output=" + str(out / "qemu-counters.json"),
                    ]
                    if needs_dtb:
                        argv += ["-dtb", str(dtb)]
                else:
                    dtb_address = 0
                    if needs_dtb:
                        dtb_address = contract["ram_base"] + contract["ram_size"] - 0x200000
                        # A loadable ELF section places the exact verified DTB in RAM.
                        objcopy, _ = tool_version("riscv64-unknown-elf-objcopy")
                        execute(
                            [
                                objcopy,
                                "-I",
                                "binary",
                                "-O",
                                "elf64-littleriscv",
                                "-B",
                                "riscv",
                                "--rename-section",
                                ".data=.dtb",
                                "--change-section-vma",
                                ".dtb=" + hex(dtb_address),
                                str(dtb),
                                str(out / "dtb.o"),
                            ],
                            out,
                            out / "dtb-image.log",
                            60,
                        )
                        (out / "dtb.ld").write_text(
                            "SECTIONS { . = " + hex(dtb_address) + "; .dtb : { *(.dtb) } }\n"
                        )
                        execute(
                            [
                                "riscv64-unknown-elf-ld",
                                "-T",
                                str(out / "dtb.ld"),
                                str(out / "dtb.o"),
                                "-o",
                                str(out / "dtb.elf"),
                            ],
                            out,
                            out / "dtb-link.log",
                            60,
                        )
                        contract["ram_image"] = str(out / "dtb.elf")
                    # Actual ROM: mhartid -> a0, DTB pointer -> a1, jump to IR entry.
                    (out / "rom.bin").write_bytes(
                        struct.pack(
                            "<6IQQ",
                            0x00000297,
                            0xF1402573,
                            0x0202B583,
                            0x0182B283,
                            0x00028067,
                            0x00000013,
                            contract["entry"],
                            dtb_address,
                        )
                    )
                    contract["rom_image"] = str(out / "rom.bin")
                    write_json(out / "board.json", contract)
                    shutil.copy2(UNIFIED_RESOURCES / "board.py.in", out / "board.py")
                    argv = [
                        str(bridge / "gem5.opt"),
                        "--listener-mode=off",
                        "--outdir=" + str(out),
                        str(out / "board.py"),
                        str(out / "board.json"),
                        str(sock),
                        str(elf),
                        str(out / "exit.json"),
                    ]
                write_json(out / "command.json", {"argv": argv, "cwd": str(out)})
                timed_out = False
                started = time.monotonic()
                with (
                    (out / "stdout.log").open("wb") as stdout,
                    (out / "stderr.log").open("wb") as stderr,
                ):
                    guest = subprocess.Popen(
                        argv,
                        cwd=out,
                        env=env,
                        stdout=stdout,
                        stderr=stderr,
                        start_new_session=True,
                    )
                    try:
                        rc = guest.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        os.killpg(guest.pid, signal.SIGKILL)
                        rc = guest.wait()
                    finally:
                        if guest.poll() is None:
                            os.killpg(guest.pid, signal.SIGKILL)
                            guest.wait()
                elapsed = time.monotonic() - started
                try:
                    model.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(model.pid, signal.SIGKILL)
                    model.wait()
            finally:
                if model.poll() is None:
                    os.killpg(model.pid, signal.SIGKILL)
                    model.wait()
    serial = (
        (out / "stdout.log").read_bytes()
        if engine == "qemu"
        else b"".join(p.read_bytes() for p in sorted(out.glob("*.terminal")))
    )
    (out / "uart.log").write_bytes(serial)
    text = serial.decode(errors="replace").replace("\r", "")
    trace = (
        [json.loads(s) for s in (out / "bridge-trace.jsonl").read_text().splitlines()]
        if (out / "bridge-trace.jsonl").exists()
        else []
    )
    checks = {
        **validate_bridge_trace(trace),
        **(
            {m: text.count(m) == 1 for m in artifact["expected_markers"]}
            if artifact
            else guest_checks(layer, text, trace)
        ),
        "engine_exit": rc == 0,
        "model_exit": model.returncode == 0,
    }
    if engine == "gem5":
        exit_info = (
            json.loads((out / "exit.json").read_text()) if (out / "exit.json").exists() else {}
        )
        checks["guest_exit"] = (
            exit_info.get("cause") == "USP guest finisher" and exit_info.get("code") == 0
        )
    try:
        metrics = (
            gem5_metrics(out / "stats.txt")
            if engine == "gem5"
            else qemu_metrics(json.loads((out / "qemu-counters.json").read_text()), cost)
        )
        checks["performance_evidence"] = True
    except (ContractError, OSError, ValueError) as exc:
        metrics = {"status": "UNAVAILABLE", "reason": str(exc)}
        checks["performance_evidence"] = False
    report = {
        "schema_version": "1",
        "status": "PASSED"
        if all(checks.values()) and not timed_out
        else "TIMED_OUT"
        if timed_out
        else "FAILED",
        "engine": engine,
        "cpu_selection": cpu,
        "layer": layer,
        "checks": checks,
        "performance": metrics,
        "returncode": rc,
        "timed_out": timed_out,
        "host_elapsed_seconds": elapsed,
        "ir_sha256": identity(project),
        "elf_sha256": digest(elf),
        "software_build": sw,
        "engine_build": engine_record,
        "counter_build": counter_record,
        "model_binary_sha256": model_record["binary_sha256"],
        "full_architecture_complete": False,
        "ip_driver_coverage": "GCD/GPIO actual MMIO and IRQ"
        if layer in {"baremetal", "zephyr"}
        else "CPU/OS boot and engine-native peripherals; custom IP drivers unverified",
    }
    if artifact:
        report["ip_driver_coverage"] = "External firmware: owner-declared acceptance only"
    write_json(out / "performance.json", metrics)
    bindings = binding_plan(project, engine)
    bindings["cpu_selection"] = cpu
    for binding in bindings["bindings"]:
        if binding["instance"] == "rv64":
            binding["implementation"] = cpu.get("gem5_cpu", cpu.get("qemu_cpu"))
    write_json(out / "engine-bindings.json", bindings)
    shutil.copytree(models / "licenses", out / "licenses")
    write_json(out / "report.json", report)
    (out / "report.md").write_text(
        "# Software layer acceptance\n\n"
        + engine
        + " / "
        + layer
        + "\n\nStatus: **"
        + report["status"]
        + "**\n\n"
        + "\n".join(f"- {k}: {v}" for k, v in checks.items())
        + "\n\n"
        + "```json\n"
        + json.dumps(metrics, indent=2)
        + "\n```\n"
    )
    return report


def matrix_checks(out):
    ir_sha = hashlib.sha256(
        json.dumps(json.loads((out / "soc-ir.json").read_text()), sort_keys=True).encode()
    ).hexdigest()
    rows = {}
    for layer in LAYERS:
        q = json.loads((out / layer / "qemu/report.json").read_text())
        g = json.loads((out / layer / "gem5/report.json").read_text())
        rows[layer] = {
            "qemu_passed": q["status"] == "PASSED" and all(q["checks"].values()),
            "gem5_passed": g["status"] == "PASSED" and all(g["checks"].values()),
            "same_software": q["elf_sha256"]
            == g["elf_sha256"]
            == digest(out / "firmware" / (layer + ".elf")),
            "same_hardware": q["ir_sha256"] == g["ir_sha256"] == ir_sha,
            "same_models": q["model_binary_sha256"] == g["model_binary_sha256"],
        }
        for engine, r in (("qemu", q), ("gem5", g)):
            p = out / layer / engine
            try:
                actual = (
                    gem5_metrics(p / "stats.txt")
                    if engine == "gem5"
                    else qemu_metrics(
                        json.loads((p / "qemu-counters.json").read_text()),
                        r["performance"]["cost_model"],
                    )
                )
                rows[layer][engine + "_performance"] = actual == r["performance"]
            except (ContractError, OSError, ValueError, KeyError):
                rows[layer][engine + "_performance"] = False
    return rows


def run_matrix(project, generated, models, bridge, counter, out, cost=None):
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / "firmware").mkdir()
    write_json(out / "soc-ir.json", project.ir.to_dict())
    for layer in LAYERS:
        elf, _ = software(project, generated.resolve(), layer)
        shutil.copy2(elf, out / "firmware" / (layer + ".elf"))
        for engine in ("qemu", "gem5"):
            run_layer(
                project,
                generated,
                models,
                engine,
                layer,
                out / layer / engine,
                bridge=bridge,
                counter=counter,
                cost=cost,
            )
    checks = matrix_checks(out)
    passed = all(all(c.values()) for c in checks.values())
    result = {
        "schema_version": "1",
        "status": "PASSED" if passed else "FAILED",
        "scope": "Five SW layers on two CPU engines with common IR/ELF/models",
        "checks": checks,
        "full_architecture_complete": False,
        "files": {
            p.relative_to(out).as_posix(): digest(p) for p in sorted(out.rglob("*")) if p.is_file()
        },
    }
    write_json(out / "report.json", result)
    (out / "report.md").write_text(
        "# Software integration matrix\n\nStatus: **"
        + result["status"]
        + "**\n\n"
        + "\n".join(f"- {k}: {v}" for k, v in checks.items())
        + "\n\nQEMU cycle/CPI/IPC fields are analytical estimates. Gem5 uses native statistics.\n"
    )
    return result


def verify_matrix(out):
    out = out.resolve()
    r = json.loads((out / "report.json").read_text())
    if r["schema_version"] != "1" or r["full_architecture_complete"] is not False:
        raise ContractError("invalid software matrix completion claim")
    required = {f"{layer}/{engine}/report.json" for layer in LAYERS for engine in ("qemu", "gem5")}
    if not required.issubset(r["files"]):
        raise ContractError("incomplete software matrix evidence")
    for name, sha in r["files"].items():
        if digest(inside(out, name)) != sha:
            raise ContractError("modified software matrix evidence")
    checks = matrix_checks(out)
    passed = all(all(c.values()) for c in checks.values())
    if r["checks"] != checks or r["status"] != ("PASSED" if passed else "FAILED"):
        raise ContractError("invalid software/performance acceptance claim")
    return r


def main(argv):
    parser = argparse.ArgumentParser(prog="usp layers")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("counter-build")
    b.add_argument("--source-qemu", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    b = sub.add_parser("enable-qemu")
    b.add_argument("project", type=Path)
    b.add_argument("--generated", type=Path, required=True)
    b.add_argument("--source-qemu", type=Path, required=True)
    b.add_argument("--jobs", type=int, choices=range(1, 5), default=2)
    b = sub.add_parser("baremetal-build")
    b.add_argument("project", type=Path)
    b.add_argument("--generated", type=Path, required=True)
    b.add_argument("--cross-compile", default="riscv64-unknown-elf-")
    for command in ("run", "matrix"):
        p = sub.add_parser(command)
        p.add_argument("project", type=Path)
        for key in ("generated", "models", "out", "bridge", "counter"):
            p.add_argument(
                "--" + key,
                type=Path,
                required=key not in {"bridge", "counter"} or command == "matrix",
            )
        p.add_argument("--cost-model", type=Path)
        if command == "run":
            p.add_argument("--engine", choices=("qemu", "gem5"), required=True)
            p.add_argument("--os", choices=LAYERS, required=True)
    sub.add_parser("verify").add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "counter-build":
            r = build_counter(args.source_qemu, args.out)
        elif args.command == "enable-qemu":
            r = {
                "binary": str(
                    enable_qemu_counter(
                        load_project(args.project),
                        args.generated.resolve(),
                        args.source_qemu,
                        args.jobs,
                    )
                )
            }
        elif args.command == "baremetal-build":
            r = build_baremetal(
                load_project(args.project), args.generated.resolve(), args.cross_compile
            )
        elif args.command == "verify":
            r = verify_matrix(args.run_dir)
        else:
            cost = json.loads(args.cost_model.read_text()) if args.cost_model else None
            if args.command == "run":
                r = run_layer(
                    load_project(args.project),
                    args.generated,
                    args.models,
                    args.engine,
                    args.os,
                    args.out,
                    bridge=args.bridge,
                    counter=args.counter,
                    cost=cost,
                )
            else:
                r = run_matrix(
                    load_project(args.project),
                    args.generated,
                    args.models,
                    args.bridge,
                    args.counter,
                    args.out,
                    cost=cost,
                )
        print(json.dumps(r, indent=2))
        return 0 if r.get("status", "PASSED") == "PASSED" else 1
    except (ContractError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}))
        return 2
