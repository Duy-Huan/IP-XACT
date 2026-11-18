"""Source-package and native HW/SW lifecycle qualification, including actual engines."""

import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from socsim.domain import ContractError
from socsim.native_flow import build_flow, execute_flow, plan, verify_build, verify_flow
from socsim.native_package import build_package, read_package, verify_package
from socsim.native_performance import metrics
from socsim.native_software import build_software, check_run, contract, freertos_bsp, zephyr_bsp
from socsim.soc_native import generate, run, verify

ROOT = Path(__file__).resolve().parents[1]
FLOWS = ROOT / "specs/flows"


def clone_inputs(tmp_path):
    out = tmp_path / "input"
    shutil.copytree(FLOWS, out)
    return out


def test_package_contract_has_pinned_behavior_driver_and_real_error_vectors():
    package, inputs = read_package(FLOWS / "packages/counter/package.yaml")
    assert len(inputs) == 4
    assert package["registers"][1]["access"] == "write-one-clear"
    assert any(v["status"] == 1 for v in package["conformance"])
    assert package["interface"]["state_owner"] == "peripheral-process"


@pytest.mark.parametrize(
    "case",
    ["source", "driver", "license", "bus", "offset", "register-name", "missing-error", "escape"],
)
def test_package_rejects_corruption_ambiguity_or_unimplemented_contract(tmp_path, case):
    root = clone_inputs(tmp_path)
    path = root / "packages/counter/package.yaml"
    data = yaml.safe_load(path.read_text())
    if case in {"source", "driver", "license"}:
        filename = {"source": "tlm-counter.cpp", "driver": "selftest.c", "license": "LICENSE"}[case]
        (path.parent / filename).write_text("changed\n")
    elif case == "bus":
        data["interface"]["protocol"] = "axi4"
    elif case == "offset":
        data["registers"][1]["offset"] = 0
    elif case == "register-name":
        data["registers"][1]["name"] = "value"
    elif case == "missing-error":
        data["conformance"] = [v for v in data["conformance"] if v["status"] == 0]
    else:
        data["driver"]["source"] = "../../../../outside.c"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises((ContractError, FileNotFoundError)):
        read_package(path)


def test_flow_plan_is_pending_build_with_explicit_ownership_and_all_source_pins():
    p = plan(FLOWS / "AI_SOC-package.yaml")
    assert p["status"] == "PLANNED" and p["hart_count"] == 1
    assert p["packages"] == {"user.counter": "packages/counter/package.yaml"}
    assert p["inputs"]["AI_SOC-package.yaml"]
    assert p["inputs"]["packages/counter/selftest.c"]
    assert p["deferred_until_build"] and not p["full_master_objective_complete"]


@pytest.mark.parametrize(
    "case",
    [
        "cpu",
        "cpu-type",
        "harts",
        "irq",
        "engine",
        "duplicate",
        "timeout",
        "package",
        "cost",
        "escape",
    ],
)
def test_flow_preflight_rejects_invalid_or_unavailable_lowering(tmp_path, case):
    root = clone_inputs(tmp_path)
    path = root / "AIZ_SOC.yaml"
    data = yaml.safe_load(path.read_text())
    spec_path = root / data["spec"]
    spec = yaml.safe_load(spec_path.read_text())
    if case == "cpu":
        spec["cpu"]["model"] = "chipyard.rocket"
    elif case == "cpu-type":
        spec["cpu"] = None
    elif case == "harts":
        spec["cpu"]["harts"] = 2
    elif case == "irq":
        spec["devices"][5]["irq"] = 0
    elif case == "engine":
        data["engine"] = "verilator"
    elif case == "duplicate":
        data["software"].append(data["software"][0])
    elif case == "timeout":
        data["software"][0]["timeout"] = True
    elif case == "package":
        data["software"][0].update(kind="package-driver", package="unknown")
    elif case == "cost":
        data["performance"] = {"qemu_cost_model": {"base_cpi": -1, "memory_extra_cycles": 1}}
    else:
        data["spec"] = "../../private.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))
    with pytest.raises(ContractError):
        plan(path)


def test_build_resource_preflight_happens_before_output_or_processes(tmp_path):
    with pytest.raises(ContractError, match="source-qemu"):
        build_flow(FLOWS / "AI_SOC-package.yaml", tmp_path / "built", {})
    assert not (tmp_path / "built").exists()


