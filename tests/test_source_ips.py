import json
import os
import shutil
import socket
import struct
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from socsim.cli import main
from socsim.domain import ContractError
from socsim.ipblocks import (
    CHIPYARD_SHA,
    OPENTITAN_SHA,
    run_ipblocks,
    select_gcd,
    verify_dependencies,
    verify_models,
)
from socsim.pipeline import generate, resolve, verify_generated
from socsim.project import load_project

ROOT = Path(__file__).resolve().parents[1]


def test_exact_source_models_and_generated_topology(tmp_path):
    project = load_project(ROOT / "examples/rv64-ips")
    models = {m["instance"]: m for m in resolve(project)}
    assert len(models) == 9
    assert models["cygcd"]["source"]["commit"] == CHIPYARD_SHA
    assert models["otgpio"]["source"]["commit"] == OPENTITAN_SHA
    assert models["cygcd"]["source"]["license"] == "BSD-3-Clause"
    assert models["otgpio"]["source"]["license"] == "Apache-2.0"
    assert models["cygcd"]["interrupts"] and models["otgpio"]["interrupts"]
    first = generate(project, tmp_path / "one")
    second = generate(project, tmp_path / "two")
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()
    assert verify_generated(project, first)
    machine = (first / "machine.c").read_text()
    assert "0x13000000, 0x14000000" in machine
    assert "{ 12, 13 }" in machine
    assert "usp.opentitan-gpio" in machine
    assert "usp.scfifo" not in machine
    assert not (first / "yocto").exists()


@pytest.mark.parametrize("change", ["same_irq", "missing_irq", "wrong_window", "wrong_binding"])
def test_invalid_source_ip_contracts_rejected(tmp_path, change):
    root = tmp_path / "project"
    shutil.copytree(ROOT / "examples/rv64-ips", root)
    if change in {"same_irq", "missing_irq"}:
        path = root / "hardware.yaml"
        value = yaml.safe_load(path.read_text())
        if change == "same_irq":
            value["interrupts"][-1]["line"] = 12
        else:
            value["interrupts"].pop()
        path.write_text(yaml.safe_dump(value))
    elif change == "wrong_window":
        path = root / "ipxact/otgpio.xml"
        path.write_text(path.read_text().replace("0x1000", "0x2000"))
    else:
        path = root / "models.yaml"
        value = yaml.safe_load(path.read_text())
        value["bindings"][-1]["implementation"] = "usp.systemc.fifo"
        path.write_text(yaml.safe_dump(value))
    with pytest.raises(ContractError):
        resolve(load_project(root))


def test_verbatim_source_selection_rejects_unknown_content(tmp_path):
    path = tmp_path / "generators/chipyard/src/main/scala/example/GCD.scala"
    path.parent.mkdir(parents=True)
    path.write_text("an arbitrary algorithm must not be accepted as Chipyard")
    with pytest.raises(ContractError, match="content mismatch"):
        select_gcd(tmp_path)


def test_maven_inventory_must_match_complete_content_lock(tmp_path):
    jar = tmp_path / "chisel_2.13-6.5.0.jar"
    jar.write_bytes(b"modified")
    with pytest.raises(ContractError, match="inventory/content"):
        verify_dependencies([jar])


def test_cli_requires_explicit_rtl_bridge(tmp_path, capsys):
    assert (
        main(
            [
                "test",
                str(ROOT / "examples/rv64-ips"),
                "--generated",
                str(tmp_path),
                "--bridge-models",
                str(tmp_path),
                "--out",
                str(tmp_path / "run"),
            ]
        )
        == 2
    )
    assert "--bridge-mode rtl" in json.loads(capsys.readouterr().out)["message"]


@pytest.mark.parametrize("timeout", [0, float("nan"), 301])
def test_invalid_timeout_before_starting_model(tmp_path, timeout):
    with pytest.raises(ContractError, match="timeout"):
        run_ipblocks(None, tmp_path, tmp_path, tmp_path / "run", timeout)
    assert not (tmp_path / "run").exists()


@pytest.fixture
def native_models():
    value = os.environ.get("USP_IP_MODELS_DIR")
    if not value:
        if os.environ.get("USP_REQUIRE_IPS") == "1":
            pytest.fail("source IP model build is required")
        pytest.skip("optional Chipyard/OpenTitan source models are not built")
    models = Path(value).resolve()
    verify_models(models)
    return models


