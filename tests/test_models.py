import json
import os
import socket
import struct
import subprocess
import time
from pathlib import Path

import pytest

from socsim.hybrid import run_hybrid
from socsim.project import load_project

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def models():
    value = os.environ.get("USP_MODEL_DIR")
    if not value:
        if os.environ.get("USP_REQUIRE_MODELS") == "1":
            pytest.fail("USP_MODEL_DIR is required for model conformance")
        pytest.skip("optional SystemC/Verilator source models are not configured")
    return Path(value).resolve()


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["tlm", "rtl"])
def test_fifo_errors_reset_and_order(models, mode, tmp_path):
    binary = models / ("fifo-tlm-server" if mode == "tlm" else "rtl/Vusp_fifo")
    sock_path = tmp_path / "socket"
    with (tmp_path / "model.log").open("wb") as stream:
        process = subprocess.Popen(
            [str(binary), str(sock_path), str(tmp_path / "trace")], stdout=stream, stderr=stream
        )
        try:
            deadline = time.monotonic() + 5
            while not sock_path.exists():
                assert process.poll() is None
                assert time.monotonic() < deadline
                time.sleep(0.01)
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(2)
                client.connect(str(sock_path))
                vectors = [(2, 0, 0, 0, 0, 0), (0, 4, 0, 0, 0, 0)]
                vectors += [(1, 0, n, n, 0, 1) for n in range(1, 5)]
                vectors += [(1, 0, 5, 5, 1, 1)]
                vectors += [(0, 0, 0, n, 0, int(n < 4)) for n in range(1, 5)]
                vectors += [
                    (0, 0, 0, 0, 1, 0),
                    (0, 12, 0, 0, 1, 0),
                    (1, 0, 123, 123, 0, 1),
                    (2, 0, 0, 0, 0, 0),
                    (0, 4, 0, 0, 0, 0),
                ]
                for sequence, (op, offset, data, value, status, irq) in enumerate(vectors, 1):
                    client.sendall(struct.pack("<5I", 0x31505355, sequence, op, offset, data))
                    response = b""
                    while len(response) < 20:
                        part = client.recv(20 - len(response))
                        assert part
                        response += part
                    assert struct.unpack("<5I", response) == (
                        0x31505355,
                        sequence,
                        status,
                        value,
                        irq,
                    )
            process.wait(timeout=5)
            assert process.returncode == 0
            trace = [json.loads(line) for line in (tmp_path / "trace").read_text().splitlines()]
            assert trace[2]["time_ps"] == 30000
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["tlm", "rtl"])
def test_guest_bridge_irq_and_replay(models, mode, tmp_path):
    generated = os.environ.get("USP_HYBRID_GENERATED_DIR")
    if not generated:
        if os.environ.get("USP_REQUIRE_MODELS") == "1":
            pytest.fail("USP_HYBRID_GENERATED_DIR required for guest bridge proof")
        pytest.skip("custom hybrid machine is not built")
    project = load_project(ROOT / "examples/rv64-hybrid")
    for name in ["first", "replay"]:
        report = run_hybrid(project, Path(generated).resolve(), models, tmp_path / name, mode)
        assert report["status"] == "PASSED"
        assert report["assertions"]["USP_TLM_RTL_IRQ_OK"]
        assert report["assertions"]["bridge_mmio_and_irq"]
    first = (tmp_path / "first/bridge-trace.jsonl").read_bytes()
    assert first == (tmp_path / "replay/bridge-trace.jsonl").read_bytes()