def test_native_bsp_uses_ir_boot_entry_timer_console_and_real_register_names(tmp_path):
    generated = tmp_path / "generated"
    generate(ROOT / "specs/AIZ_SOC-native.yaml", generated, "both")
    spec, _ = contract(generated)
    out = tmp_path / "software"
    out.mkdir()
    bsp = freertos_bsp(generated, out, spec)
    assert "0x81001000" in (bsp / "memory.ld").read_text()
    assert "USP_UART_BASE USP_CONSOLE_BASE" in (bsp / "soc.h").read_text()
    assert "0x210bff8" in (bsp / "FreeRTOSConfig.h").read_text()
    z = tmp_path / "zephyr"
    z.mkdir()
    zephyr_bsp(z, spec)
    dts = (z / "zephyr/boards/usp/usp_native/usp_native.dts").read_text()
    assert "<0x0 0x81001000" in dts and "interrupts = <3 1>" in dts


def test_native_rtos_bsp_rejects_multi_hart_before_output(tmp_path):
    generated = tmp_path / "generated"
    generate(ROOT / "specs/AI_SOC-smp.yaml", generated, "both")
    with pytest.raises(ContractError, match="exactly one"):
        build_software(generated, tmp_path / "software", "freertos", ROOT, "unused-")
    assert not (tmp_path / "software").exists()


@pytest.mark.parametrize("case", ["hart", "sum", "missing", "empty", "bool"])
def test_qemu_per_hart_event_evidence_cannot_be_fabricated_or_mixed(tmp_path, case):
    raw = {
        "executed_instructions": 100,
        "data_accesses": 20,
        "per_hart": [{"hart_id": 0, "executed_instructions": 100, "data_accesses": 20}],
    }
    if case == "hart":
        raw["per_hart"][0]["hart_id"] = 1
    elif case == "sum":
        raw["executed_instructions"] = 101
    elif case == "missing":
        raw.pop("per_hart")
    elif case == "empty":
        raw["per_hart"] = []
    else:
        raw["data_accesses"] = True
    p = tmp_path / "counters.json"
    p.write_text(json.dumps(raw))
    with pytest.raises(ContractError):
        metrics(p, 1, {"base_cpi": 1, "memory_extra_cycles": 1})


def test_qemu_estimated_cpi_is_labelled_and_not_native_cycles(tmp_path):
    p = tmp_path / "counters.json"
    p.write_text(
        json.dumps(
            {
                "executed_instructions": 100,
                "data_accesses": 20,
                "per_hart": [{"hart_id": 0, "executed_instructions": 100, "data_accesses": 20}],
            }
        )
    )
    r = metrics(p, 1, {"base_cpi": 1, "memory_extra_cycles": 1})
    assert r["estimated_cpi"] == 1.2 and r["native_cpu_cycles"] is None
    assert r["per_hart"][0]["counters"]["executed_instructions"] == 100
    assert r["roi"] == "whole-guest-including-boot-and-idle"


@pytest.mark.parametrize("kind", ["freertos", "zephyr", "ucos2", "ucos3"])
def test_boot_string_without_real_timer_scheduler_or_irq_cannot_pass(kind):
    assert not all(check_run(kind, "USP_TEST_PASS\n").values())


def actual_flow(name):
    if os.environ.get("USP_REQUIRE_NATIVE_FLOW") != "1":
        pytest.skip("requires the source-built native lifecycle batch")
    return Path(os.environ["USP_NATIVE_FLOW_" + name])


@pytest.mark.parametrize("name", ["AI_SOC", "AIZ_SOC", "GEM5_ONLY", "SMP_PROFILE"])
def test_actual_native_flow_runs_the_selected_software_and_engine_without_board_branches(
    tmp_path, name
):
    built = actual_flow(name)
    r, p = verify_build(built)
    out = tmp_path / "run"
    report = execute_flow(built, out)
    assert report["status"] == verify_flow(built, out)["status"] == "PASSED"
    if name == "AI_SOC":
        assert len(r["software"]) == 6 and len(r["packages"]) == 1
        for software in r["software"]:
            q = json.loads((out / software / "qemu/report.json").read_text())
            g = json.loads((out / software / "gem5/report.json").read_text())
            assert q["native_ir_sha256"] == g["native_ir_sha256"] == r["native_ir_sha256"]
            assert q["firmware_sha256"] == g["firmware_sha256"]
            assert q["performance"]["counters"]["executed_instructions"] > 0
            assert q["performance"]["native_cpi"] is None
            assert g["performance"]["native_cpi"] > 0
        # Altering only the claimed result must fail even when raw files are untouched.
        original = (out / "report.json").read_bytes()
        claimed = json.loads(original)
        claimed["checks"] = {"pretend": True}
        (out / "report.json").write_text(json.dumps(claimed))
        with pytest.raises(ContractError, match="checks differ"):
            verify_flow(built, out)
        (out / "report.json").write_bytes(original)
        assert verify_flow(built, out)["status"] == "PASSED"
    if name == "SMP_PROFILE":
        q = json.loads((out / "baremetal/qemu/report.json").read_text())["performance"]
        g = json.loads((out / "baremetal/gem5/report.json").read_text())["performance"]
        assert len(q["per_hart"]) == len(g["per_hart"]) == p["hart_count"] == 4
        assert all(h["counters"]["executed_instructions"] > 0 for h in q["per_hart"])
        assert all(h["instructions"] > 0 and h["cycles"] > 0 for h in g["per_hart"])
        assert q["native_cpi"] is None and g["native_cpi"] > 0
    if name == "GEM5_ONLY":
        assert p["engine"] == "gem5" and not (built / "generated/qemu-build").exists()
        assert not (built / "generated/qemu-source").exists()
        assert not (out / "baremetal/qemu").exists()


