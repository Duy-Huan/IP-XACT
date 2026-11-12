"""CPU-owned RTL test chips, independent of the larger 46/37-instance targets."""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path

from jsonschema import ValidationError, validate

from .domain import ContractError
from .execution import cleanup_on_termination
from .hybrid import OPENTITAN_SHA
from .ipblocks import CHIPYARD_SHA, verify_models
from .ipxact import digest
from .pipeline import execute, verify_source
from .project import inside, load_project, read_yaml
from .runner import tool_version, write_json

RESOURCES = Path(__file__).parent / "resources/testchip"
NANGATE_LIB_SHA256 = "8d540a4d4cf6d09d27c87ad067857a9c0c2eeb023ab7a56e058cd3113db4e9b1"
CONTRACTS = {
    "cpu": ("lowrisc:ibex:ibex_top:silver-v5", "opentitan.ibex-v1"),
    "rom": ("socsim.public:rtl:rom:1.0", "usp.rom-case-v1"),
    "ram": ("socsim.public:rtl:sram:1.0", "usp.sram-byte-v1"),
    "uart": ("lowrisc:ip:uart:silver-v5", "opentitan.uart-v1"),
    "gpio": ("lowrisc:ip:gpio:silver-v5", "opentitan.gpio-v1"),
    "gcd": ("ucb-bar:chipyard:gcd:1.13.0", "chipyard.gcd-v1"),
    "timer": ("lowrisc:ip:rv_timer:silver-v5", "opentitan.rv-timer-v1"),
    "finisher": ("socsim.public:rtl:finisher:1.0", "usp.finisher-v1"),
    "fabric": ("socsim.public:rtl:obi_tlul:1.0", "usp.obi-tlul-v1"),
}
PACKAGES = [
    "hw/top_earlgrey/rtl/top_pkg.sv",
    "hw/ip/prim/rtl/prim_util_pkg.sv",
    "hw/ip/prim/rtl/prim_secded_pkg.sv",
    "hw/ip/prim/rtl/prim_alert_pkg.sv",
    "hw/ip/prim/rtl/prim_ram_1p_pkg.sv",
    "hw/ip/tlul/rtl/tlul_pkg.sv",
    "hw/ip/gpio/rtl/gpio_reg_pkg.sv",
    "hw/ip/uart/rtl/uart_reg_pkg.sv",
    "hw/ip/rv_timer/rtl/rv_timer_reg_pkg.sv",
    "hw/vendor/lowrisc_ibex/rtl/ibex_pkg.sv",
]
DIRECTORIES = [
    "hw/ip/prim/rtl",
    "hw/ip/prim_generic/rtl",
    "hw/ip/tlul/rtl",
    "hw/ip/gpio/rtl",
    "hw/ip/uart/rtl",
    "hw/ip/rv_timer/rtl",
    "hw/vendor/lowrisc_ibex/rtl",
    "hw/dv/sv/dv_utils",
]


