import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from socsim.cpu_platform import load_platform, platform_plan, run_platform, verify_platform
from socsim.domain import ContractError
from socsim.ipxact import digest
from socsim.project import load_project
from socsim.rtl_cpu import run_cpu, validate_elf, verify_run
from socsim.rtl_cpu_build import load_manifest, validate_ports, verify_cpu

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def firmware_ids():
    return {}


@pytest.mark.parametrize("engine", ["qemu", "gem5"])
@pytest.mark.parametrize("model", ["chipyard-ibex", "custom-ibex-wb"])
def test_rtl_plan_has_one_actual_cpu_and_engine_memory(engine, model):
    p = platform_plan(
        load_project(ROOT / "examples/rv64-ips"),
        load_platform(ROOT / f"platforms/{model}-{engine}.yaml"),
    )
    assert p["status"] == "AVAILABLE_ADAPTER"
    assert p["cpu"]["performance"] == "native-rtl-counters"
    assert p["ownership"]["instructions"] == "systemc-verilator-cpu"
    plan = p["execution_plan"]
    assert (
        next(b for b in plan["bindings"] if b["instance"] == "cpu")["domain"]
        == "systemc-verilator-cpu"
    )
    assert next(r for r in plan["address_routes"] if r["target"] == "ram")["owner"] == engine
    assert all(r["initiator"] == "cpu" for r in plan["address_routes"])
    assert all(r["line"] in {3, 7, 11} for r in plan["interrupt_routes"] if r["target"] == "cpu")
    assert not plan["dma_supported"] and not plan["coherence_supported"]


@pytest.mark.parametrize("case", ["kind", "model", "interface", "os", "cost"])
def test_rtl_configuration_rejects_unimplemented_contract(case, tmp_path):
    config = load_platform(ROOT / "platforms/chipyard-ibex-qemu.yaml")
    m = load_manifest(Path(config["cpu"]["rtl_manifest"]))
    if case == "kind":
        config["cpu"]["provider"] = "custom-rtl"
    if case == "model":
        config["cpu"]["model"] = "Rocket"
    if case == "interface":
        m["interface"] = "axi-magic"
    if case == "os":
        config["workloads"] = ["linux"]
    if case == "cost":
        config["qemu_cost_model"] = {"base_cpi": 1, "memory_extra_cycles": 0}
    manifest = tmp_path / "cpu.yaml"
    manifest.write_text(yaml.safe_dump(m))
    config["cpu"]["rtl_manifest"] = str(manifest)
    with pytest.raises(ContractError):
        platform_plan(load_project(ROOT / "examples/rv64-ips"), config)


@pytest.mark.parametrize("case", ["empty", "hash", "escape", "extra"])
def test_custom_cpu_source_integrity_is_required(tmp_path, case):
    src = tmp_path / "cpu.sv"
    src.write_text("module usp_rtl_cpu; endmodule\n")
    m = {
        "schema_version": "1",
        "kind": "custom-rtl",
        "interface": "obi-rvfi32-v1",
        "model": "test",
        "files": [{"path": "cpu.sv", "sha256": digest(src)}],
    }
    if case == "empty":
        m["files"] = []
    if case == "hash":
        src.write_text("changed")
    if case == "escape":
        m["files"][0]["path"] = "../outside.sv"
    if case == "extra":
        m["shell"] = "build"
    p = tmp_path / "manifest.yaml"
    p.write_text(yaml.safe_dump(m))
    with pytest.raises(ContractError):
        load_manifest(p)


def test_missing_rvfi_interface_is_rejected():
    with pytest.raises(ContractError, match="ports"):
        validate_ports("VL_IN8(&clk_i,0,0);\n")


def test_invalid_rtl_elf_container_is_rejected(tmp_path):
    p = tmp_path / "bad.elf"
    p.write_bytes(b"not an ELF")
    with pytest.raises(ContractError, match="ELF"):
        validate_elf(load_project(ROOT / "examples/rv64-ips"), p)


def native_paths():
    if os.environ.get("USP_REQUIRE_RTL_CPU") != "1":
        pytest.skip("actual RTL CPU co-simulation explicitly required")
    return {
        "ibex": Path(os.environ["USP_RTL_CPU_IBEX_BUILD"]),
        "custom": Path(os.environ["USP_RTL_CPU_CUSTOM_BUILD"]),
        "bridge": Path(os.environ["USP_RTL_MASTER_BUILD"]),
        "models": Path(os.environ["USP_IP_MODELS_DIR"]),
    }


