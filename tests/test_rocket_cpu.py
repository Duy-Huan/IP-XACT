"""Rocket ownership/protocol contracts and real source-built acceptance gates."""

import copy
import json
import os
import struct
from pathlib import Path

import pytest

from socsim.cpu_platform import cpu_selection, platform_plan
from socsim.domain import ContractError
from socsim.project import load_project
from socsim.rocket_build import CPU_CONTRACT
from socsim.rocket_cpu import acceptance, axi_checks, layout, run, validate_elf, verify_run
from socsim.rocket_software import MMU_MARKERS, verify_software
from socsim.systems import load_system

ROOT = Path(__file__).resolve().parents[1]


def project(name):
    if name == "rv64-ips":
        return load_project(ROOT / "examples/rv64-ips")
    return load_system(ROOT / "systems" / name)[2]


def config(engine="qemu"):
    return {
        "schema_version": "1",
        "engine": engine,
        "cpu": {"provider": "chipyard", "model": CPU_CONTRACT["model"]},
        "workloads": ["baremetal"],
    }


@pytest.mark.parametrize("engine", ["qemu", "gem5"])
def test_rocket_plan_preserves_cpu_cache_memory_and_irq_owners(engine):
    p = project("rv64-ips")
    plan = platform_plan(p, config(engine))
    execution = plan["execution_plan"]
    assert plan["ownership"]["native_engine_cpu"] == "halted-irq-sink"
    assert plan["ownership"]["l1_caches"] == "RocketTile"
    assert plan["ownership"]["backing_memory"] == engine
    assert execution["cpu_model"] == CPU_CONTRACT["model"]
    assert execution["dma_supported"] is False
    assert execution["coherence_supported"] is False
    assert execution["cpu_rtl_cycle_correlation"] is False
    cpu = next(b for b in execution["bindings"] if b["instance"] == "cpu")
    assert cpu["implementation"] == "RocketTile+TLBroadcast+TLToAXI4"
    assert any(i["target"] == "cpu" and i["line"] == 9 for i in execution["interrupt_routes"])
    ram, regions = layout(p)
    assert ram["base"] == 0x80000000
    assert len(regions) == 7


@pytest.mark.parametrize("case", ["linux", "boom", "manifest", "cost", "clock", "missing-kernel"])
def test_rocket_cannot_infer_other_cpu_kernel_or_timing_profiles(case):
    c = config()
    if case == "linux":
        c["workloads"] = ["linux"]
    elif case == "boom":
        c["cpu"]["model"] = "BOOM"
    elif case == "manifest":
        c["cpu"]["rtl_manifest"] = "/nonexistent"
    elif case == "cost":
        c["qemu_cost_model"] = {"base_cpi": 1, "memory_extra_cycles": 1}
    elif case == "clock":
        c["cpu"]["clock_hz"] = 300000000
    else:
        c["workloads"] = ["freertos"]
    with pytest.raises(ContractError):
        cpu_selection(c, "qemu")


def elf_image(flags=1, entry=0x80000000, address=0x80000000):
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    header = struct.pack("<HHIQQQIHHHHHH", 2, 243, 1, entry, 64, 0, flags, 64, 56, 1, 0, 0, 0)
    segment = struct.pack("<IIQQQQQQ", 1, 5, 120, address, address, 4, 4, 4)
    return ident + header + segment + b"\x13\x00\x00\x00"


@pytest.mark.parametrize("case", ["valid", "float-abi", "outside-ram", "wrong-entry", "truncated"])
def test_rocket_elf_boot_contract_rejects_wrong_abi_entry_and_loads(tmp_path, case):
    payload = elf_image(
        flags=5 if case == "float-abi" else 1,
        entry=0x80001000 if case == "wrong-entry" else 0x80000000,
        address=0x1000 if case == "outside-ram" else 0x80000000,
    )
    if case == "truncated":
        payload = payload[:-3]
    elf = tmp_path / "firmware.elf"
    elf.write_bytes(payload)
    ram = {"base": 0x80000000, "size": 128 * 1024**2}
    if case == "valid":
        assert validate_elf(elf, ram) == 0x80000000
    else:
        with pytest.raises(ContractError):
            validate_elf(elf, ram)