def load_chip(root):
    root = root.resolve()
    config = read_yaml(inside(root, "chip.yaml"))
    schema = json.loads((Path(__file__).parent / "schemas/chip-v1.json").read_text())
    try:
        validate(config, schema)
    except ValidationError as exc:
        raise ContractError(f"E_CHIP_SCHEMA {list(exc.absolute_path)}: {exc.message}") from exc
    project = load_project(root)
    if project.manifest["profile"] != "ibex-testchip-v1":
        raise ContractError("unsupported RTL chip profile")
    if project.ir.vlnv != f"socsim.public:testchip:{config['name']}:1.0":
        raise ContractError("test-chip identity mismatch")
    instances = {i.name: i for i in project.ir.instances}
    if set(instances) != set(CONTRACTS):
        raise ContractError("RTL chip requires exactly its nine declared components")
    expected = []
    for name, (vlnv, implementation) in CONTRACTS.items():
        if instances[name].vlnv != vlnv:
            raise ContractError(f"{name}: unsupported exact VLNV")
        expected.append({"vlnv": vlnv, "backend": "rtl", "implementation": implementation})
    if sorted(project.models["bindings"], key=lambda b: b["vlnv"]) != sorted(
        expected, key=lambda b: b["vlnv"]
    ):
        raise ContractError("missing, ambiguous or incompatible RTL model bindings")
    for name, inst in instances.items():
        if inst.parameters:
            raise ContractError("unsupported undeclared test-chip parameter semantics")
        if name in {"cpu", "fabric"}:
            if inst.windows:
                raise ContractError("CPU/fabric cannot expose an MMIO window")
            continue
        if len(inst.windows) != 1 or inst.windows[0].width != 32 or inst.windows[0].registers:
            raise ContractError("unsupported test-chip window/register contract")
        w = inst.windows[0]
        size = {"rom": {8192}, "ram": {1024, 2048}, "gcd": {16}, "finisher": {4}}.get(name, {4096})
        if w.size not in size or w.base % 4096 or w.base + w.size > 2**32:
            raise ContractError(f"{name}: unsupported range/alignment")
    return config, project


def render(text, values):
    for key, value in values.items():
        text = text.replace("@" + key + "@", str(value))
    if re.search(r"@[A-Z_]+@", text):
        raise ContractError("unresolved RTL/firmware template token")
    return text


def adapter_identity():
    return {
        "adapter": digest(Path(__file__)),
        "resources": {p.name: digest(p) for p in sorted(RESOURCES.iterdir()) if p.is_file()},
    }


def prepare_output(out, manifest, rebuild):
    if not out.exists():
        out.mkdir(parents=True)
        return
    if not rebuild or not (out / manifest).is_file():
        raise ContractError("output exists; use a new directory or explicit --rebuild")
    previous = json.loads((out / manifest).read_text())
    for relative, sha in previous["files"].items():
        if digest(inside(out, relative)) != sha:
            raise ContractError("preserve modified generated artifacts before rebuilding")
    if (
        previous["name"] not in {"AI_SOC", "AIZ_SOC"}
        or digest(out / f"sim/V{previous['name']}") != previous["binary_sha256"]
    ):
        raise ContractError("preserve modified executable before rebuilding")
    history = out / ".history" / digest(out / manifest)
    history.mkdir(parents=True, exist_ok=True)
    for relative in (*previous["files"], manifest):
        target = history / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(inside(out, relative), target)


