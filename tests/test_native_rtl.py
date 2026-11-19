"""Native source CPU binding, immutable IR and actual per-instance execution."""

import copy
import json
import os
from pathlib import Path

import pytest
import yaml

from socsim.domain import ContractError
from socsim.native_cpu_binding import ROCKET
from socsim.native_ir import read_ir
from socsim.soc_native import generate, verify_generated

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["AI_SOC", "AIZ_SOC"])
def test_native_source_cpu_identity_and_halted_memory_contexts(name, tmp_path):
    out = tmp_path / "generated"
    generate(ROOT / "specs/rtl" / (name + "-rocket.yaml"), out, "both")
    ir = read_ir(out / "native-ir.json")
    assert ir.schema_version == "6"
    assert ir.cpu_binding.instruction_owner == "actual-RocketTile-RTL"
    data = ir.to_dict()
    assert data["cpus"][0]["model"] == ROCKET
    assert "qemu_model" not in data["cpus"][0] and "gem5_model" not in data["cpus"][0]
    assert ir.cpus[0].contract.reset_vector == ir.boot.entry
    bindings = json.loads((out / "engine-bindings.json").read_text())
    for engine in ("qemu", "gem5"):
        cpu = bindings[engine][0]
        assert cpu["implementation"] == ROCKET
        assert "halted-irq-context" in cpu["memory_engine_context"]
    assert verify_generated(out)["cpu"]["model"] == ROCKET


@pytest.mark.parametrize("field", ["pins", "owner", "context", "model", "reset", "coherence"])
def test_native_source_cpu_ir_rejects_substitution_and_mixed_capabilities(tmp_path, field):
    out = tmp_path / "generated"
    generate(ROOT / "specs/rtl/AIZ_SOC-rocket.yaml", out, "both")
    p = out / "native-ir.json"
    data = json.loads(p.read_text())
    if field == "pins":
        data["cpu_binding"]["source_pins"][0][1] = "0" * 40
    elif field == "owner":
        data["cpu_binding"]["instruction_owner"] = "QEMU ISS"
    elif field == "context":
        data["cpu_binding"]["memory_contexts"][0][1] = "qemu-tcg"
    elif field == "model":
        data["cpus"][0]["model"] = "qemu.rv64"
    elif field == "reset":
        data["cpus"][0]["contract"]["reset_vector"] = 0x1000
    else:
        data["cpu_binding"]["external_dma_coherence"] = True
    p.write_text(json.dumps(data))
    with pytest.raises(ContractError, match="E_NATIVE_IR"):
        read_ir(p)


@pytest.mark.parametrize("field", ["harts", "iss", "address", "ram", "clock"])
def test_unsupported_native_rtl_capabilities_fail_before_generation(tmp_path, field):
    data = yaml.safe_load((ROOT / "specs/rtl/AIZ_SOC-rocket.yaml").read_text())
    if field == "harts":
        data["cpu"]["harts"] = 2
    elif field == "iss":
        data["cpu"]["gem5_model"] = "gem5.RiscvAtomicSimpleCPU"
    elif field == "address":
        data["devices"][-1]["base"] += 2**32
    elif field == "ram":
        d = copy.deepcopy(data["devices"][0])
        d.update(name="another_ram", base=0x90000000)
        data["devices"].append(d)
    else:
        data["cpu"]["clock_hz"] = 300000000
    source = tmp_path / "input.yaml"
    source.write_text(yaml.safe_dump(data))
    out = tmp_path / "generated"
    with pytest.raises(ContractError):
        generate(source, out, "both")
    assert not out.exists()


@pytest.mark.parametrize("name", ["AI_SOC", "AIZ_SOC"])
def test_actual_native_source_cpu_and_all_ip_instances_on_both_engines(tmp_path, name):
    from socsim.soc_native import accept, verify

    keys = (
        "USP_NATIVE_ROCKET_ROOT",
        "USP_NATIVE_ROCKET_CPU_ROOT",
        "USP_NATIVE_RTL_ENGINE",
        "USP_IP_MODELS_DIR",
    )
    if any(not os.environ.get(k) for k in keys):
        if os.environ.get("USP_REQUIRE_NATIVE_ROCKET") == "1":
            pytest.fail("mandatory real native Rocket builds missing")
        pytest.skip("actual native Rocket build roots not supplied")
    generated, cpus, engine, models = (Path(os.environ[k]).resolve() for k in keys)
    r = accept(
        generated / name, models, engine, tmp_path / "run", timeout=180, rtl_cpu_build=cpus / name
    )
    assert r["status"] == "PASSED", r
    assert r["checks"]["same_elf"] and r["checks"]["same_canonical_ir"]
    assert verify(tmp_path / "run")["status"] == "PASSED"
    row = json.loads((tmp_path / "run/gem5/report.json").read_text())
    assert row["checks"]["no_native_iss"] is True
    assert row["counters"]["instructions"] > 100


@pytest.mark.parametrize("case", ["linux", "clock", "cost", "missing-tools"])
def test_native_source_cpu_flow_rejects_unqualified_boot_timing_and_builds(tmp_path, case):
    import shutil

    from socsim.native_flow import build_flow, plan

    root = tmp_path / "input"
    shutil.copytree(ROOT / "specs/rtl", root)
    path = root / "AI_SOC-rocket-flow.yaml"
    data = yaml.safe_load(path.read_text())
    if case == "linux":
        data["software"][0]["kind"] = "linux"
    elif case == "clock":
        spec = root / data["spec"]
        c = yaml.safe_load(spec.read_text())
        c["cpu"]["clock_hz"] = 100000000
        spec.write_text(yaml.safe_dump(c))
    elif case == "cost":
        data["performance"] = {"qemu": {"base_cpi": 1, "memory_extra_cycles": 1}}
    path.write_text(yaml.safe_dump(data))
    out = tmp_path / "built"
    with pytest.raises(ContractError):
        if case == "missing-tools":
            build_flow(path, out, {})
        else:
            plan(path)
    assert not out.exists()


@pytest.mark.parametrize("name", ["AI_SOC", "AIZ_SOC"])
def test_actual_native_source_cpu_flow_runs_all_eligible_kernels(tmp_path, name):
    from socsim.native_flow import execute_flow, verify_build, verify_flow

    root = os.environ.get("USP_NATIVE_ROCKET_FLOW_ROOT")
    if not root:
        if os.environ.get("USP_REQUIRE_NATIVE_ROCKET") == "1":
            pytest.fail("mandatory native source-CPU software flow builds missing")
        pytest.skip("actual native source-CPU flow roots not supplied")
    built = Path(root).resolve() / name
    record, p = verify_build(built)
    assert p["engine"] == "both"
    assert record["software_order"] == ["baremetal", "freertos", "zephyr", "ucos2", "ucos3"]
    out = tmp_path / "run"
    assert execute_flow(built, out)["status"] == "PASSED"
    assert verify_flow(built, out)["status"] == "PASSED"
    for kind in record["software_order"]:
        for engine in ("qemu", "gem5"):
            row = json.loads((out / kind / engine / "report.json").read_text())
            assert row["counters"]["instructions"] > 100
            assert row["performance"]["source"] == "actual Rocket clock edges and commit trace"
            if engine == "gem5":
                assert row["checks"]["no_native_iss"] is True
