import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from socsim.domain import ContractError
from socsim.ipxact import elaborate
from socsim.soc_native import accept, generate, run, verify, verify_generated
from socsim.soc_spec import load_spec

ROOT = Path(__file__).resolve().parents[1]


def write_spec(tmp_path, mutate=None):
    spec = load_spec(ROOT / "specs/AI_SOC-native.yaml")
    if mutate:
        mutate(spec)
    path = tmp_path / "input.yaml"
    path.write_text(yaml.safe_dump(spec))
    return path


def test_multiple_instances_roundtrip_through_real_xsd_and_ir(tmp_path):
    source = write_spec(tmp_path)
    out = tmp_path / "generated"
    result = generate(source, out)
    assert result["instances"] == 13
    spec = verify_generated(out)
    placements = {d["name"]: d["base"] for d in spec["devices"]}
    placements[spec["cpu"]["name"]] = 0
    parsed = elaborate(out, placements, out / "ipxact/design.xml")
    by_name = {i.name: i for i in parsed.instances}
    assert by_name["accelerator_a"].vlnv == by_name["accelerator_b"].vlnv
    assert by_name["accelerator_a"].windows[0].base == 0x13000000
    assert by_name["accelerator_b"].windows[0].base == 0x13001000
    assert by_name["scratch_sram"].windows[0].size == 0x10000
    assert by_name["main_sram"].windows[0].size == 0x1000000
    ir = json.loads((out / "soc-ir.json").read_text())
    assert ("accelerator_b", 14) in {(i["source"], i["line"]) for i in ir["interrupts"]}
    assert len(ir["address_spaces"][0]["targets"]) == 12


def test_unseen_configuration_renames_every_instance_and_adds_existing_ip(tmp_path):
    def change(s):
        s["name"] = "unseen_spec"
        for d in s["devices"]:
            d["name"] = "new_" + d["name"]
        s["cpu"]["name"] = "new_hart"
        for k in ("ram", "rom", "console"):
            s["boot"][k] = "new_" + s["boot"][k]
        s["devices"].append(
            {
                "name": "third_accelerator",
                "model": "chipyard.gcd",
                "base": 0x15000000,
                "size": 0x1000,
                "irq": 31,
            }
        )

    out = tmp_path / "new-machine"
    generate(write_spec(tmp_path, change), out)
    ir = json.loads((out / "soc-ir.json").read_text())
    assert len(ir["instances"]) == 14
    assert {i["name"] for i in ir["instances"]} >= {"third_accelerator", "new_hart"}
    assert "native_attach(memory, plic, 4, 0x15000000, 31, 0" in (out / "machine.c").read_text()
    assert "#define USP_INSTANCE_THIRD_ACCELERATOR_IRQ 31" in (out / "firmware/soc.h").read_text()


