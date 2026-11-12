"""Actual Yosys synthesis/equivalence of the verified Chipyard GCD RTL only."""

import json
import shutil
from pathlib import Path

from .domain import ContractError
from .ipblocks import verify_models
from .ipxact import digest
from .pipeline import execute
from .runner import tool_version, write_json


def synthesize_gcd(models, out):
    model = verify_models(models)
    binary, version = tool_version("yosys")
    if not version.startswith("Yosys 0.52 "):
        raise ContractError("synthesis profile requires Yosys 0.52")
    abc = Path(binary).parent / "yosys-abc"
    if not abc.is_file():
        raise ContractError("Yosys synthesis requires its pinned yosys-abc companion")
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    shutil.copy2(models / "generator/rtl/GCDMMIOChiselModule.sv", out / "source.sv")
    script = """read_verilog -sv source.sv
prep -top GCDMMIOChiselModule
design -stash gold
design -reset
read_verilog -sv source.sv
synth -top GCDMMIOChiselModule
check -assert
tee -o statistics.json stat -json
write_json netlist.json
write_verilog -noattr netlist.v
design -stash gate
design -reset
design -copy-from gold -as gold GCDMMIOChiselModule
design -copy-from gate -as gate GCDMMIOChiselModule
equiv_make gold gate equiv
hierarchy -top equiv
equiv_simple
equiv_induct -seq 10
equiv_status -assert
"""
    (out / "synth.ys").write_text(script)
    execute([str(binary), "-Q", "-T", "-s", "synth.ys"], out, out / "yosys.log", 300)
    netlist = json.loads((out / "netlist.json").read_text())
    module = netlist["modules"]["GCDMMIOChiselModule"]
    cells = module["cells"]
    if not cells or any(
        c["type"] not in {"0", "1"} and not c["type"].startswith("$_") for c in cells.values()
    ):
        raise ContractError("unexpected blackbox/unmapped cells after generic synthesis")
    report = {
        "status": "PASSED",
        "scope": "Chipyard GCD leaf; generic gate synthesis and equivalence",
        "full_soc_synthesized": False,
        "technology_mapped": False,
        "physical_implementation": False,
        "equivalence": "Yosys equiv_status -assert passed",
        "yosys_version": version,
        "yosys_binary_sha256": digest(Path(binary)),
        "yosys_abc_sha256": digest(abc),
        "input_rtl_sha256": digest(out / "source.sv"),
        "source_model_binary_sha256": model["binary_sha256"],
        "netlist_sha256": digest(out / "netlist.v"),
        "cell_count": len(cells),
        "limitations": [
            "No PDK mapping, STA, placement, routing, DRC/LVS or signoff",
            "No CPU, NoC, memory, IO PHY, security subsystem or complete SoC top",
        ],
    }
    write_json(out / "report.json", report)
    (out / "report.md").write_text(
        "# GCD leaf synthesis\n\nStatus: **PASSED**\n\n"
        + report["scope"]
        + f"\n\nGeneric cells: {len(cells)}.\n\n"
        + "\n".join("- " + text for text in report["limitations"])
        + "\n"
    )
    return report