@pytest.mark.parametrize("engine", ["qemu", "gem5"])
@pytest.mark.parametrize("model", ["chipyard-ibex", "custom-ibex-wb"])
@pytest.mark.parametrize("name", ["rv64-ips", "AI_SOC", "AIZ_SOC"])
def test_actual_rtl_cpu_runs_same_firmware_on_each_engine_and_map(
    tmp_path, engine, model, name, firmware_ids
):
    paths = native_paths()
    config = load_platform(ROOT / f"platforms/{model}-{engine}.yaml")
    project = load_project(
        ROOT / (f"examples/{name}" if name == "rv64-ips" else f"systems/{name}/bringup")
    )
    generated = (
        Path(os.environ["USP_IP_GENERATED_DIR"])
        if name == "rv64-ips"
        else Path(os.environ["USP_DUAL_BUILD_ROOT"]) / name
    )
    cpu = paths["ibex" if model == "chipyard-ibex" else "custom"]
    r = run_platform(
        project, config, generated, paths["models"], paths["bridge"], None, tmp_path / "run", cpu
    )
    assert r["status"] == "PASSED", r
    verify_platform(tmp_path / "run")
    row = json.loads((tmp_path / "run/baremetal/report.json").read_text())
    assert firmware_ids.setdefault(name, row["elf_sha256"]) == row["elf_sha256"]
    assert all(row["checks"].values())
    assert row["performance"]["instructions"] > 100
    assert row["performance"]["cycles"] > row["performance"]["instructions"]
    assert row["performance"]["kind"] == "native-rtl-counters"
    if engine == "gem5":
        assert row["engine_statistics"]["system.cpu.thread_0.numInsts"] == 0
    with (tmp_path / "run/baremetal/rtl-counters.json.rvfi.jsonl").open("a") as f:
        f.write("{}\n")
    with pytest.raises(ContractError, match="modified"):
        verify_platform(tmp_path / "run")


@pytest.mark.parametrize("engine", ["qemu", "gem5"])
def test_actual_cycle_limit_cannot_pass_or_start_an_iss(tmp_path, engine):
    paths = native_paths()
    r = run_cpu(
        load_project(ROOT / "examples/rv64-ips"),
        ROOT / "models/cpus/chipyard-ibex.yaml",
        paths["ibex"],
        Path(os.environ["USP_IP_GENERATED_DIR"]),
        paths["models"],
        engine,
        tmp_path / "run",
        paths["bridge"],
        max_cycles=128,
    )
    assert r["status"] == "FAILED"
    assert not r["checks"]["finisher"]
    assert r["checks"]["no_iss_execution"] is (engine == "qemu")
    verify_run(tmp_path / "run")


def test_actual_unchanged_rv64_firmware_is_rejected_before_start():
    native_paths()
    with pytest.raises(ContractError, match="RV32"):
        validate_elf(
            load_project(ROOT / "examples/rv64-ips"),
            Path(os.environ["USP_IP_GENERATED_DIR"]) / "zephyr-build/zephyr/zephyr.elf",
        )


def test_actual_modified_cpu_binary_and_wrong_port_width_are_rejected(tmp_path):
    paths = native_paths()
    header = (paths["ibex"] / "sim/Vusp_rtl_cpu.h").read_text()
    validate_ports(header)
    with pytest.raises(ContractError, match="ports"):
        validate_ports(header.replace("&d_addr_o,31,0", "&d_addr_o,15,0"))
    copied = tmp_path / "cpu"
    shutil.copytree(paths["ibex"], copied)
    manifest = copied / "cpu-build.json"
    original = json.loads(manifest.read_text())
    for field in ("core_commit", "builder_sha256", "model", "contract"):
        modified = {**original, field: "unverified"}
        manifest.write_text(json.dumps(modified))
        with pytest.raises(ContractError, match="stale/modified"):
            verify_cpu(ROOT / "models/cpus/chipyard-ibex.yaml", copied)
    manifest.write_text(json.dumps(original))
    with (copied / "sim/Vusp_rtl_cpu").open("ab") as f:
        f.write(b"tamper")
    with pytest.raises(ContractError, match="modified"):
        verify_cpu(ROOT / "models/cpus/chipyard-ibex.yaml", copied)
