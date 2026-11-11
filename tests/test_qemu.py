import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from socsim.domain import ContractError
from socsim.project import load_project
from socsim.runner import run_qemu

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def firmware(tmp_path_factory):
    missing = [t for t in ("qemu-system-riscv64", "riscv64-unknown-elf-gcc") if not shutil.which(t)]
    if missing:
        if os.environ.get("SOCSIM_REQUIRE_INTEGRATION") == "1":
            pytest.fail(f"required tools missing: {missing}")
        pytest.skip(f"integration tools missing: {missing}")
    directory = tmp_path_factory.mktemp("firmware")
    for name, source in {
        "boot": (ROOT / "examples/riscv-virt/firmware/boot.S").read_text(),
        "hang": ".section .text.init\n.globl _start\n_start: j _start\n",
        "fail": (
            ".section .text.init\n.globl _start\n_start:\n"
            " li t0, 0x100000\n li t1, 0x13333\n sw t1, 0(t0)\n1: j 1b\n"
        ),
    }.items():
        assembly = directory / f"{name}.S"
        assembly.write_text(source)
        subprocess.run(
            [
                "riscv64-unknown-elf-gcc",
                "-march=rv64imac",
                "-mabi=lp64",
                "-nostdlib",
                "-nostartfiles",
                "-Wl,--build-id=none",
                "-T",
                str(ROOT / "examples/riscv-virt/firmware/link.ld"),
                str(assembly),
                "-o",
                str(directory / f"{name}.elf"),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    return directory


@pytest.mark.integration
def test_real_guest_boot_and_evidence(project_dir, tmp_path, firmware):
    output = tmp_path / "run"
    report = run_qemu(load_project(project_dir), firmware / "boot.elf", output)
    assert report["status"] == "PASSED"
    assert report["returncode"] == 0
    assert report["assertions"] == {"guest_boot_marker": True, "finisher_exit_zero": True}
    assert report["simulated_time_seconds"] is None
    assert (output / "stdout.log").read_text() == "SOCSIM_BOOT_OK\n"
    assert (output / "report.md").is_file()
    assert json.loads((output / "report.json").read_text())["firmware_sha256"]
    assert len(list((output / "inputs/ipxact").glob("*.xml"))) == 5
    with pytest.raises(ContractError, match="new writable"):
        run_qemu(load_project(project_dir), firmware / "boot.elf", output)


@pytest.mark.integration
def test_real_timeout_is_failure(project_dir, tmp_path, firmware):
    report = run_qemu(load_project(project_dir), firmware / "hang.elf", tmp_path / "timeout", 0.2)
    assert report["status"] == "FAILED"
    assert report["timed_out"]
    assert report["returncode"] < 0


@pytest.mark.integration
def test_real_guest_failure_exit_is_failure(project_dir, tmp_path, firmware):
    report = run_qemu(load_project(project_dir), firmware / "fail.elf", tmp_path / "fail")
    assert report["status"] == "FAILED"
    assert report["returncode"] == 1
    assert not report["timed_out"]


def test_invalid_firmware_rejected(project_dir, tmp_path):
    path = tmp_path / "invalid.elf"
    path.write_text("fake output is not firmware")
    with pytest.raises(ContractError, match="RV64 ELF"):
        run_qemu(load_project(project_dir), path, tmp_path / "evidence")


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), 301])
def test_invalid_timeout_rejected(project_dir, tmp_path, timeout):
    with pytest.raises(ContractError, match="timeout"):
        run_qemu(load_project(project_dir), tmp_path / "missing", tmp_path / "evidence", timeout)


@pytest.mark.integration
def test_cli_sigterm_reaps_owned_guest(project_dir, tmp_path, firmware):
    import signal
    import sys
    import time

    output = tmp_path / "cancelled"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "socsim.cli",
            "run",
            str(project_dir),
            "--firmware",
            str(firmware / "hang.elf"),
            "--out",
            str(output),
            "--timeout",
            "120",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        child = None
        while time.monotonic() < deadline:
            assert process.poll() is None
            if (output / "command.json").exists():
                children = subprocess.run(
                    ["ps", "--ppid", str(process.pid), "-o", "pid="], capture_output=True, text=True
                ).stdout.split()
                if children:
                    child = int(children[0])
                    break
            time.sleep(0.01)
        assert child is not None
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 143
        assert not Path(f"/proc/{child}").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