def test_actual_package_source_model_conformance_and_modified_binary_rejection(tmp_path):
    actual_flow("AI_SOC")
    out = tmp_path / "package"
    build_package(FLOWS / "packages/counter/package.yaml", out)
    record = verify_package(out)
    q = json.loads((out / "qualification.json").read_text())
    assert q["status"] == "PASSED" and any(t["status"] == 1 for t in q["actual"])
    assert record["driver"]["expected_markers"]
    with (out / "model").open("ab") as stream:
        stream.write(b"modified")
    with pytest.raises(ContractError, match="modified/stale"):
        verify_package(out)


def test_actual_false_package_reset_contract_fails_against_real_model(tmp_path):
    actual_flow("AI_SOC")
    root = clone_inputs(tmp_path)
    p = root / "packages/counter/package.yaml"
    data = yaml.safe_load(p.read_text())
    data["conformance"][1]["value"] = 7
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ContractError, match="differs from declared"):
        build_package(p, tmp_path / "package")
    actual = json.loads((tmp_path / "package/qualification.json").read_text())
    assert actual["status"] == "FAILED" and actual["actual"][1]["value"] == 0


@pytest.mark.parametrize("engine", ["qemu", "gem5"])
@pytest.mark.parametrize("case", ["assert-failure", "tick-disabled"])
def test_actual_native_freertos_guest_failures_cannot_pass(tmp_path, engine, case):
    built = actual_flow("AIZ_SOC")
    sw = tmp_path / "software"
    generated = built / "generated"
    record = build_software(
        generated,
        sw,
        "freertos",
        Path(os.environ["USP_FREERTOS_SOURCE"]),
        os.environ.get("CROSS_COMPILE", "riscv64-unknown-elf-"),
        test_case=case,
    )
    build_record, _ = verify_build(built)
    bindings = build_record["engine_bindings"]
    out = tmp_path / "run"
    r = run(
        generated,
        Path(bindings["models"]),
        out,
        10,
        sw / record["elf"],
        engine,
        Path(bindings["gem5_build"]),
    )
    text = (out / "trace_port.uart.log").read_text()
    assert "USP_FREERTOS_BOOT_OK" in text
    assert r["status"] == verify(out)["status"] == "FAILED"


def test_native_ucos_rv64_frames_preserve_high_addresses_and_word_mmio():
    from socsim.native_ucos import port_assembly, startup

    asm = (
        ".equ BASE, 0x44000000\n    sw t1, 0x0(t0)\n    sw zero, 0x0(t0)\n"
        "    sw a2, 0(t1)\n    lw t0, 31 * 4(sp)\n"
    )
    widened = port_assembly(asm, 0x2100000)
    assert "sw t1, 0x0(t0)" in widened and "sw zero, 0x0(t0)" in widened
    assert "sd a2, 0(t1)" in widened and "ld t0, 31 * 8(sp)" in widened
    assert "0x44000000" not in widened
    boot = startup()
    assert "sp, sp, -256" in boot and "248(sp)" in boot
    assert "0x8000000000000003" in boot and "ld s1, 8(s0)" in boot


@pytest.mark.parametrize("engine", ["qemu", "gem5"])
@pytest.mark.parametrize("case", ["assert-failure", "tick-disabled"])
@pytest.mark.parametrize("kind", ["ucos2", "ucos3"])
def test_actual_native_ucos_failures_cannot_pass(tmp_path, engine, case, kind):
    built = actual_flow("AIZ_SOC")
    generated = built / "generated"
    sw = tmp_path / "software"
    record = build_software(
        generated,
        sw,
        kind,
        ROOT / ".tools/upstream",
        os.environ.get("CROSS_COMPILE", "riscv64-unknown-elf-"),
        test_case=case,
    )
    build_record, _ = verify_build(built)
    bindings = build_record["engine_bindings"]
    out = tmp_path / "run"
    result = run(
        generated,
        Path(bindings["models"]),
        out,
        10,
        sw / record["elf"],
        engine,
        Path(bindings["gem5_build"]),
    )
    text = (out / "trace_port.uart.log").read_text()
    assert "USP_UCOS_BOOT_OK" in text
    assert not all(check_run(kind, text).values())
    assert result["status"] == verify(out)["status"] == "FAILED"