def build_chip(
    root, out, opentitan, models, cross_compile="riscv64-unknown-elf-", jobs=4, rebuild=False
):
    config, project = load_chip(root)
    identity = adapter_identity()
    configuration_inputs = {
        str(project.root / relative): sha for relative, sha in project.ir.sources
    }
    configuration_inputs[str(project.root / "chip.yaml")] = digest(project.root / "chip.yaml")
    opentitan = verify_source(opentitan.resolve(), OPENTITAN_SHA)
    models = models.resolve()
    model = verify_models(models)
    cc, cc_version = tool_version(cross_compile + "gcc")
    verilator, version = tool_version("verilator")
    if not version.startswith("Verilator 5.032"):
        raise ContractError("RTL chip requires Verilator 5.032")
    out = out.resolve()
    prepare_output(out, "chip-build.json", rebuild)
    windows = {i.name: i.windows[0] for i in project.ir.instances if i.windows}
    values = {name.upper() + "_BASE": w.base for name, w in windows.items()}
    values["FINISH_BASE"] = windows["finisher"].base
    values.update(
        NAME=config["name"],
        ROM_BYTES=windows["rom"].size,
        RAM_BYTES=windows["ram"].size,
        RAM_WORDS=windows["ram"].size // 4,
        WRITEBACK=int(config["writeback_stage"]),
        BRANCH=int(config["branch_target_alu"]),
        HALF_TICKS=10**12 // (2 * config["clock_hz"]),
    )
    (out / "chip.h").write_text(
        "#include <stdint.h>\n"
        + "".join(f"#define {k} {v}u\n" for k, v in values.items() if k.endswith("_BASE"))
    )
    (out / "firmware.c").write_text(render((RESOURCES / "firmware.c.in").read_text(), values))
    rom, ram = windows["rom"], windows["ram"]
    (out / "link.ld").write_text(f"""ENTRY(_start)
MEMORY {{ ROM (rx) : ORIGIN = {rom.base}, LENGTH = {rom.size}
RAM (rw) : ORIGIN = {ram.base}, LENGTH = {ram.size} }}
SECTIONS {{
 .reset {rom.base + 128} : {{ KEEP(*(.reset)) }} > ROM
 .vectors {rom.base + 256} : {{ KEEP(*(.vectors)) }} > ROM
 .text : {{ *(.text*) *(.rodata*) }} > ROM
 .data : {{ *(.data*) *(.sdata*) }} > RAM AT> ROM
 .bss (NOLOAD) : {{ . = ALIGN(4); _bss_start = .; *(.bss*) *(.sbss*) *(COMMON)
  . = ALIGN(4); _bss_end = .; }} > RAM
 _stack_top = ORIGIN(RAM) + LENGTH(RAM) - 16;
 /DISCARD/ : {{ *(.comment*) *(.riscv.attributes*) }}
}}
""")
    command = [
        cc,
        "-march=rv32imc_zicsr_zifencei",
        "-mabi=ilp32",
        "-Os",
        "-ffreestanding",
        "-fno-builtin",
        "-msmall-data-limit=0",
        "-mno-relax",
        "-nostdlib",
        "-Wl,--build-id=none",
        "-T",
        str(out / "link.ld"),
        "-I",
        str(out),
        str(RESOURCES / "start.S"),
        str(out / "firmware.c"),
        "-o",
        str(out / "firmware.elf"),
    ]
    execute(command, out, out / "firmware-build.log", 60)
    from elftools.elf.elffile import ELFFile

    image = bytearray(rom.size)
    with (out / "firmware.elf").open("rb") as stream:
        elf = ELFFile(stream)
        if (
            elf.elfclass != 32
            or not elf.little_endian
            or elf.header["e_machine"] != "EM_RISCV"
            or elf.header["e_entry"] != rom.base + 128
        ):
            raise ContractError("firmware must match the explicit RV32 reset/ROM contract")
        for segment in elf.iter_segments():
            if segment["p_type"] != "PT_LOAD" or segment["p_filesz"] == 0:
                continue
            offset = segment["p_paddr"] - rom.base
            data = segment.data()
            if (
                offset < 0
                or offset + len(data) > rom.size
                or segment["p_vaddr"] != segment["p_paddr"]
            ):
                raise ContractError("unsupported firmware initializer/ROM range")
            image[offset : offset + len(data)] = data
    (out / "firmware.bin").write_bytes(image)
    values["ROM_CASES"] = "\n".join(
        f"      30'h{(rom.base + i) // 4:08x}: "
        f"rom_word = 32'h{int.from_bytes(image[i : i + 4], 'little'):08x};"
        for i in range(0, len(image), 4)
        if any(image[i : i + 4])
    )
    top = config["name"]
    for source, dest in (("chip.sv.in", top + ".sv"), ("sim.cpp.in", "sim.cpp")):
        (out / dest).write_text(render((RESOURCES / source).read_text(), values))
    args = [
        verilator,
        "--cc",
        "--exe",
        "--build",
        "--assert",
        "--timescale",
        "1ns/1ps",
        "--top-module",
        top,
        "--Mdir",
        str(out / "sim"),
        "-j",
        str(jobs),
        "-Wno-fatal",
    ]
    for directory in DIRECTORIES:
        args += ["-I" + str(opentitan / directory), "-y", str(opentitan / directory)]
    initial = [opentitan / p for p in PACKAGES]
    args += list(map(str, initial)) + [
        str(RESOURCES / "primitives.sv"),
        str(models / "generator/rtl/GCDMMIOChiselModule.sv"),
        str(out / (top + ".sv")),
        str(out / "sim.cpp"),
    ]
    execute(args, out, out / "verilator-build.log", 600)
    closure = []
    for line in (out / f"sim/V{top}__verFiles.dat").read_text().splitlines():
        if line.startswith("S "):
            path = Path(line.split('"')[1])
            if path.suffix in {".sv", ".svh"} and path.name != "verilated_std.sv":
                closure.append(path)
    ordered = initial + [p for p in closure if p not in initial and p.suffix == ".sv"]
    preprocessing = [verilator, "-E", "-P", "--pp-comments", "-DSYNTHESIS"]
    for parent in sorted({p.parent for p in closure}):
        preprocessing.append("-I" + str(parent))
    preprocessing += list(map(str, ordered))
    with (out / "consolidated.sv").open("w") as stream:
        subprocess.run(preprocessing, stdout=stream, stderr=subprocess.PIPE, check=True, text=True)
    period = 10**9 / config["clock_hz"]
    (out / "chip.sdc").write_text(
        f"create_clock -name system_clk -period {period} [get_ports clk_i]\n"
        "set_input_delay 0.2 -clock system_clk [get_ports {rst_ni uart_rx_i}]\n"
        "set_output_delay 0.2 -clock system_clk [all_outputs]\n"
    )
    licenses = out / "licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copy2(opentitan / "LICENSE", licenses / "OpenTitan-LICENSE")
    shutil.copy2(models / "licenses/Chipyard-LICENSE", licenses / "Chipyard-LICENSE")
    write_json(out / "chip-ir.json", project.ir.to_dict())
    write_json(out / "chip-config.json", config)
    if adapter_identity() != identity or any(
        digest(Path(p)) != sha for p, sha in configuration_inputs.items()
    ):
        raise ContractError("adapter or configuration changed during chip compilation")
    verify_source(opentitan, OPENTITAN_SHA)
    managed = [p for p in out.iterdir() if p.is_file() and p.name != "chip-build.json"]
    record = {
        "contract": "ibex-testchip-v1",
        "name": top,
        "adapter_identity": identity,
        "configuration_inputs": configuration_inputs,
        "opentitan_commit": OPENTITAN_SHA,
        "chipyard_commit": CHIPYARD_SHA,
        "source_model_binary_sha256": model["binary_sha256"],
        "cc_version": cc_version,
        "cc_sha256": digest(Path(cc)),
        "verilator": version,
        "verilator_sha256": digest(Path(verilator)),
        "binary_sha256": digest(out / f"sim/V{top}"),
        "inputs": {str(p): digest(p) for p in closure},
        "files": {p.relative_to(out).as_posix(): digest(p) for p in managed},
        "full_architecture_complete": False,
    }
    write_json(out / "chip-build.json", record)
    return record