@pytest.mark.parametrize(
    "case",
    [
        "duplicate",
        "case_collision",
        "unsafe_name",
        "reserved_name",
        "unknown_model",
        "cpu_adapter",
        "cpu_model_type",
        "gem5_model_type",
        "overlap",
        "alignment",
        "overflow",
        "empty_ram",
        "bad_aperture",
        "irq_zero",
        "irq_range",
        "irq_duplicate",
        "irq_boolean",
        "unknown_field",
        "wrong_console",
        "wrong_ram",
        "entry_outside",
        "entry_alignment",
        "duplicate_plic",
        "timebase",
        "schema",
        "boot_field",
    ],
)
def test_invalid_specs_fail_before_generating_any_output(tmp_path, case):
    def change(s):
        first, uart = s["devices"][0], s["devices"][6]
        if case == "duplicate":
            s["devices"][1]["name"] = first["name"]
        elif case == "case_collision":
            s["devices"][1]["name"] = first["name"].upper()
        elif case == "unsafe_name":
            first["name"] = "x;echo bad"
        elif case == "reserved_name":
            first["name"] = "__usp_hlic"
        elif case == "unknown_model":
            first["model"] = "unknown.ai"
        elif case == "cpu_adapter":
            s["cpu"]["model"] = "chipyard.rocket"
        elif case == "cpu_model_type":
            s["cpu"]["model"] = {}
        elif case == "gem5_model_type":
            s["cpu"]["gem5_model"] = []
        elif case == "overlap":
            s["devices"][1]["base"] = first["base"]
        elif case == "alignment":
            first["base"] += 1
        elif case == "overflow":
            first["base"] = 0xFFFFFFFFF000
        elif case == "empty_ram":
            first["size"] = 0
        elif case == "bad_aperture":
            uart["size"] = 0x1000
        elif case == "irq_zero":
            uart["irq"] = 0
        elif case == "irq_range":
            uart["irq"] = 32
        elif case == "irq_duplicate":
            uart["irq"] = s["devices"][7]["irq"]
        elif case == "irq_boolean":
            uart["irq"] = True
        elif case == "unknown_field":
            uart["clock"] = 1
        elif case == "wrong_console":
            s["boot"]["console"] = first["name"]
        elif case == "wrong_ram":
            s["boot"]["ram"] = uart["name"]
        elif case == "entry_outside":
            s["boot"]["entry"] = 0x90000000
        elif case == "entry_alignment":
            s["boot"]["entry"] += 1
        elif case == "duplicate_plic":
            s["devices"].append(
                {"name": "another_plic", "model": "plic", "base": 0x4000000, "size": 0x4000000}
            )
        elif case == "timebase":
            s["cpu"]["timebase_hz"] = 0
        elif case == "schema":
            s["schema_version"] = "2"
        elif case == "boot_field":
            s["boot"]["ignored"] = 1

    out = tmp_path / "generated"
    with pytest.raises(ContractError):
        generate(write_spec(tmp_path, change), out)
    assert not out.exists()


def test_gem5_requires_explicit_cpu_selection_before_output(tmp_path):
    out = tmp_path / "generated"
    source = write_spec(tmp_path, lambda s: s["cpu"].pop("gem5_model"))
    with pytest.raises(ContractError, match="explicit cpu.gem5_model"):
        generate(source, out, engine="gem5")
    assert not out.exists()


def test_modified_generated_header_is_not_accepted(tmp_path):
    out = tmp_path / "generated"
    generate(ROOT / "specs/AI_SOC-native.yaml", out)
    (out / "firmware/soc.h").write_text("#define WRONG_MAP 1\n")
    with pytest.raises(ContractError, match="modified generated"):
        verify_generated(out)


