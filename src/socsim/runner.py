"""Bounded real processes, raw logs, hashes and explicit test outcomes."""

import json
import math
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

from . import __version__
from .backends import qemu_plan
from .domain import ContractError
from .ipxact import digest
from .project import Project


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def tool_version(binary: str) -> tuple[str, str]:
    path = shutil.which(binary)
    if not path:
        raise ContractError(f"BLOCKED: tool not found: {binary}")
    try:
        result = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContractError(f"BLOCKED: cannot identify {binary}: {exc}") from exc
    return str(Path(path).resolve()), result.stdout.splitlines()[0]


def run_qemu(project: Project, firmware: Path, output: Path, timeout: float = 10) -> dict:
    plan = qemu_plan(project, "qemu")
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ContractError("timeout must be finite and between 0 and 300 seconds")
    firmware = firmware.resolve()
    try:
        with firmware.open("rb") as stream:
            header = stream.read(20)
    except OSError as exc:
        raise ContractError(f"firmware unavailable: {exc}") from exc
    if len(header) != 20 or header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\xf3\x00":
        raise ContractError("firmware must be a little-endian RV64 ELF")
    binary, version = tool_version("qemu-system-riscv64")
    match = re.search(r"version (\d+\.\d+\.\d+)", version)
    if not match or match[1] != plan["qemu_version"]:
        raise ContractError(
            f"toolchain mismatch: expected QEMU {plan['qemu_version']}; got {version}"
        )
    # Caller chooses a fresh directory: never destroy earlier evidence.
    output = output.resolve()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise ContractError(f"output must be a new writable directory: {exc}") from exc
    command = [binary, *plan["arguments"], "-kernel", str(firmware)]
    write_json(output / "soc-ir.json", project.ir.to_dict())
    write_json(output / "plan.json", plan)
    write_json(output / "command.json", {"argv": command, "cwd": str(project.root)})
    # Preserve the exact input files; do not scan for unrelated proprietary files.
    for name, expected_hash in project.ir.sources:
        source = project.root / name
        if digest(source) != expected_hash:
            raise ContractError(f"input changed since elaboration: {name}")
        target = output / "inputs" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    started = time.monotonic()
    timed_out = False
    launch_error = None
    process = None
    with (output / "stdout.log").open("wb") as stdout, (output / "stderr.log").open("wb") as stderr:
        try:
            process = subprocess.Popen(
                command,
                cwd=project.root,
                stdin=subprocess.DEVNULL,
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
        except OSError as exc:
            launch_error = str(exc)
        except BaseException:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
    elapsed = time.monotonic() - started
    serial = (output / "stdout.log").read_text(errors="replace")
    rc = process.returncode if process is not None else None
    passed = not timed_out and launch_error is None and rc == 0 and "SOCSIM_BOOT_OK" in serial
    report = {
        "status": "PASSED" if passed else "FAILED",
        "backend": "qemu",
        "platform_version": __version__,
        "simulator": version,
        "binary": binary,
        "simulator_sha256": digest(Path(binary)),
        "firmware_sha256": digest(firmware),
        "ir_sha256": digest(output / "soc-ir.json"),
        "fidelity": "functional",
        "returncode": rc,
        "timed_out": timed_out,
        "launch_error": launch_error,
        "wall_time_seconds": elapsed,
        "simulated_time_seconds": None,
        "simulated_time_reason": "QEMU TCG functional execution has no calibrated cycle model",
        "assertions": {
            "guest_boot_marker": "SOCSIM_BOOT_OK" in serial,
            "finisher_exit_zero": rc == 0,
        },
        "bindings": plan["bindings"],
        "limitations": plan["limitations"],
        "inputs": dict(project.ir.sources),
    }
    write_json(output / "report.json", report)
    # Same evidence always renders identically; timing is a recorded observation.
    lines = [
        "# SoC simulation report",
        "",
        f"Status: **{report['status']}**",
        "",
        f"Simulator: {version}",
        "",
        "Fidelity: functional",
        "",
        f"Return code: {rc}; timeout: {timed_out}",
        "",
        f"Wall time: {elapsed:.6f} s; simulated time: unavailable (functional TCG)",
        "",
        f"Firmware SHA256: `{report['firmware_sha256']}`",
        "",
        "Bindings:",
        "",
        *[f"- {b['instance']}: {b['implementation']}" for b in plan["bindings"]],
        "",
        "Limitations:",
        "",
        *[f"- {s}" for s in plan["limitations"]],
        "",
        "Serial output is preserved verbatim in `stdout.log`; diagnostics in `stderr.log`.",
        "",
    ]
    (output / "report.md").write_text("\n".join(lines))
    return report
