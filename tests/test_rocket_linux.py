"""Source-bound OpenSBI/Sv39/Linux boot on the actual Rocket CPU."""

import json
import os
import shutil
from pathlib import Path

import pytest

from socsim.domain import ContractError
from socsim.rocket_cpu import CPU_CONTRACT, selection


@pytest.mark.parametrize("engine", ["qemu", "gem5"])
def test_linux_requires_explicit_source_built_boot_contract(engine):
    c = {
        "engine": engine,
        "cpu": {"provider": "chipyard", "model": CPU_CONTRACT["model"]},
        "workloads": ["linux", "boot-sequence"],
        "software_builds": {"linux": "/real-build", "boot-sequence": "/real-build"},
    }
    assert selection(c, engine)["performance"] == "native-rtl-counters"
    del c["software_builds"]["boot-sequence"]
    with pytest.raises(ContractError, match="source-built"):
        selection(c, engine)


def native_inputs(name):
    from test_rocket_cpu import native_inputs as inputs

    root = os.environ.get("USP_ROCKET_LINUX_ROOT")
    if not root:
        if os.environ.get("USP_REQUIRE_ROCKET_LINUX") == "1":
            pytest.fail("mandatory source-built Rocket Linux roots missing")
        pytest.skip("actual Rocket Linux boot inputs not supplied")
    return (*inputs(name), Path(root).resolve() / name)


@pytest.mark.parametrize("name", ["rv64-ips", "AI_SOC", "AIZ_SOC"])
@pytest.mark.parametrize("engine", ["qemu", "gem5"])
def test_actual_rocket_opensbi_linux_userspace(tmp_path, name, engine):
    from socsim.cpu_platform import run_platform, verify_platform
    from socsim.rocket_linux import verify_build

    p, cpu, _, generated, models, bridge, sw = native_inputs(name)
    record = verify_build(p, cpu, sw)
    c = {
        "schema_version": "1",
        "engine": engine,
        "cpu": {"provider": "chipyard", "model": CPU_CONTRACT["model"]},
        "workloads": ["linux"],
        "software_builds": {"linux": str(sw)},
    }
    out = tmp_path / "run"
    report = run_platform(p, c, generated, models, bridge, None, out, rtl_cpu_build=cpu)
    assert report["status"] == "PASSED", report
    assert verify_platform(out)["status"] == "PASSED"
    log = (out / "linux/uart.log").read_text()
    stages = [
        "OpenSBI v1.6",
        "Linux version 6.12",
        "Run /init as init process",
        *record["expected_markers"],
        "reboot: Power down",
    ]
    assert all(s in log for s in stages)
    assert [log.index(s) for s in stages] == sorted(log.index(s) for s in stages)
    row = json.loads((out / "linux/report.json").read_text())
    assert row["counters"]["instructions"] > 1000000
    assert row["counters"]["traps"] > 100
    assert row["performance"]["source"] == "actual Rocket clock edges and commit trace"
    if engine == "gem5":
        assert row["checks"]["no_native_iss"] is True


@pytest.mark.parametrize("name", ["init", "linux.config", "soc.dtb", "boot.S"])
def test_source_bound_linux_rejects_modified_boot_dependencies(tmp_path, name):
    from socsim.rocket_linux import verify_build

    p, cpu, _, _, _, _, sw = native_inputs("rv64-ips")
    record = verify_build(p, cpu, sw)
    out = tmp_path / "tampered"
    out.mkdir()
    for file in [*record["files"], "software-build.json"]:
        shutil.copy2(sw / file, out / file)
    with (out / name).open("ab") as stream:
        stream.write(b"modified")
    with pytest.raises(ContractError, match="modified"):
        verify_build(p, cpu, out)