def test_generation_is_independent_of_output_directory(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    generate(ROOT / "specs/AI_SOC-native.yaml", first)
    generate(ROOT / "specs/AI_SOC-native.yaml", second)
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()


def test_both_engine_lowerings_bind_all_declared_instances(tmp_path):
    out = tmp_path / "generated"
    generate(ROOT / "specs/AI_SOC-native.yaml", out, engine="both")
    bindings = json.loads((out / "engine-bindings.json").read_text())
    assert set(bindings) == {"qemu", "gem5"}
    assert len(bindings["qemu"]) == len(bindings["gem5"]) == 13
    assert bindings["qemu"][0]["implementation"] == "qemu.rv64"
    assert bindings["gem5"][0]["implementation"] == "gem5.RiscvTimingSimpleCPU"


@pytest.mark.parametrize("name", ["AI_SOC", "AIZ_SOC"])
def test_actual_generated_native_hardware_and_firmware(tmp_path, name):
    if os.environ.get("USP_REQUIRE_NATIVE_SOC") != "1":
        pytest.skip("requires both generated native machines and actual source-IP models")
    generated = Path(os.environ[f"USP_NATIVE_{name}_BUILD"])
    assert load_spec(generated / "spec.yaml") == load_spec(ROOT / f"specs/{name}-native.yaml")
    out = tmp_path / "native-run"
    report = accept(
        generated,
        Path(os.environ["USP_IP_MODELS_DIR"]),
        Path(os.environ["USP_NATIVE_GEM5_BUILD"]),
        out,
    )
    assert report["status"] == verify(out)["status"] == "PASSED"
    assert all(report["checks"].values())
    for engine in ("qemu", "gem5"):
        child = json.loads((out / engine / "report.json").read_text())
        assert len(child["model_exit_codes"]) == 4
        if engine == "gem5":
            assert child["performance"]["counters"]["instructions"] > 0
            assert child["performance"]["native_ipc"] > 0
    assert report["full_master_objective_complete"] is False
    assert "usp-native" in (out / "qemu/engine.command.json").read_text()
    path = out / "gem5/engine.log"
    path.write_text(path.read_text() + "corrupted evidence\n")
    with pytest.raises(ContractError, match="modified native run evidence"):
        verify(out)


@pytest.mark.parametrize("case", ["guest-failure", "timeout", "wrong-entry"])
@pytest.mark.parametrize("engine", ["qemu", "gem5"])
def test_actual_native_user_firmware_failures_are_not_success(tmp_path, case, engine):
    if os.environ.get("USP_REQUIRE_NATIVE_SOC") != "1":
        pytest.skip("requires generated native machine, firmware compiler and source-IP models")
    generated = Path(os.environ["USP_NATIVE_AI_SOC_BUILD"])
    spec = load_spec(generated / "spec.yaml")
    finish = next(d["base"] for d in spec["devices"] if d["model"] == "finisher")
    body = (
        "1: j 1b\n"
        if case == "timeout"
        else f"li t0, {finish}\nli t1, 0x13333\nsw t1, 0(t0)\n1: j 1b\n"
    )
    console = next(d["base"] for d in spec["devices"] if d["name"] == spec["boot"]["console"])
    body = (
        f"li t0, {console}\nli t1, 88\nsb t1, 0(t0)\n"
        "2: lbu t2, 5(t0)\nandi t2, t2, 64\nbeqz t2, 2b\n" + body
    )
    asm = tmp_path / "negative.S"
    asm.write_text(".section .text\n.global _start\n_start:\n" + body)
    entry = spec["boot"]["entry"] + (0x1000 if case == "wrong-entry" else 0)
    elf = tmp_path / "negative.elf"
    linker = tmp_path / "negative.ld"
    linker.write_text(
        (generated / "firmware/linker.ld")
        .read_text()
        .replace(hex(spec["boot"]["entry"]), hex(entry), 1)
    )
    subprocess.run(
        [
            os.environ.get("CROSS_COMPILE", "riscv64-unknown-elf-") + "gcc",
            "-march=rv64imac_zicsr_zifencei",
            "-mabi=lp64",
            "-nostdlib",
            "-Wl,-e,_start",
            "-T",
            str(linker),
            str(asm),
            "-o",
            str(elf),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    out = tmp_path / "negative-run"
    report = run(
        generated,
        Path(os.environ["USP_IP_MODELS_DIR"]),
        out,
        timeout=3 if case == "timeout" else 10,
        firmware=elf,
        engine=engine,
        gem5_build=Path(os.environ["USP_NATIVE_GEM5_BUILD"]),
    )
    assert report["status"] == verify(out)["status"] == "FAILED"
    assert not report["checks"]["engine_exit"]
    command = json.loads((out / "engine.command.json").read_text())
    if case == "timeout":
        assert command["timed_out"]
        assert "X" in (out / (spec["boot"]["console"] + ".uart.log")).read_text()
    elif case == "wrong-entry":
        if engine == "qemu":
            assert (
                "ELF does not match generated entry/RAM contract"
                in (out / "engine.log").read_text()
            )
        else:
            assert command["phase"] == "not-launched"
            assert "entry/CPU contract" in report["error"]
    else:
        assert command["returncode"] == 1
        assert "ELF does not match" not in (out / "engine.log").read_text()
        assert "X" in (out / (spec["boot"]["console"] + ".uart.log")).read_text()
        if engine == "gem5":
            assert report["performance"]["counters"]["instructions"] > 0