def verify_chip(out):
    out = out.resolve()
    r = json.loads((out / "chip-build.json").read_text())
    if r["contract"] != "ibex-testchip-v1" or r["adapter_identity"] != adapter_identity():
        raise ContractError("stale RTL test-chip adapter; rebuild explicitly")
    if r["full_architecture_complete"] is not False or r["name"] not in {"AI_SOC", "AIZ_SOC"}:
        raise ContractError("invalid test-chip identity/scope")
    for name, sha in r["files"].items():
        if digest(inside(out, name)) != sha:
            raise ContractError("modified RTL/firmware chip artifact")
    if digest(out / f"sim/V{r['name']}") != r["binary_sha256"]:
        raise ContractError("modified chip executable")
    for path, sha in r["inputs"].items():
        if digest(Path(path)) != sha:
            raise ContractError("modified upstream or generated chip source")
    for path, sha in r["configuration_inputs"].items():
        if digest(Path(path)) != sha:
            raise ContractError("stale project configuration; explicitly rebuild the RTL chip")
    return r


@cleanup_on_termination
def run_chip(generated, out, boots=2, max_cycles=500000, gate=None):
    generated = generated.resolve()
    record = verify_chip(generated)
    if (
        type(boots) is not int
        or not 1 <= boots <= 3
        or type(max_cycles) is not int
        or not 1 <= max_cycles <= 1000000
    ):
        raise ContractError("unsupported chip run bounds")
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    binary = generated / f"sim/V{record['name']}"
    if gate is not None:
        gate_record = verify_gate(gate, generated)
        binary = gate.resolve() / f"sim/V{record['name']}"
    argv = [str(binary), str(max_cycles), str(boots)]
    write_json(out / "command.json", {"argv": argv, "cwd": str(out)})
    timed_out = False
    with (out / "uart.log").open("w") as serial, (out / "host.log").open("w") as host:
        p = subprocess.Popen(argv, cwd=out, stdout=serial, stderr=host, start_new_session=True)
        try:
            rc = p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(p.pid, signal.SIGKILL)
            rc = p.wait()
        finally:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
    log = (out / "uart.log").read_text(errors="replace")
    markers = [
        record["name"] + " CPU_BOOT_OK",
        "SRAM_BYTE_OK",
        "BUS_FAULT_OK",
        "TIMER_IRQ_OK",
        "GPIO_IRQ_OK",
        "GCD_IRQ_OK",
        record["name"] + " CHIP_PASS",
    ]
    checks = {marker: log.count(marker) == boots for marker in markers}
    report = {
        "status": "PASSED"
        if rc == 0 and all(checks.values())
        else "TIMED_OUT"
        if timed_out or rc == 5
        else "FAILED",
        "name": record["name"],
        "scope": "complete functional bare-metal RTL test-chip configuration",
        "returncode": rc,
        "timed_out": timed_out,
        "boots": boots,
        "max_cycles": max_cycles,
        "level": "post-route-functional" if gate is not None else "rtl",
        "checks": checks,
        "binary_sha256": gate_record["binary_sha256"]
        if gate is not None
        else record["binary_sha256"],
        "firmware_sha256": record["files"]["firmware.elf"],
        "build_sha256": digest(generated / "chip-build.json"),
        "full_architecture_complete": False,
        "limitations": [
            "One RV32 Ibex hart; no Linux/MMU or heterogeneous Rocket/DSP cluster",
            "Original OBI/TL-UL fabric; Garnet remains an independent timing experiment",
            "GPIO conformance loopback; no foundry/IO/pad/security signoff",
        ],
    }
    shutil.copy2(generated / "chip-build.json", out / "chip-build.json")
    write_json(out / "report.json", report)
    (out / "report.md").write_text(
        "# "
        + record["name"]
        + " RTL chip\n\nStatus: **"
        + report["status"]
        + "**\n\n"
        + report["scope"]
        + "\n\n"
        + "\n".join("- " + s for s in report["limitations"])
        + "\n"
    )
    return report