def axi_evidence():
    channels = ["AR", "R", "R", "AW", "W", "B"]
    rows = [
        {
            "channel": channel,
            "cycle": i,
            "address": 0x80000000,
            "id": 2,
            "data": 2 if channel == "AR" else 1,
            "response": 0,
        }
        for i, channel in enumerate(channels)
    ]
    counters = {
        "cycles": 10,
        "max_burst_beats": 2,
        "axi_ar": 1,
        "axi_aw": 1,
        "axi_r_beats": 2,
        "axi_w_beats": 1,
        "axi_b": 1,
    }
    return rows, counters


@pytest.mark.parametrize(
    "case", ["valid", "wrong-id", "missing-b", "extra-r", "error", "truncated"]
)
def test_axi_verification_detects_incomplete_and_error_responses(tmp_path, case):
    rows, counters = axi_evidence()
    if case == "wrong-id":
        rows[1]["id"] = 3
    elif case == "missing-b":
        rows.pop()
    elif case == "extra-r":
        rows.insert(3, copy.deepcopy(rows[2]))
    elif case == "error":
        rows[1]["response"] = 3
    path = tmp_path / "axi.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    if case == "truncated":
        with path.open("a") as f:
            f.write('{"channel":')
    assert axi_checks(path, counters) is (case == "valid")


@pytest.mark.parametrize("raw", ["{", "[]", '{"cycles":true}'])
def test_crashed_counters_cannot_become_a_success_report(tmp_path, raw):
    (tmp_path / "rtl-counters.json").write_text(raw)
    checks, _ = acceptance(tmp_path, "qemu", ["PASS"], 0, 0, False)
    assert not all(checks.values())
    assert checks["valid_counters"] is False
    assert checks["actual_retirement"] is False


def native_roots():
    names = ("USP_ROCKET_ROOT", "USP_ROCKET_SOFTWARE_ROOT", "USP_RTL_MASTER_BUILD")
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        if os.environ.get("USP_REQUIRE_ROCKET") == "1":
            pytest.fail("mandatory actual Rocket inputs missing: " + ", ".join(missing))
        pytest.skip("actual source-built Rocket integration inputs not supplied")
    return tuple(Path(os.environ[n]).resolve() for n in names)


@pytest.mark.parametrize("name", ["rv64-ips", "AI_SOC", "AIZ_SOC"])
@pytest.mark.parametrize("engine", ["qemu", "gem5"])
@pytest.mark.parametrize("kind", ["baremetal", "mmu"])
def test_actual_rocket_axi_backpressure_and_privilege_paths(tmp_path, name, engine, kind):
    p, cpu, software, generated, models, bridge = native_inputs(name)
    kw = {"axi_latency": 3, "compressed_trace": kind == "mmu" or engine == "qemu"}
    if kind == "mmu":
        sw = software / "mmu"
        r = verify_software(p, cpu, sw)
        assert r["expected_markers"] == MMU_MARKERS
        kw.update(elf=sw / "firmware.elf", markers=r["expected_markers"])
    report = run(
        p,
        cpu,
        generated,
        models,
        engine,
        tmp_path / "run",
        bridge,
        **kw,
    )
    assert report["status"] == "PASSED", report
    assert report["counters"]["max_burst_beats"] == 8
    assert report["counters"]["axi_request_stalls"] > 0
    assert verify_run(p, cpu, tmp_path / "run")["status"] == "PASSED"


def native_inputs(name):
    cpu_root, software_root, bridge = native_roots()
    generated = Path(os.environ.get("USP_IP_GENERATED_DIR", ROOT / "build/rv64-ips"))
    if name != "rv64-ips":
        generated = Path(os.environ.get("USP_DUAL_BUILD_ROOT", ROOT / "build")) / name
    models = Path(os.environ.get("USP_IP_MODELS_DIR", ROOT / "build/source-ip-models"))
    return project(name), cpu_root / name, software_root / name, generated, models, bridge


