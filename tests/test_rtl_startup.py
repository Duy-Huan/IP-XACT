import subprocess
import sys

import pytest

from socsim.domain import ContractError
from socsim.rtl_cpu import wait_socket


def server(path, delay):
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import socket,sys,time; time.sleep(float(sys.argv[2])); "
            "s=socket.socket(socket.AF_UNIX); s.bind(sys.argv[1]); s.listen(1); sys.stdin.read()",
            str(path),
            str(delay),
        ],
        stdin=subprocess.PIPE,
    )


def stop(process):
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=5)
    if process.stdin:
        process.stdin.close()


def test_real_delayed_native_socket_is_awaited(tmp_path):
    path = tmp_path / "bus.sock"
    process = server(path, 0.1)
    try:
        wait_socket(path, process, 5)
        assert path.is_socket() and process.poll() is None
    finally:
        stop(process)


def test_real_native_socket_startup_is_bounded(tmp_path):
    process = server(tmp_path / "bus.sock", 5)
    try:
        with pytest.raises(TimeoutError, match="socket startup deadline exceeded"):
            wait_socket(tmp_path / "bus.sock", process, 0.05)
    finally:
        stop(process)


def test_actual_dead_native_server_reports_its_exit_code(tmp_path):
    process = subprocess.Popen([sys.executable, "-c", "raise SystemExit(23)"])
    try:
        with pytest.raises(ContractError, match="exited before opening its socket .*23"):
            wait_socket(tmp_path / "bus.sock", process, 5)
    finally:
        stop(process)