def build_gate(generated, physical, out, jobs=4, rebuild=False):
    from .asic import verify_asic

    generated, physical = generated.resolve(), physical.resolve()
    record = verify_chip(generated)
    identity = adapter_identity()
    layout = verify_asic(physical)
    top = record["name"]
    if layout["top"] != top or layout["rtl_sha256"] != digest(generated / "consolidated.sv"):
        raise ContractError("physical netlist does not match this complete test-chip RTL")
    yosys, version = tool_version("yosys")
    if not version.startswith("Yosys 0.52 "):
        raise ContractError("functional cell import requires pinned Yosys 0.52")
    out = out.resolve()
    prepare_output(out, "gate-build.json", rebuild)
    lib = physical / "flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib"
    if digest(lib) != NANGATE_LIB_SHA256:
        raise ContractError("Nangate Liberty differs from the pinned OCI technology input")
    netlist = physical / f"flow/results/nangate45/{top}/base/6_final.v"
    shutil.copy2(lib, out / "cells.lib")
    shutil.copy2(netlist, out / "netlist.v")
    shutil.copy2(physical / "flow/platforms/nangate45/LICENSE", out / "Nangate45-LICENSE")
    (out / "cells.ys").write_text(
        "read_liberty -ignore_miss_func cells.lib\nwrite_verilog -noattr cells.v\n"
    )
    execute([yosys, "-Q", "-T", "-s", "cells.ys"], out, out / "cell-import.log", 60)
    skipped = set(
        re.findall(
            r"Ignoring cell (\w+) with missing function", (out / "cell-import.log").read_text()
        )
    )
    allowed = {f"CLKGATE{test}_X{size}" for test in ("", "TST") for size in (1, 2, 4, 8)}
    if skipped != allowed:
        raise ContractError("unsupported Liberty function/statetable omissions")
    execute(
        [
            "verilator",
            "--cc",
            "--exe",
            "--build",
            "--timescale",
            "1ns/1ps",
            "--top-module",
            top,
            "--Mdir",
            str(out / "sim"),
            "-j",
            str(jobs),
            "-Wno-fatal",
            str(out / "cells.v"),
            str(RESOURCES / "clock_cells.v"),
            str(out / "netlist.v"),
            str(generated / "sim.cpp"),
        ],
        out,
        out / "gate-build.log",
        1200,
    )
    if adapter_identity() != identity:
        raise ContractError("adapter changed during mapped-chip compilation")
    result = {
        "name": top,
        "physical_report_sha256": digest(physical / "report.json"),
        "rtl_build_sha256": digest(generated / "chip-build.json"),
        "liberty_sha256": digest(lib),
        "postroute_netlist_sha256": digest(netlist),
        "yosys_sha256": digest(Path(yosys)),
        "adapter_identity": identity,
        "binary_sha256": digest(out / f"sim/V{top}"),
        "files": {
            p.name: digest(p) for p in out.iterdir() if p.is_file() and p.name != "gate-build.json"
        },
        "scope": "zero-delay functional post-route netlist; not SDF or transistor simulation",
    }
    write_json(out / "gate-build.json", result)
    return result


