"""Pinned FreeRTOS RV64 M-mode BSP and real preemption/PLIC acceptance."""

import json
import math
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path
from string import Template

from .domain import ContractError
from .execution import cleanup_on_termination
from .ipxact import digest
from .pipeline import (
    QEMU_SHA,
    execute,
    identity,
    resolve,
    verify_generated,
    verify_source,
)
from .runner import tool_version, write_json

FREERTOS_SHA = "0adc196d4bd52a2d91102b525b0aafc1e14a2386"
RESOURCES = Path(__file__).parent / "resources/freertos"
TEST_CASES = {"smoke": 0, "assert-failure": 1, "tick-disabled": 2}
MARKERS = (
    "USP_FREERTOS_BOOT_OK",
    "USP_FREERTOS_TIMER_IRQ_OK",
    "USP_FREERTOS_PREEMPTION_OK",
    "USP_FREERTOS_UART_PLIC_IRQ_OK",
    "USP_TEST_PASS",
)


def adapter_identity():
    # Software templates have their own identity; hardware generation is unchanged.
    return {
        "adapter_sha256": digest(Path(__file__)),
        "execution_sha256": digest(Path(__file__).with_name("execution.py")),
        "resources": {p.name: digest(p) for p in sorted(RESOURCES.iterdir()) if p.is_file()},
    }


def generate_bsp(project, out):
    """Generate an independent software BSP from verified hardware artifacts/IR."""
    verify_generated(project, out)
    if project.manifest["profile"] not in {"usp-rv64-v1", "usp-rv64-ips-v1"}:
        raise ContractError("FreeRTOS requires the non-hybrid base or source-IP profile")
    if project.ir.cpu is None:
        raise ContractError("FreeRTOS requires an explicit CPU contract")
    clint = next(i.windows[0].base for i in project.ir.instances if i.name == "clint")
    values = {
        "timebase_hz": project.ir.cpu.timebase_hz,
        "mtime": hex(clint + 0xBFF8),
        "mtimecmp": hex(clint + 0x4000),
        "entry": hex(project.ir.cpu.entry),
        "uart_irq": next(r.line for r in project.ir.interrupts if r.source == "uart"),
    }
    bsp = out / "freertos/bsp"
    bsp.mkdir(parents=True, exist_ok=True)
    for resource in sorted(RESOURCES.iterdir()):
        if not resource.is_file():
            continue
        if resource.suffix == ".in":
            (bsp / resource.stem).write_text(Template(resource.read_text()).substitute(values))
        else:
            shutil.copyfile(resource, bsp / resource.name)
    for name in ("soc.h", "memory.ld"):
        shutil.copyfile(out / name, bsp / name)
    return bsp


def build_freertos(project, out, source, cross_compile, test_case="smoke"):
    if test_case not in TEST_CASES:
        raise ContractError("unknown FreeRTOS acceptance test case")
    bsp = generate_bsp(project, out)
    try:
        source = verify_source(source, FREERTOS_SHA)
    except subprocess.CalledProcessError as exc:
        raise ContractError(
            "FreeRTOS source must be a clean Git checkout at the pinned revision"
        ) from exc
    compiler, version = tool_version(cross_compile + "gcc")
    for option, expected in (
        ("-dumpmachine", "riscv64-unknown-elf"),
        ("-dumpfullversion", "14.2.0"),
    ):
        try:
            probe = subprocess.run(
                [compiler, option], text=True, capture_output=True, timeout=10, check=True
            ).stdout.strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ContractError(f"FreeRTOS compiler probe failed: {option}") from exc
        if probe != expected:
            raise ContractError(f"FreeRTOS compiler {option}: expected {expected}, got {probe}")
    port = source / "portable/GCC/RISC-V"
    shutil.copyfile(source / "LICENSE.md", bsp / "FreeRTOS-LICENSE.md")
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
        "-march=" + project.ir.cpu.isa,
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
    directory = out / "freertos"
    for index, path in enumerate(sources):
        obj = directory / f"{index}-{path.stem}.o"
        execute(
            [compiler, *flags, "-c", str(path), "-o", str(obj)],
            bsp,
            out / f"logs/freertos-{index}-compile.log",
        )
        objects.append(obj)
    elf = directory / "firmware.elf"
    execute(
        [
            compiler,
            *flags,
            "-nostdlib",
            "-nostartfiles",
            "-Wl,--no-relax,--gc-sections,--build-id=none",
            "-Wl,-Map=" + str(directory / "firmware.map"),
            "-T",
            str(bsp / "linker.ld"),
            *objects,
            "-lgcc",
            "-o",
            str(elf),
        ],
        bsp,
        out / "logs/freertos-link.log",
    )
    write_json(
        out / "freertos-build.json",
        {
            "schema_version": "1",
            "generated_manifest_sha256": digest(out / "manifest.json"),
            "software_adapter": adapter_identity(),
            "kernel_version": "11.2.0",
            "kernel_source_commit": FREERTOS_SHA,
            "kernel_license": "MIT",
            "port": "GCC/RISC-V/RISCV_MTIME_CLINT_no_extensions",
            "compiler": {"path": compiler, "version": version, "sha256": digest(Path(compiler))},
            "compile_flags": flags,
            "test_case": test_case,
            "elf_sha256": digest(elf),
            "bsp_files": {p.name: digest(p) for p in sorted(bsp.iterdir()) if p.is_file()},
            "boot": "direct M-mode, one hart, static allocation, no FPU/vector context",
            "tick_hz": 1000,
            "mtime_hz": project.ir.cpu.timebase_hz,
        },
    )
    return elf


