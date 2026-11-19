"""One persisted native design lifecycle with explicit model and software ownership."""

import argparse
import copy
import json
import math
import re
import shutil
from pathlib import Path

import yaml

from .domain import ContractError
from .execution import execute_build
from .ipxact import digest, integer
from .native_cpu_binding import ROCKET, is_rtl
from .native_ir import read_ir
from .native_package import build_package, read_package, verify_package
from .native_software import build_software, check_run, verify_software
from .project import inside, keys, read_yaml
from .runner import tool_version, write_json
from .soc_native import accept, build, generate, run, verify, verify_generated
from .soc_native_gem5 import verify_engine
from .soc_spec import validate_cpu, validate_spec

CONTRACT = "native-flow-1"
KINDS = {"baremetal", "linux", "freertos", "zephyr", "ucos2", "ucos3", "package-driver"}


def flow_identity():
    root = Path(__file__).parent
    return {
        n: digest(root / n)
        for n in (
            "native_flow.py",
            "native_package.py",
            "native_software.py",
            "native_ucos.py",
            "native_performance.py",
            "native_cpu_binding.py",
        )
    }


def plan(path):
    path = path.resolve()
    root = path.parent
    data = read_yaml(path)
    if not isinstance(data, dict):
        raise ContractError("native flow must be a mapping")
    keys(
        data,
        {"schema_version", "spec", "input_format", "engine", "packages", "software"}
        | ({"performance"} if "performance" in data else set()),
        "native flow",
    )
    if "performance" in data:
        from .native_performance import validate_cost

        keys(data["performance"], {"qemu_cost_model"}, "flow performance")
        validate_cost(data["performance"]["qemu_cost_model"])
        if data["engine"] == "gem5":
            raise ContractError("QEMU analytical costs do not apply to a gem5-only flow")
    if (
        data["schema_version"] != CONTRACT
        or not isinstance(data["engine"], str)
        or data["engine"] not in {"qemu", "gem5", "both"}
    ):
        raise ContractError("unsupported native flow version/engine")
    if not isinstance(data["input_format"], str) or data["input_format"] not in {
        "yaml",
        "ipxact",
        "fabric",
    }:
        raise ContractError("unsupported native flow input format")
    spec_path = inside(root, data["spec"])
    inputs = {path.name: digest(path), data["spec"]: digest(spec_path)}
    if data["input_format"] == "ipxact":
        if data["packages"]:
            raise ContractError(
                "direct IP-XACT packages must be materialized explicitly before intake"
            )
        from .native_ipxact import import_design

        spec, canonical = import_design(spec_path)[:2]
        for name, sha in canonical.sources:
            inputs[(spec_path.parent / name).relative_to(root).as_posix()] = sha
    elif data["input_format"] == "fabric":
        if data["packages"]:
            raise ContractError(
                "fabric source packages require explicit materialized model bindings"
            )
        from .native_fabric import read_source

        spec, _ = read_source(spec_path)
    else:
        spec = read_yaml(spec_path)
    if not isinstance(data["packages"], list) or len(data["packages"]) > 16:
        raise ContractError("invalid native flow package list")
    packages = {}
    for name in data["packages"]:
        package_path = inside(root, name)
        package, hashes = read_package(package_path)
        if package["id"] in packages:
            raise ContractError("duplicate native flow package identity")
        packages[package["id"]] = name
        inputs[name] = digest(package_path)
        for filename in hashes.keys() - {"package.yaml"}:
            inputs[(package_path.parent / filename).relative_to(root).as_posix()] = hashes[filename]
    if not packages:
        spec = validate_spec(copy.deepcopy(spec))
    # Packages do not yet have binaries at plan time. Record deferred hardware
    # validation, never a fabricated executable digest or a claimed runnable machine.
    if not isinstance(spec, dict):
        raise ContractError("flow input must be a design mapping")
    cpu = validate_cpu(copy.deepcopy(spec.get("cpu")))
    rtl = is_rtl(cpu)
    if cpu.get("model") not in {"qemu.rv64", "qemu.max", ROCKET}:
        raise ContractError("requested CPU needs an adapter; ISS substitution is forbidden")
    if (
        not rtl
        and data["engine"] in {"gem5", "both"}
        and cpu.get("gem5_model")
        not in {
            "gem5.RiscvTimingSimpleCPU",
            "gem5.RiscvAtomicSimpleCPU",
        }
    ):
        raise ContractError("explicit qualified gem5 CPU binding required")
    if rtl and "performance" in data:
        raise ContractError(
            "actual RTL uses clock/retirement counters, not QEMU ISS cost estimates"
        )
    harts = integer(cpu.get("harts", 1), "cpu.harts")
    if not 1 <= harts <= 8:
        raise ContractError("native flow requires 1..8 qualified homogeneous RV64 harts")
    if not isinstance(data["software"], list) or not 1 <= len(data["software"]) <= 16:
        raise ContractError("native flow requires 1..16 explicit software workloads")
    names = set()
    for item in data["software"]:
        if not isinstance(item, dict):
            raise ContractError("flow software requires workload mappings")
        keys(
            item,
            {"id", "kind", "timeout"} | ({"package"} if "package" in item else set()),
            "flow software",
        )
        if (
            not isinstance(item["id"], str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", item["id"])
            or item["id"] in names
            or not isinstance(item["kind"], str)
            or item["kind"] not in KINDS
            or type(item["timeout"]) not in (int, float)
            or not math.isfinite(item["timeout"])
            or not 0 < item["timeout"] <= 300
        ):
            raise ContractError("invalid/duplicate software workload or bounded timeout")
        names.add(item["id"])
        if rtl and item["kind"] == "linux":
            raise ContractError(
                "native Rocket Linux needs its owned boot BSP; no implicit ISS port"
            )
        if (
            rtl
            and item["kind"] in {"freertos", "zephyr", "ucos2", "ucos3"}
            and cpu["clock_hz"] != 50000000
        ):
            raise ContractError("native source CPU kernels require the verified 50MHz BSP clock")
        if item["kind"] in {"freertos", "zephyr", "ucos2", "ucos3"} and harts != 1:
            raise ContractError("native FreeRTOS/Zephyr software needs a single-hart BSP")
        if (item["kind"] == "package-driver") != ("package" in item):
            raise ContractError("only package-driver workloads select a package")
        if item.get("package") is not None and item["package"] not in packages:
            raise ContractError("software references an unresolved model package")
    model_instances = spec.get("devices", [])
    if not isinstance(model_instances, list) or any(
        not isinstance(d, dict) for d in model_instances
    ):
        raise ContractError("flow devices require instance mappings")
    return {
        "contract": CONTRACT,
        "status": "PLANNED",
        "configuration": data,
        "flow_adapter": flow_identity(),
        "inputs": dict(sorted(inputs.items())),
        "packages": packages,
        "engine": data["engine"],
        "hart_count": harts,
        "cpu_model": cpu["model"],
        "cpu_contract": cpu,
        "gem5_cpu_model": cpu.get("gem5_model"),
        "ownership": {
            "instructions": "actual-RocketTile-RTL" if rtl else "selected engine ISS",
            "ram": "selected engine",
            "peripherals": "one isolated state-owning process per external/RTL instance",
            "time": "engine-local; functional external MMIO with annotated SystemC time",
        },
        "required_resources": {
            **({"source_rtl_cpu": True} if rtl else {}),
            "qemu_source": data["engine"] in {"qemu", "both"},
            "native_gem5_build": data["engine"] in {"gem5", "both"},
            "source_ip_models": any(
                d.get("model") in {"chipyard.gcd", "opentitan.gpio"} for d in model_instances
            ),
        },
        "steps": [
            "package-build",
            "materialize-bindings",
            "canonical-ir-lowering",
            *(["source-cpu-build-or-verified-reuse"] if rtl else []),
            "selected-engine-build",
            "software-build",
            "execute",
            "verify",
        ],
        "deferred_until_build": [
            "compiled package binary identity",
            "complete canonical hardware validation",
        ],
        "full_master_objective_complete": False,
    }


def flow_engine_verifier(cpu):
    if is_rtl(cpu):
        from .native_rtl_engine import verify

        return verify
    return verify_engine


def firmware_build(generated, cross_compile):
    """Build a real native software bundle for gem5 without compiling QEMU."""
    verify_generated(generated)
    compiler, version = tool_version(cross_compile + "gcc")
    fw = generated / "firmware"
    execute_build(
        [
            compiler,
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
            str(fw / "main.c"),
            "-o",
            str(generated / "firmware.elf"),
        ],
        generated,
        generated / "logs/firmware-build.log",
        120,
    )
    execute_build(
        [
            "dtc",
            "-I",
            "dts",
            "-O",
            "dtb",
            "-o",
            str(generated / "soc.dtb"),
            str(generated / "soc.dts"),
        ],
        generated,
        generated / "logs/dtc.log",
        30,
    )
    write_json(
        generated / "build.json",
        {
            "contract": "native-gem5-software-bundle-1",
            "engine": "gem5",
            "generated_manifest_sha256": digest(generated / "manifest.json"),
            "firmware_compiler": {
                "path": compiler,
                "version": version,
                "binary_sha256": digest(Path(compiler)),
            },
            "files": {n: digest(generated / n) for n in ("firmware.elf", "soc.dtb")},
        },
    )


def package_driver(generated, package_out, out, cross_compile):
    record = verify_package(package_out)
    spec = verify_generated(generated)
    targets = [d for d in spec["devices"] if d["model"] == record["package_id"]]
    if not targets or any(
        d["model"] in spec["external_models"] and d not in targets for d in spec["devices"]
    ):
        raise ContractError(
            "package selftest must cover every external instance; "
            "supply a combined driver otherwise"
        )
    out.mkdir(parents=True, exist_ok=False)
    shutil.copy2(generated / "firmware/soc.h", out / "soc.h")
    (out / "driver_bindings.h").write_text(
        "#include <stdint.h>\n#define USP_DRIVER_COUNT " + str(len(targets)) + "\n"
        "static const uintptr_t usp_driver_bases[] = {"
        + ",".join(hex(d["base"]) + "UL" for d in targets)
        + "};\n"
        "static const unsigned usp_driver_irqs[] = {"
        + ",".join(str(d["irq"]) for d in targets)
        + "};\n"
    )
    source = package_out / record["driver"]["source"]
    shutil.copy2(source, out / "driver.c")
    execute_build(
        [
            cross_compile + "gcc",
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
            str(out),
            "-I",
            str(generated / "firmware"),
            "-T",
            str(generated / "firmware/linker.ld"),
            str(generated / "firmware/start.S"),
            str(out / "driver.c"),
            "-o",
            str(out / "firmware.elf"),
        ],
        out,
        out / "build.log",
        120,
    )
    return out / "firmware.elf"


def build_flow(path, out, options):
    path, out = path.resolve(), out.resolve()
    options = {k: v.resolve() if isinstance(v, Path) else v for k, v in options.items()}
    p = plan(path)
    config, root = p["configuration"], path.parent
    required = p["required_resources"]
    for flag, option in (
        ("qemu_source", "source_qemu"),
        ("native_gem5_build", "gem5_build"),
        ("source_ip_models", "models"),
    ):
        if required[flag] and not options.get(option):
            raise ContractError("flow requires --" + option.replace("_", "-"))
    for item in config["software"]:
        for name in {
            "linux": ("source_linux", "source_opensbi"),
            "freertos": ("source_freertos",),
            "zephyr": ("source_zephyr",),
            "ucos2": ("ucos_upstream",),
            "ucos3": ("ucos_upstream",),
        }.get(item["kind"], ()):
            if not options.get(name):
                raise ContractError("flow software requires --" + name.replace("_", "-"))
    rtl = is_rtl(p["cpu_contract"])
    if (
        rtl
        and not options.get("rtl_cpu_build")
        and not all(
            options.get(k)
            for k in ("cpu_upstream", "cpu_tools", "cpu_java_home", "cpu_scala_cache")
        )
    ):
        raise ContractError(
            "source CPU needs --rtl-cpu-build or all four --cpu-* source tool paths"
        )
    gem5 = (
        flow_engine_verifier(p["cpu_contract"])(options["gem5_build"])
        if required["native_gem5_build"]
        else None
    )
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "plan.json", p)
    materialized = out / "input"
    materialized.mkdir()
    raw = read_yaml(inside(root, config["spec"]))
    package_records = {}
    for model, name in p["packages"].items():
        package_out = out / "packages" / model
        record = build_package(inside(root, name), package_out)
        destination = materialized / "models" / model / "model"
        destination.parent.mkdir(parents=True)
        shutil.copy2(package_out / "model", destination)
        raw.setdefault("external_models", {})[model] = {
            "vlnv": record["vlnv"],
            "executable": destination.relative_to(materialized).as_posix(),
            "sha256": record["binary_sha256"],
            "license": record["license"]["spdx"],
            "protocol": record["interface"]["protocol"],
            "compatible": "socsim,packaged-model",
        }
        package_records[model] = digest(package_out / "package-build.json")
    if config["input_format"] == "ipxact":
        source_spec = inside(root, config["spec"])
        shutil.copytree(source_spec.parent / "ipxact", materialized / "ipxact")
        shutil.copy2(source_spec, materialized / "spec.yaml")
    elif config["input_format"] == "fabric":
        from .native_external import stage
        from .native_fabric import read_source

        source_spec = inside(root, config["spec"])
        stage(read_source(source_spec)[0], source_spec.parent, materialized)
        shutil.copy2(source_spec, materialized / "spec.yaml")
    else:
        validate_spec(copy.deepcopy(raw))
        if p["packages"]:
            (materialized / "spec.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
        else:
            from .native_external import stage

            stage(
                validate_spec(copy.deepcopy(raw)), inside(root, config["spec"]).parent, materialized
            )
            shutil.copy2(inside(root, config["spec"]), materialized / "spec.yaml")
    generated = out / "generated"
    generate(materialized / "spec.yaml", generated, config["engine"], config["input_format"])
    cpu_build = None
    if rtl:
        from .native_cpu_binding import build_cpu, verify_cpu

        cpu_build = options.get("rtl_cpu_build")
        if cpu_build is None:
            cpu_build = out / "cpu-build"
            build_cpu(
                verify_generated(generated),
                cpu_build,
                options["cpu_upstream"],
                options["cpu_tools"],
                options["cpu_java_home"],
                options["cpu_scala_cache"],
                min(options["jobs"], 4),
            )
        verify_cpu(verify_generated(generated), cpu_build)
    if required["qemu_source"]:
        build(
            generated,
            options["source_qemu"],
            options["cross_compile"],
            options["jobs"],
            options.get("reuse_qemu"),
            "performance" in config,
        )
    else:
        firmware_build(generated, options["cross_compile"])
    profile = None
    if "performance" in config:
        from .native_performance import build_counter

        build_counter(options["source_qemu"], out / "counter")
        profile = {
            "counter": "counter",
            "cost": config["performance"]["qemu_cost_model"],
        }
    software = {}
    for item in config["software"]:
        kind, name = item["kind"], item["id"]
        record = {"kind": kind, "timeout": item["timeout"], "firmware": None}
        if kind == "package-driver":
            package = out / "packages" / item["package"]
            elf = package_driver(
                generated, package, out / "software" / name, options["cross_compile"]
            )
            record.update(
                firmware=elf.relative_to(out).as_posix(),
                package=item["package"],
                expected_markers=verify_package(package)["driver"]["expected_markers"],
            )
        elif kind in {"freertos", "zephyr", "ucos2", "ucos3"}:
            directory = out / "software" / name
            sw = build_software(
                generated,
                directory,
                kind,
                options["ucos_upstream"]
                if kind in {"ucos2", "ucos3"}
                else options["source_" + kind],
                options["cross_compile"],
                options["jobs"],
            )
            record["firmware"] = (directory / sw["elf"]).relative_to(out).as_posix()
            record["software_build_sha256"] = digest(directory / "software-build.json")
        elif kind == "linux":
            from .soc_native_linux import build as linux_build

            if not (generated / "linux-build.json").exists():
                linux_build(
                    generated,
                    options["source_linux"],
                    options["source_opensbi"],
                    options["cross_compile_linux"],
                    options["jobs"],
                    options.get("reuse_linux"),
                )
        if record["firmware"]:
            record["elf_sha256"] = digest(out / record["firmware"])
        software[name] = record
    if p != plan(path):
        raise ContractError("flow source inputs changed during build")
    record = {
        "contract": CONTRACT,
        "status": "BUILT",
        "flow_adapter": flow_identity(),
        "plan_sha256": digest(out / "plan.json"),
        "packages": package_records,
        "generated_manifest_sha256": digest(generated / "manifest.json"),
        "native_ir_sha256": digest(generated / "native-ir.json"),
        "software": software,
        "software_order": [s["id"] for s in config["software"]],
        "engine_bindings": {
            "gem5_build": str(options["gem5_build"]) if gem5 else None,
            "gem5_binary_sha256": gem5["binary_sha256"] if gem5 else None,
            "models": str(options["models"]) if options.get("models") else None,
            **(
                {
                    "rtl_cpu_build": str(cpu_build),
                    "rtl_cpu_build_sha256": digest(cpu_build / "rocket-build.json"),
                }
                if cpu_build is not None
                else {}
            ),
        },
        "qemu_profile": profile,
        "auxiliary_files": {
            p.relative_to(out).as_posix(): digest(p)
            for p in (out / "software").rglob("*")
            if p.is_file() and not p.is_symlink()
        },
    }
    write_json(out / "flow-build.json", record)
    verify_build(out)
    return record


def verify_build(out):
    out = out.resolve()
    r = json.loads((out / "flow-build.json").read_text())
    p = json.loads((out / "plan.json").read_text())
    spec = verify_generated(out / "generated")
    if (
        r.get("contract") != CONTRACT
        or r.get("flow_adapter") != flow_identity()
        or r["plan_sha256"] != digest(out / "plan.json")
        or p["flow_adapter"] != flow_identity()
        or r["generated_manifest_sha256"] != digest(out / "generated/manifest.json")
        or r["native_ir_sha256"] != digest(out / "generated/native-ir.json")
    ):
        raise ContractError("stale or modified flow compilation")
    ir = read_ir(out / "generated/native-ir.json")
    if r["software_order"] != [s["id"] for s in p["configuration"]["software"]]:
        raise ContractError("flow software execution order differs from source plan")
    if len(ir.cpus) != p["hart_count"] or spec["cpu"] != p["cpu_contract"]:
        raise ContractError("execution plan CPU ownership differs from canonical IR")
    if set(r["packages"]) != set(p["packages"]) or set(r["software"]) != {
        s["id"] for s in p["configuration"]["software"]
    }:
        raise ContractError("flow build omits declared packages/software")
    native_build = json.loads((out / "generated/build.json").read_text())
    required_files = {"firmware.elf", "soc.dtb"}
    if p["engine"] in {"qemu", "both"}:
        required_files.add("qemu-build/qemu-system-riscv64")
    if (
        native_build["generated_manifest_sha256"] != r["generated_manifest_sha256"]
        or set(native_build["files"]) != required_files
    ):
        raise ContractError("native engine/software bundle differs from flow plan")
    for name, sha in native_build["files"].items():
        if digest(inside(out / "generated", name)) != sha:
            raise ContractError("modified selected native engine/software bundle")
    for model, sha in r["packages"].items():
        package_out = inside(out / "packages", model)
        verify_package(package_out)
        if digest(package_out / "package-build.json") != sha:
            raise ContractError("modified package build identity")
    for name, sha in r["auxiliary_files"].items():
        if digest(inside(out, name)) != sha:
            raise ContractError("modified flow software input/artifact")
    for name, sw in r["software"].items():
        configured = next(s for s in p["configuration"]["software"] if s["id"] == name)
        if sw["kind"] != configured["kind"] or sw["timeout"] != configured["timeout"]:
            raise ContractError("flow workload differs from source plan")
        if sw["kind"] == "package-driver":
            package = verify_package(inside(out / "packages", configured["package"]))
            if (
                sw["package"] != configured["package"]
                or sw["expected_markers"] != package["driver"]["expected_markers"]
            ):
                raise ContractError("flow driver acceptance differs from pinned package")
        if sw["kind"] in {"freertos", "zephyr", "ucos2", "ucos3"}:
            verify_software(out / "generated", inside(out / "software", name))
        if sw["firmware"] and digest(inside(out, sw["firmware"])) != sw["elf_sha256"]:
            raise ContractError("modified flow workload ELF")
    binding = r["engine_bindings"]
    if is_rtl(spec["cpu"]):
        from .native_cpu_binding import verify_cpu

        if not binding.get("rtl_cpu_build"):
            raise ContractError("native source CPU build owner is missing")
        cpu = Path(binding["rtl_cpu_build"])
        verify_cpu(spec, cpu)
        if digest(cpu / "rocket-build.json") != binding["rtl_cpu_build_sha256"]:
            raise ContractError("native flow source CPU build identity changed")
    elif "rtl_cpu_build" in binding:
        raise ContractError("native ISS plan cannot substitute a source CPU")
    if p["required_resources"]["source_ip_models"]:
        from .ipblocks import verify_models

        if not binding.get("models"):
            raise ContractError("missing source IP model owner")
        verify_models(Path(binding["models"]))
    if bool(r.get("qemu_profile")) != ("performance" in p["configuration"]):
        raise ContractError("flow profiling differs from declared source configuration")
    if r.get("qemu_profile"):
        from .native_performance import validate_cost, verify_counter

        validate_cost(r["qemu_profile"]["cost"])
        if r["qemu_profile"]["cost"] != p["configuration"]["performance"]["qemu_cost_model"]:
            raise ContractError("QEMU analytical cost differs from source configuration")
        verify_counter(inside(out, r["qemu_profile"]["counter"]))
    if p["engine"] in {"gem5", "both"}:
        if (
            flow_engine_verifier(spec["cpu"])(Path(binding["gem5_build"]))["binary_sha256"]
            != binding["gem5_binary_sha256"]
        ):
            raise ContractError("modified selected gem5 engine")
    return r, p


def execute_flow(built, out):
    built, out = built.resolve(), out.resolve()
    record, p = verify_build(built)
    out.mkdir(parents=True, exist_ok=False)
    binding = record["engine_bindings"]
    models = Path(binding["models"]) if binding["models"] else None
    gem5 = Path(binding["gem5_build"]) if binding["gem5_build"] else None
    cpu = Path(binding["rtl_cpu_build"]) if binding.get("rtl_cpu_build") else None
    generated = built / "generated"
    profile = record.get("qemu_profile")
    if profile:
        profile = dict(profile, counter=str(inside(built, profile["counter"])))
    for name in record["software_order"]:
        sw = record["software"][name]
        firmware = inside(built, sw["firmware"]) if sw["firmware"] else None
        software = "linux" if sw["kind"] == "linux" else "baremetal"
        dest = out / name
        markers = sw.get("expected_markers")
        if cpu is not None and sw["kind"] in {"freertos", "zephyr", "ucos2", "ucos3"}:
            from .freertos import MARKERS as FREERTOS_MARKERS
            from .native_software import ZEPHYR_MARKERS

            markers = (
                list(FREERTOS_MARKERS)
                if sw["kind"] == "freertos"
                else (
                    list(ZEPHYR_MARKERS)
                    if sw["kind"] == "zephyr"
                    else [
                        "USP_UCOS_BOOT_OK",
                        "USP_UCOS_TIMER_PREEMPTION_REGISTERS_OK",
                        "USP_UCOS_UART_PLIC_IRQ_OK",
                        "USP_TEST_PASS",
                    ]
                )
            )
        if p["engine"] == "both":
            accept(
                generated,
                models,
                gem5,
                dest,
                sw["timeout"],
                software,
                firmware,
                sw["kind"] not in {"freertos", "zephyr", "ucos2", "ucos3"},
                profile,
                rtl_cpu_build=cpu,
                rtl_markers=markers,
            )
        else:
            run(
                generated,
                models,
                dest,
                sw["timeout"],
                firmware,
                p["engine"],
                gem5,
                software,
                profile,
                rtl_cpu_build=cpu,
                rtl_markers=markers,
            )
    checks = collect_checks(generated, record, p, out)
    r = {
        "contract": CONTRACT,
        "status": "PASSED" if checks and all(checks.values()) else "FAILED",
        "checks": checks,
        "build_record_sha256": digest(built / "flow-build.json"),
        "native_ir_sha256": record["native_ir_sha256"],
        "engine": p["engine"],
        "software": record["software"],
        "full_master_objective_complete": False,
        "files": {f.relative_to(out).as_posix(): digest(f) for f in out.rglob("*") if f.is_file()},
    }
    write_json(out / "report.json", r)
    (out / "report.md").write_text(
        f"# Native flow\n\nStatus: {r['status']}\n\n"
        + "\n".join(f"- {key}: {value}" for key, value in checks.items())
        + "\n"
    )
    return r


def collect_checks(generated, record, p, out):
    checks = {}
    for name in record["software_order"]:
        sw = record["software"][name]
        dest = out / name
        result = verify(dest)
        engines = ("qemu", "gem5") if p["engine"] == "both" else (p["engine"],)
        checks[name + ".native_execution"] = result["status"] == "PASSED"
        spec = verify_generated(generated)
        for engine in engines:
            where = dest / engine if len(engines) == 2 else dest
            actual = verify(where)
            checks[name + "." + engine + ".canonical_ir"] = (
                actual["native_ir_sha256"] == record["native_ir_sha256"]
            )
            expected_elf = sw.get("elf_sha256")
            if expected_elf is None:
                expected_elf = digest(
                    generated
                    / (
                        "linux/opensbi/platform/generic/firmware/fw_payload.elf"
                        if sw["kind"] == "linux"
                        else "firmware.elf"
                    )
                )
            checks[name + "." + engine + ".selected_elf"] = (
                actual["firmware_sha256"] == expected_elf
            )
            text = (where / (spec["boot"]["console"] + ".uart.log")).read_text()
            if record.get("qemu_profile") and engine == "qemu":
                from .native_performance import metrics

                checks[name + ".qemu.measured_per_hart_events"] = result[
                    "status"
                ] == "PASSED" and actual["performance"] == metrics(
                    where / "qemu-counters.json", p["hart_count"], record["qemu_profile"]["cost"]
                )
            if sw["kind"] in {"freertos", "zephyr", "ucos2", "ucos3"}:
                checks.update(
                    {
                        name + "." + engine + "." + k: v
                        for k, v in check_run(sw["kind"], text).items()
                    }
                )
            for marker in sw.get("expected_markers", []):
                checks[name + "." + engine + "." + marker] = marker in text.splitlines()
    return checks


def verify_flow(built, out):
    build_record, p = verify_build(built)
    r = json.loads((out / "report.json").read_text())
    if r["build_record_sha256"] != digest(built / "flow-build.json"):
        raise ContractError("flow run/build identities differ")
    for name, sha in r["files"].items():
        if digest(inside(out.resolve(), name)) != sha:
            raise ContractError("modified flow execution evidence")
    if r["checks"] != collect_checks(built / "generated", build_record, p, out):
        raise ContractError("flow checks differ from actual workload evidence")
    if (
        r["status"] != "PASSED"
        or not r["checks"]
        or not all(r["checks"].values())
        or r["engine"] != p["engine"]
    ):
        raise ContractError("native flow acceptance incomplete")
    return r


def main(argv):
    parser = argparse.ArgumentParser(prog="usp flow")
    commands = parser.add_subparsers(dest="command", required=True)
    for cmd in ("plan", "build", "all"):
        sub = commands.add_parser(cmd)
        sub.add_argument("flow", type=Path)
        sub.add_argument("--out", type=Path, required=True)
        if cmd != "plan":
            for flag in (
                "source-qemu",
                "gem5-build",
                "models",
                "reuse-qemu",
                "reuse-linux",
                "source-linux",
                "source-opensbi",
                "source-freertos",
                "source-zephyr",
                "ucos-upstream",
                "rtl-cpu-build",
                "cpu-upstream",
                "cpu-tools",
                "cpu-java-home",
                "cpu-scala-cache",
            ):
                sub.add_argument("--" + flag, type=Path)
            sub.add_argument("--cross-compile", default="riscv64-unknown-elf-")
            sub.add_argument("--cross-compile-linux", default="riscv64-linux-gnu-")
            sub.add_argument("--jobs", type=int, choices=range(1, 9), default=2)
    for cmd in ("run", "verify"):
        sub = commands.add_parser(cmd)
        sub.add_argument("--built", type=Path, required=True)
        sub.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = plan(args.flow)
            if args.out.exists():
                raise ContractError("preserve an existing plan; select a fresh output")
            write_json(args.out, result)
        elif args.command in {"build", "all"}:
            result = build_flow(args.flow, args.out, vars(args))
            if args.command == "all":
                result = execute_flow(args.out, args.out / "runs")
                verify_flow(args.out, args.out / "runs")
        elif args.command == "run":
            result = execute_flow(args.built, args.out)
        else:
            result = verify_flow(args.built.resolve(), args.out.resolve())
        print(
            json.dumps(
                {"status": result["status"], "scope": "native qualified model/software contracts"}
            )
        )
        return 0 if result["status"] in {"PLANNED", "BUILT", "PASSED"} else 1
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}))
        return 2