def verify_gate(gate, generated):
    gate = gate.resolve()
    record = json.loads((gate / "gate-build.json").read_text())
    if record["adapter_identity"] != adapter_identity() or record["rtl_build_sha256"] != digest(
        generated / "chip-build.json"
    ):
        raise ContractError("stale mapped-chip build identity")
    for name, sha in record["files"].items():
        if digest(inside(gate, name)) != sha:
            raise ContractError("modified mapped-chip artifact")
    if digest(gate / f"sim/V{record['name']}") != record["binary_sha256"]:
        raise ContractError("modified mapped-chip executable")
    return record


def accept_chip(generated, physical, gate, out):
    from .asic import reference_checks, verify_asic

    generated, physical, gate = generated.resolve(), physical.resolve(), gate.resolve()
    native = verify_chip(generated)
    mapped = verify_gate(gate, generated)
    layout = verify_asic(physical)
    if mapped["physical_report_sha256"] != digest(physical / "report.json"):
        raise ContractError("mapped executable and physical layout provenance differ")
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    rtl = run_chip(generated, out / "rtl", boots=2)
    post = run_chip(generated, out / "mapped", boots=2, gate=gate)
    negative = run_chip(generated, out / "cycle-limit", boots=1, max_cycles=20)
    serial_equal = (out / "rtl/uart.log").read_bytes() == (out / "mapped/uart.log").read_bytes()
    pdir = out / "physical"
    pdir.mkdir()
    for relative in layout["artifacts"]:
        target = pdir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(inside(physical, relative), target)
    for name in ("report.json", "report.md", "flow.log"):
        shutil.copy2(physical / name, pdir / name)
    for name in (
        "chip-build.json",
        "chip-ir.json",
        "chip-config.json",
        "firmware.elf",
        "firmware.bin",
    ):
        shutil.copy2(generated / name, out / name)
    shutil.copy2(gate / "gate-build.json", out / "gate-build.json")
    shutil.copytree(generated / "licenses", out / "licenses")
    shutil.copy2(gate / "Nangate45-LICENSE", out / "licenses/Nangate45-LICENSE")
    checks = {
        "rtl_boot_irq_memory": rtl["status"] == "PASSED",
        "mapped_boot_irq_memory": post["status"] == "PASSED",
        "identical_serial_trace": serial_equal,
        "cycle_limit_rejected": negative["status"] == "TIMED_OUT"
        and negative["returncode"] == 5
        and negative["max_cycles"] == 20,
        "reference_timing_drc": all(reference_checks(layout).values()),
    }
    result = {
        "schema_version": "1",
        "name": native["name"],
        "status": "PASSED" if all(checks.values()) else "FAILED",
        "scope": "complete nine-component functional RTL test-chip flow",
        "checks": checks,
        "complete_testchip_flow": all(checks.values()),
        "full_architecture_complete": False,
        "foundry_signoff": False,
        "files": {
            p.relative_to(out).as_posix(): digest(p) for p in sorted(out.rglob("*")) if p.is_file()
        },
        "limitations": [
            "The original 46/37-instance target architectures remain incomplete",
            "One RV32 hart, SRAM/ROM, UART/GPIO/timer/GCD and OBI/TL-UL fabric",
            "Mapped simulation is zero-delay; no SDF, full ISA compliance or foundry signoff",
            "Independent Garnet experiments are not coupled to this RTL interconnect",
        ],
    }
    write_json(out / "report.json", result)
    (out / "report.md").write_text(
        "# "
        + native["name"]
        + " complete test-chip flow\n\nStatus: **"
        + result["status"]
        + "**\n\n"
        + "\n".join(f"- {k}: {v}" for k, v in checks.items())
        + "\n\n"
        + "\n".join("- " + x for x in result["limitations"])
        + "\n"
    )
    return result