def verify_freertos(project, out):
    verify_generated(project, out)
    if project.manifest["profile"] not in {"usp-rv64-v1", "usp-rv64-ips-v1"}:
        raise ContractError("FreeRTOS runtime requires the non-hybrid base or source-IP profile")
    record = json.loads((out / "freertos-build.json").read_text())
    if (
        not isinstance(record, dict)
        or record.get("schema_version") != "1"
        or not isinstance(record.get("test_case"), str)
        or record.get("test_case") not in TEST_CASES
        or record.get("generated_manifest_sha256") != digest(out / "manifest.json")
        or record.get("software_adapter") != adapter_identity()
        or record.get("kernel_source_commit") != FREERTOS_SHA
    ):
        raise ContractError("stale FreeRTOS adapter/hardware/source identity; rebuild software")
    expected = {p.stem if p.suffix == ".in" else p.name for p in RESOURCES.iterdir() if p.is_file()}
    expected |= {"soc.h", "memory.ld", "FreeRTOS-LICENSE.md"}
    if not isinstance(record.get("bsp_files"), dict) or set(record["bsp_files"]) != expected:
        raise ContractError("incomplete FreeRTOS BSP identity")
    for name, sha in record["bsp_files"].items():
        if digest(out / "freertos/bsp" / name) != sha:
            raise ContractError(f"modified FreeRTOS BSP: {name}")
    if digest(out / "freertos/firmware.elf") != record.get("elf_sha256"):
        raise ContractError("modified FreeRTOS firmware")
    qemu = json.loads((out / "qemu-build.json").read_text())
    if (
        not isinstance(qemu, dict)
        or qemu.get("generated_manifest_sha256") != digest(out / "manifest.json")
        or qemu.get("source_commit") != QEMU_SHA
        or qemu.get("generated_machine_sha256") != digest(out / "machine.c")
        or qemu.get("binary_sha256") != digest(out / "qemu-build/qemu-system-riscv64")
    ):
        raise ContractError("stale/modified custom QEMU build")
    return record


def acceptance(serial):
    """Require guest assertions and observed scheduling/IRQ counters."""
    checks = {marker: marker in serial.splitlines() for marker in MARKERS}
    match = re.search(
        r"^USP_FREERTOS_METRICS ticks=(\d+) timer_irqs=(\d+) switches=(\d+) "
        r"worker0=(\d+) worker1=(\d+) uart_irqs=(\d+)$",
        serial,
        re.MULTILINE,
    )
    metrics = {}
    if match:
        metrics = dict(
            zip(
                ("ticks", "timer_irqs", "switches", "worker0", "worker1", "uart_irqs"),
                map(int, match.groups()),
                strict=True,
            )
        )
    checks["observed_preemption_and_irqs"] = bool(
        metrics
        and metrics["ticks"] >= 30
        and metrics["timer_irqs"] >= 30
        and metrics["switches"] >= 10
        and metrics["worker0"] > 0
        and metrics["worker1"] > 0
        and metrics["uart_irqs"] == 1
    )
    checks["no_guest_failure"] = "USP_FREERTOS_FAIL" not in serial
    return checks, metrics


@cleanup_on_termination
def run_freertos(project, out, result_dir, timeout=20):
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ContractError("invalid timeout")
    record = verify_freertos(project, out)
    binary = out / "qemu-build/qemu-system-riscv64"
    elf = out / "freertos/firmware.elf"
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
    write_json(result_dir / "command.json", {"argv": command, "cwd": str(out)})
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
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
    serial = (result_dir / "stdout.log").read_text(errors="replace")
    checks, metrics = acceptance(serial)
    checks["production_test_case"] = record["test_case"] == "smoke"
    report = {
        "status": "PASSED"
        if all(checks.values()) and process.returncode == 0 and not timed_out
        else "FAILED",
        "backend": "qemu",
        "machine": "usp-rv64",
        "os": "freertos",
        "acceptance_profile": "freertos-rv64-m-preempt-v1",
        "fidelity": "functional",
        "assertions": checks,
        "guest_metrics": metrics,
        "returncode": process.returncode,
        "timed_out": timed_out,
        "wall_time_seconds": time.monotonic() - started,
        "simulated_time_seconds": None,
        "ir_sha256": identity(project),
        "generated_manifest_sha256": digest(out / "manifest.json"),
        "qemu_binary_sha256": digest(binary),
        "qemu_source_commit": QEMU_SHA,
        "firmware_sha256": digest(elf),
        "software_source": record,
        "bindings": resolve(project),
        "limitations": [
            "Single-hart RV64 M-mode static-allocation acceptance BSP",
            "No FPU/vector, SMP, MPU, hybrid, generic driver or timing calibration claim",
            "Worker loop counts are guest observations, not calibrated performance",
        ],
    }
    write_json(result_dir / "report.json", report)
    (result_dir / "report.md").write_text(
        "# FreeRTOS acceptance\n\nStatus: **" + report["status"] + "**\n\n"
        "Machine: usp-rv64; fidelity: functional; CPU cycles unavailable.\n\n"
        + "\n".join(f"- {key}: {value}" for key, value in checks.items())
        + "\n\nGuest metrics: "
        + json.dumps(metrics, sort_keys=True)
        + "\n\n```text\n"
        + serial
        + "\n```\n"
    )
    for name in (
        "manifest.json",
        "models.lock.json",
        "soc-ir.json",
        "qemu-build.json",
        "freertos-build.json",
    ):
        shutil.copyfile(out / name, result_dir / name)
    shutil.copyfile(out / "freertos/bsp/FreeRTOS-LICENSE.md", result_dir / "FreeRTOS-LICENSE.md")
    return report
