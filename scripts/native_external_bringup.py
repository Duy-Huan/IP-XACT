"""Build and execute an original external SystemC model without compiler-specific IP code."""

import argparse
import json
import os
import shlex
import subprocess
from pathlib import Path

import yaml

from socsim.execution import execute_build
from socsim.ipxact import digest
from socsim.runner import tool_version, write_json
from socsim.soc_native import accept, build, generate, verify
from socsim.soc_spec import load_spec

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-qemu", type=Path, required=True)
    parser.add_argument("--gem5-build", type=Path, required=True)
    parser.add_argument(
        "--cross-compile", default=os.environ.get("CROSS_COMPILE", "riscv64-unknown-elf-")
    )
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--reuse-qemu", type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    inputs = out / "input"
    (inputs / "models").mkdir(parents=True)
    binary = inputs / "models/counter-model"
    cc, version = tool_version("c++")
    flags = shlex.split(
        subprocess.check_output(["pkg-config", "--cflags", "--libs", "systemc"], text=True)
    )
    includes = [v for f in flags if f.startswith("-I") for v in ("-isystem", f[2:])]
    flags = [f for f in flags if not f.startswith("-I")]
    source = ROOT / "models/peripherals/tlm-counter.cpp"
    execute_build(
        [
            cc,
            "-std=c++17",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            *includes,
            str(source),
            *flags,
            "-o",
            str(binary),
        ],
        out,
        out / "model-build.log",
        120,
    )
    write_json(
        out / "counter-build.json",
        {
            "source_sha256": digest(source),
            "binary_sha256": digest(binary),
            "compiler": version,
            "systemc": subprocess.check_output(
                ["pkg-config", "--modversion", "systemc"], text=True
            ).strip(),
            "license": "Apache-2.0",
        },
    )
    spec = load_spec(ROOT / "specs/AI_SOC-native.yaml")
    spec["name"] = "AI_SOC_external"
    spec["cpu"]["harts"] = 2
    spec["devices"] = [
        d for d in spec["devices"] if d["model"] not in {"chipyard.gcd", "opentitan.gpio"}
    ]
    for dev in spec["devices"]:
        if dev["name"] == spec["boot"]["ram"]:
            dev["base"] = 0x300000000
    spec["boot"]["entry"] = 0x300000000
    spec["external_models"] = {
        "user.counter": {
            "vlnv": "socsim.example:external:counter:1.0",
            "executable": "models/counter-model",
            "sha256": digest(binary),
            "license": "Apache-2.0",
            "protocol": "usp-mmio32-v1",
            "compatible": "socsim,external-counter",
        }
    }
    spec["devices"].append(
        {
            "name": "custom_counter",
            "model": "user.counter",
            "base": 0x190000000,
            "size": 4096,
            "irq": 20,
        }
    )
    source_spec = inputs / "spec.yaml"
    source_spec.write_text(yaml.safe_dump(spec, sort_keys=False))
    generated = out / "generated"
    generate(source_spec, generated, "both")
    build(generated, args.source_qemu, args.cross_compile, args.jobs, args.reuse_qemu)
    elf = out / "counter.elf"
    fw = generated / "firmware"
    execute_build(
        [
            args.cross_compile + "gcc",
            "-march=rv64imac_zicsr_zifencei",
            "-mabi=lp64",
            "-mcmodel=medany",
            "-mno-relax",
            "-msmall-data-limit=0",
            "-O2",
            "-nostdlib",
            "-nostartfiles",
            "-ffreestanding",
            "-Wall",
            "-Werror",
            "-I",
            str(fw),
            "-T",
            str(fw / "linker.ld"),
            str(fw / "start.S"),
            str(ROOT / "models/peripherals/counter-firmware.c"),
            "-o",
            str(elf),
        ],
        out,
        out / "firmware-build.log",
        120,
    )
    result = accept(generated, None, args.gem5_build.resolve(), out / "run", 60, firmware=elf)
    verify(out / "run")
    print(
        json.dumps(
            {"status": result["status"], "generated": str(generated), "firmware": str(elf)},
            indent=2,
        )
    )
    return int(result["status"] != "PASSED")


if __name__ == "__main__":
    raise SystemExit(main())