def verify_flow(out):
    from .asic import verify_asic

    out = out.resolve()
    r = json.loads((out / "report.json").read_text())
    if (
        r["schema_version"] != "1"
        or r["full_architecture_complete"] is not False
        or r["foundry_signoff"] is not False
    ):
        raise ContractError("invalid test-chip full-architecture/signoff claim")
    required = {
        "rtl/report.json",
        "rtl/uart.log",
        "mapped/report.json",
        "mapped/uart.log",
        "cycle-limit/report.json",
        "physical/report.json",
        "chip-ir.json",
        "chip-config.json",
        "chip-build.json",
        "gate-build.json",
        "firmware.elf",
        "firmware.bin",
    }
    if not required.issubset(r["files"]):
        raise ContractError("incomplete test-chip evidence")
    for relative, sha in r["files"].items():
        if digest(inside(out, relative)) != sha:
            raise ContractError("modified test-chip evidence")
    layout = verify_asic(out / "physical")
    build = json.loads((out / "chip-build.json").read_text())
    gate = json.loads((out / "gate-build.json").read_text())
    if (
        layout["top"] != r["name"]
        or build["name"] != r["name"]
        or layout["rtl_sha256"] != build["files"]["consolidated.sv"]
        or gate["physical_report_sha256"] != digest(out / "physical/report.json")
        or gate["rtl_build_sha256"] != digest(out / "chip-build.json")
    ):
        raise ContractError("test-chip stage identity mismatch")
    rtl = json.loads((out / "rtl/report.json").read_text())
    mapped = json.loads((out / "mapped/report.json").read_text())
    negative = json.loads((out / "cycle-limit/report.json").read_text())
    if (
        rtl["binary_sha256"] != build["binary_sha256"]
        or mapped["binary_sha256"] != gate["binary_sha256"]
        or rtl["build_sha256"] != digest(out / "chip-build.json")
        or mapped["build_sha256"] != digest(out / "chip-build.json")
        or digest(out / "firmware.elf") != build["files"]["firmware.elf"]
        or digest(out / "firmware.bin") != build["files"]["firmware.bin"]
    ):
        raise ContractError("mixed firmware/executable evidence in chip flow")
    from .asic import reference_checks

    checks = {
        "rtl_boot_irq_memory": rtl["status"] == "PASSED",
        "mapped_boot_irq_memory": mapped["status"] == "PASSED",
        "identical_serial_trace": (out / "rtl/uart.log").read_bytes()
        == (out / "mapped/uart.log").read_bytes(),
        "cycle_limit_rejected": negative["status"] == "TIMED_OUT"
        and negative["returncode"] == 5
        and negative["max_cycles"] == 20,
        "reference_timing_drc": all(reference_checks(layout).values()),
    }
    passed = all(checks.values())
    if (
        r["checks"] != checks
        or r["complete_testchip_flow"] is not passed
        or r["status"] != ("PASSED" if passed else "FAILED")
    ):
        raise ContractError("invalid test-chip acceptance claim")
    return r