@pytest.mark.parametrize("name", ["rv64-ips", "AI_SOC", "AIZ_SOC"])
@pytest.mark.parametrize("engine", ["qemu", "gem5"])
@pytest.mark.parametrize("case", ["smoke", "assert-failure", "tick-disabled"])
def test_actual_rocket_scheduler_and_kernel_failure_cases(tmp_path, name, engine, case):
    from socsim.cpu_platform import run_platform, verify_platform

    p, cpu, software, generated, models, bridge = native_inputs(name)
    sw = software / ("freertos-" + case)
    r = verify_software(p, cpu, sw)
    out = tmp_path / "run"
    if case == "smoke":
        c = config(engine)
        c["workloads"] = ["baremetal", "freertos"]
        c["software_builds"] = {"freertos": str(sw)}
        report = run_platform(p, c, generated, models, bridge, None, out, cpu)
        assert report["status"] == "PASSED", report
        assert verify_platform(out)["status"] == "PASSED"
    else:
        report = run(
            p,
            cpu,
            generated,
            models,
            engine,
            out,
            bridge,
            elf=sw / "firmware.elf",
            markers=r["expected_markers"],
            max_cycles=200000 if case == "tick-disabled" else 3000000,
        )
        assert report["status"] == "FAILED", report
        counters = report["counters"]
        uart = (out / "uart.log").read_text()
        assert counters["instructions"] > 1000
        assert counters["axi_errors"] == 0
        if case == "assert-failure":
            assert "USP_FREERTOS_FAIL" in uart
            assert counters["signature"] == 0x13333
        else:
            assert "USP_FREERTOS_BOOT_OK" in uart
            assert "USP_TEST_PASS" not in uart
            assert counters["cycles"] == 200000
            assert counters["finished"] == 0
        with pytest.raises(ContractError):
            verify_run(p, cpu, out)


@pytest.mark.parametrize("engine", ["qemu", "gem5"])
@pytest.mark.parametrize("failure", ["downstream-axi", "cycle-budget"])
def test_actual_rocket_bus_errors_and_execution_budget_cannot_pass(tmp_path, engine, failure):
    p, cpu, _, generated, models, bridge = native_inputs("rv64-ips")
    kw = {"max_cycles": 50000}
    if failure == "downstream-axi":
        kw["fault_address"] = next(i.windows[0].base for i in p.ir.instances if i.name == "cygcd")
    else:
        kw["max_cycles"] = 16
    report = run(
        p, cpu, generated, models, engine, tmp_path / "run", bridge, compressed_trace=True, **kw
    )
    assert report["status"] == "FAILED", report
    if failure == "downstream-axi":
        assert report["counters"]["axi_errors"] > 0
        assert report["checks"]["no_downstream_bus_error"] is False
    else:
        assert report["counters"]["cycles"] == 16
        assert report["counters"]["instructions"] == 0


@pytest.mark.parametrize("case", ["valid", "backwards", "negative-pc", "bad-trap", "truncated"])
def test_streamed_retirement_validation_rejects_partial_or_invalid_evidence(tmp_path, case):
    from socsim.rocket_cpu import commit_summary

    rows = [{"cycle": i, "pc": 0x80000000, "instruction": 0x23, "trap": 0} for i in range(5)]
    if case == "backwards":
        rows[-1]["cycle"] = 1
    elif case == "negative-pc":
        rows[-1]["pc"] = -1
    elif case == "bad-trap":
        rows[-1]["trap"] = 7
    path = tmp_path / "commit.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    if case == "truncated":
        with path.open("a") as stream:
            stream.write('{"cycle":')
    summary = commit_summary(path)
    if case == "valid":
        assert summary == (5, 0, 4, {0}, False)
    else:
        assert summary is None