@pytest.fixture
def model_bus(native_models, tmp_path):
    sock = tmp_path / "bus.sock"
    trace = tmp_path / "trace.jsonl"
    with (tmp_path / "model.log").open("wb") as log:
        process = subprocess.Popen(
            [str(native_models / "rtl/Vusp_ipblocks"), str(sock), str(trace)],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 5
            while not sock.exists():
                assert process.poll() is None and time.monotonic() < deadline
                time.sleep(0.01)
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(2)
                client.connect(str(sock))
                sequence = 0

                def tx(op, offset=0, data=0, magic=0x31505355):
                    nonlocal sequence
                    sequence += 1
                    client.sendall(struct.pack("<5I", magic, sequence, op, offset, data))
                    response = b""
                    while len(response) < 20:
                        part = client.recv(20 - len(response))
                        assert part
                        response += part
                    header, seq, status, value, irq = struct.unpack("<5I", response)
                    assert header == 0x31505355 and seq == sequence
                    return status, value, irq

                yield tx, trace
            process.wait(timeout=5)
            assert process.returncode == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


@pytest.mark.integration
def test_chipyard_gcd_rtl_vectors_irq_busy_errors_and_reset(model_bus):
    tx, trace = model_bus
    assert tx(2) == (0, 0, 0)
    assert tx(0, 0) == (0, 2, 0)
    assert tx(0, 12)[0] == 1
    assert tx(1, 8, 12)[0] == 1  # zero x cannot enter upstream subtract-zero loop
    for x, y, result in ((48, 18, 6), (270, 192, 6), (65535, 255, 255), (1, 1, 1)):
        assert tx(1, 4, x)[0] == 0
        assert tx(1, 8, y)[0] == 0
        if x > 1:
            assert tx(1, 8, y)[0] == 1  # start while busy
        for _ in range(1024):
            if tx(0, 0)[2] & 1:
                break
        else:
            pytest.fail("actual GCD never completed")
        assert tx(0, 12)[1:] == (result, 0)
        assert tx(0, 0) == (0, 2, 0)
    tx(1, 4, 65535)
    tx(1, 8, 1)
    assert tx(2) == (0, 0, 0)
    assert tx(0, 0) == (0, 2, 0)


@pytest.mark.integration
def test_opentitan_real_tlul_gpio_w1c_integrity_backpressure_reset(model_bus):
    tx, trace = model_bus
    tx(2)
    assert tx(0, 0x1010) == (0, 0, 0)
    tx(1, 0x1020, 1)
    tx(1, 0x102C, 1)
    tx(1, 0x1004, 1)
    assert tx(1, 0x1014, 1) == (0, 1, 2)
    assert tx(0, 0x1010) == (0, 1, 2)
    assert tx(0, 0x1000) == (0, 1, 2)
    assert tx(1, 0x1000, 1) == (0, 1, 0)
    assert tx(0, 0x1000) == (0, 0, 0)
    tx(1, 0x1018, 0x00010000)
    assert tx(0, 0x1010) == (0, 0, 0)
    assert tx(0, 0x1011)[0] == 1
    assert tx(0, 0x1040)[0] == 1  # no six-bit address aliasing
    assert tx(3, 0x1010)[0] == 1  # actual command ECC corruption
    assert json.loads(trace.read_text().splitlines()[-1])["alert"] == 1
    assert tx(2) == (0, 0, 0)
    assert json.loads(trace.read_text().splitlines()[-1])["alert"] == 0
    assert tx(0, 0x1010) == (0, 0, 0)


@pytest.mark.integration
def test_bridge_rejects_invalid_protocol_without_reset_or_write(model_bus):
    tx, _ = model_bus
    tx(2)
    tx(1, 0x1014, 7)
    for op, offset, magic in (
        (4, 0, 0x31505355),
        (0, 0x2000, 0x31505355),
        (1, 0x1014, 0),
        (3, 4, 0x31505355),
    ):
        assert tx(op, offset, 0, magic=magic)[0] == 1
    assert tx(0, 0x1014)[1] == 7


@pytest.mark.integration
def test_source_ip_guest_irq_and_replay(native_models, tmp_path):
    value = os.environ.get("USP_IP_GENERATED_DIR")
    if not value:
        if os.environ.get("USP_REQUIRE_IPS") == "1":
            pytest.fail("IP machine/Zephyr build required")
        pytest.skip("source IP guest profile is not built")
    project = load_project(ROOT / "examples/rv64-ips")
    for name in ("first", "replay"):
        report = run_ipblocks(project, Path(value).resolve(), native_models, tmp_path / name)
        assert report["status"] == "PASSED"
        assert all(report["assertions"].values())
        assert report["source_ip_models"]["chipyard_commit"] == CHIPYARD_SHA
        assert (tmp_path / name / "licenses/Chipyard-LICENSE").is_file()
        assert (tmp_path / name / "licenses/OpenTitan-LICENSE").is_file()
    assert (tmp_path / "first/bridge-trace.jsonl").read_bytes() == (
        tmp_path / "replay/bridge-trace.jsonl"
    ).read_bytes()


@pytest.mark.integration
@pytest.mark.parametrize("changed", ["rtl/Vusp_ipblocks", "generator/rtl/GCDMMIOChiselModule.sv"])
def test_modified_model_artifacts_rejected(native_models, tmp_path, changed):
    for name in (
        "ipblocks-build.json",
        "rtl/Vusp_ipblocks",
        "generator/rtl/GCDMMIOChiselModule.sv",
    ):
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(native_models / name, dest)
    with (tmp_path / changed).open("ab") as stream:
        stream.write(b"modified")
    with pytest.raises(ContractError, match="stale/modified"):
        verify_models(tmp_path)


@pytest.mark.integration
def test_real_source_ip_timeout_is_failed_and_owned_models_stop(native_models, tmp_path):
    value = os.environ.get("USP_IP_GENERATED_DIR")
    if not value:
        if os.environ.get("USP_REQUIRE_IPS") == "1":
            pytest.fail("IP machine/Zephyr build required")
        pytest.skip("source IP guest profile is not built")
    report = run_ipblocks(
        load_project(ROOT / "examples/rv64-ips"),
        Path(value).resolve(),
        native_models,
        tmp_path / "timeout",
        timeout=0.000001,
    )
    assert report["status"] == "FAILED" and report["timed_out"]
    assert report["returncode"] < 0
    assert not Path(f"/proc/{report['model_process']['pid']}").exists()
    assert (tmp_path / "timeout/report.json").is_file()