def main(argv):
    parser = argparse.ArgumentParser(prog="usp chip")
    sub = parser.add_subparsers(dest="command", required=True)
    validate_cmd = sub.add_parser("validate")
    validate_cmd.add_argument("project", type=Path)
    build = sub.add_parser("build")
    build.add_argument("project", type=Path)
    for key in ("out", "source-opentitan", "models"):
        build.add_argument("--" + key, type=Path, required=True)
    build.add_argument("--cross-compile", default="riscv64-unknown-elf-")
    build.add_argument("--jobs", type=int, choices=range(1, 5), default=2)
    build.add_argument("--rebuild", action="store_true")
    test = sub.add_parser("test")
    for key in ("generated", "out"):
        test.add_argument("--" + key, type=Path, required=True)
    test.add_argument("--boots", type=int, default=2)
    test.add_argument("--max-cycles", type=int, default=500000)
    test.add_argument("--gate", type=Path)
    mapped = sub.add_parser("gate-build")
    for key in ("generated", "physical", "out"):
        mapped.add_argument("--" + key, type=Path, required=True)
    mapped.add_argument("--jobs", type=int, choices=range(1, 5), default=2)
    mapped.add_argument("--rebuild", action="store_true")
    accept = sub.add_parser("accept")
    for key in ("generated", "physical", "gate", "out"):
        accept.add_argument("--" + key, type=Path, required=True)
    sub.add_parser("verify-flow").add_argument("run_dir", type=Path)
    sub.add_parser("verify").add_argument("generated", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            cfg, project = load_chip(args.project)
            r = {
                "status": "PASSED",
                "name": cfg["name"],
                "instances": len(project.ir.instances),
                "scope": cfg["scope"],
            }
        elif args.command == "build":
            r = build_chip(
                args.project,
                args.out,
                args.source_opentitan,
                args.models,
                args.cross_compile,
                args.jobs,
                args.rebuild,
            )
        elif args.command == "test":
            r = run_chip(args.generated, args.out, args.boots, args.max_cycles, args.gate)
        elif args.command == "gate-build":
            r = build_gate(args.generated, args.physical, args.out, args.jobs, args.rebuild)
        elif args.command == "accept":
            r = accept_chip(args.generated, args.physical, args.gate, args.out)
        elif args.command == "verify-flow":
            r = verify_flow(args.run_dir)
        else:
            r = verify_chip(args.generated)
        print(json.dumps(r, indent=2))
        return 0 if r.get("status", "PASSED") == "PASSED" else 1
    except (ContractError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}))
        return 2