@pytest.mark.parametrize("name", ["rv64-ips", "AI_SOC", "AIZ_SOC"])
@pytest.mark.parametrize("engine", ["qemu", "gem5"])
@pytest.mark.parametrize("case", ["smoke", "tick-disabled"])
def test_actual_rocket_source_built_zephyr_bsp(tmp_path, name, engine, case):
    from socsim.rocket_zephyr import verify_build

    p, cpu, _, generated, models, bridge = native_inputs(name)
    root = os.environ.get("USP_ROCKET_ZEPHYR_ROOT")
    if not root:
        if os.environ.get("USP_REQUIRE_ROCKET") == "1":
            pytest.fail("mandatory source-built Rocket Zephyr BSP roots missing")
        pytest.skip("source-built Rocket Zephyr BSP roots not supplied")
    sw = Path(root) / name / case
    record = verify_build(p, cpu, sw)
    report = run(
        p,
        cpu,
        generated,
        models,
        engine,
        tmp_path / "run",
        bridge,
        elf=sw / "firmware.elf",
        markers=record["expected_markers"],
        max_cycles=1500000 if case == "tick-disabled" else 3000000,
        axi_latency=3,
    )
    if case == "smoke":
        assert report["status"] == "PASSED", report
        assert report["checks"]["gcd_irq"] and report["checks"]["gpio_irq"]
        assert verify_run(p, cpu, tmp_path / "run")["status"] == "PASSED"
    else:
        assert report["status"] == "FAILED", report
        assert report["counters"]["finished"] == 0
        assert report["counters"]["cycles"] == 1500000
        assert "USP_TEST_PASS" not in (tmp_path / "run/uart.log").read_text()


@pytest.mark.parametrize(
    "raw,canonical",
    [(0x80000000, 0x80000000), (0x50000000, 0x50000000), (0xFF80089538, 0xFFFFFFFF80089538)],
)
def test_sv39_trace_pc_encoding_is_explicit_without_rewriting_raw_events(raw, canonical):
    from socsim.rocket_cpu import canonical_pc

    assert canonical_pc(raw) == canonical
    with pytest.raises(ContractError):
        canonical_pc(1 << 40)


@pytest.mark.parametrize("name", ["rv64-ips", "AI_SOC", "AIZ_SOC"])
def test_actual_qemu_ram_snapshot_keeps_scalar_retirement_and_axi_responses(tmp_path, name):
    p, cpu, _, generated, models, bridge = native_inputs(name)
    reports = []
    captures = []
    for bulk in (False, True):
        out = tmp_path / ("bulk" if bulk else "scalar")
        report = run(
            p,
            cpu,
            generated,
            models,
            "qemu",
            out,
            bridge,
            axi_latency=3,
            compressed_trace=True,
            qemu_ram_burst=bulk,
        )
        assert report["status"] == "PASSED", report
        assert "USP_ROCKET_FENCEI_OK" in (out / "uart.log").read_text()
        assert verify_run(p, cpu, out)["status"] == "PASSED"
        counters = report["counters"]
        assert (counters["ram_bulk_reads"] > 0) is bulk
        reports.append({k: v for k, v in counters.items() if k != "ram_bulk_reads"})
        captures.append(json.loads((out / "capture.json").read_text())["streams"])
    assert reports[0] == reports[1]
    for stream in ("rtl-counters.json.commit.jsonl", "rtl-counters.json.axi.jsonl"):
        assert captures[0][stream]["raw_sha256"] == captures[1][stream]["raw_sha256"]


@pytest.mark.parametrize("fault", ["ram", "mmio"])
def test_actual_bulk_mode_does_not_bypass_diagnostic_bus_faults(tmp_path, fault):
    p, cpu, _, generated, models, bridge = native_inputs("rv64-ips")
    address = (
        0x80000000
        if fault == "ram"
        else next(i.windows[0].base for i in p.ir.instances if i.name == "cygcd")
    )
    report = run(
        p,
        cpu,
        generated,
        models,
        "qemu",
        tmp_path / "run",
        bridge,
        max_cycles=50000,
        compressed_trace=True,
        qemu_ram_burst=True,
        fault_address=address,
    )
    assert report["status"] == "FAILED", report
    assert report["counters"]["axi_errors"] > 0
    assert not report["checks"]["no_downstream_bus_error"]
