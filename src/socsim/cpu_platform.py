"""Explicit CPU provider/engine capabilities and configurable workload selection."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from jsonschema import ValidationError, validate

from .domain import ContractError
from .ipxact import digest
from .project import inside, load_project, read_yaml

ADAPTERS = {
    ("qemu", "rv64", "qemu"): {"adapter": "qemu-tcg-rv64-v1", "qemu_cpu": "rv64"},
    ("qemu", "max", "qemu"): {"adapter": "qemu-tcg-max-v1", "qemu_cpu": "max"},
    ("gem5", "timing-simple", "gem5"): {
        "adapter": "gem5-timing-simple-v1",
        "gem5_cpu": "RiscvTimingSimpleCPU",
        "mem_mode": "timing",
    },
    ("gem5", "atomic-simple", "gem5"): {
        "adapter": "gem5-atomic-simple-v1",
        "gem5_cpu": "RiscvAtomicSimpleCPU",
        "mem_mode": "atomic",
    },
}


def load_platform(path):
    config = read_yaml(path.resolve())
    try:
        json.dumps(config, allow_nan=False)
    except ValueError as exc:
        raise ContractError("non-finite platform configuration") from exc
    schema = json.loads((Path(__file__).parent / "schemas/platform-v1.json").read_text())
    try:
        validate(config, schema)
    except ValidationError as exc:
        raise ContractError(f"E_PLATFORM_SCHEMA {list(exc.absolute_path)}: {exc.message}") from exc
    if config["engine"] != "qemu" and "qemu_cost_model" in config:
        raise ContractError("QEMU analytical costs cannot replace native gem5 counters")
    if (
        config["cpu"]["provider"] not in {"chipyard", "custom-rtl"}
        and "rtl_manifest" in config["cpu"]
    ):
        raise ContractError("RTL manifests require an explicit RTL CPU provider")
    if not set(config.get("artifacts", {})).issubset(config["workloads"]):
        raise ContractError("artifact does not belong to a selected workload")
    for layer, directory in config.get("software_builds", {}).items():
        if layer not in config["workloads"]:
            raise ContractError("software build does not belong to a selected workload")
        config["software_builds"][layer] = str((path.resolve().parent / directory).resolve())
    for artifact in config.get("artifacts", {}).values():
        for field in ("elf", "dtb"):
            if field in artifact:
                candidate = Path(artifact[field])
                artifact[field] = str((path.resolve().parent / candidate).resolve())
    if "rtl_manifest" in config["cpu"]:
        config["cpu"]["rtl_manifest"] = str(
            (path.resolve().parent / config["cpu"]["rtl_manifest"]).resolve()
        )
    return config


def cpu_selection(config, engine, require=True):
    if config["engine"] != engine:
        raise ContractError("CPU configuration and execution engine differ")
    cpu = config["cpu"]
    from .rocket_build import CPU_CONTRACT

    if cpu["provider"] == "chipyard" and cpu["model"] == CPU_CONTRACT["model"]:
        from .rocket_cpu import selection

        return selection(config, engine)
    adapter = ADAPTERS.get((cpu["provider"], cpu["model"], engine))
    if cpu["provider"] in {"chipyard", "custom-rtl"} and "rtl_manifest" in cpu:
        from .rtl_cpu_build import load_manifest

        manifest = load_manifest(Path(cpu["rtl_manifest"]))
        kind = "chipyard-ibex" if cpu["provider"] == "chipyard" else "custom-rtl"
        if manifest["kind"] != kind or manifest["model"] != cpu["model"]:
            raise ContractError("CPU selection does not match actual RTL source manifest")
        workloads = set(config.get("workloads", []))
        kernels = workloads - {"baremetal"}
        if kernels - {"freertos", "ucos2", "ucos3", "zephyr"}:
            raise ContractError("RV32 M-mode RTL OS/MMU port unavailable for selected workload")
        if set(config.get("software_builds", {})) != kernels:
            raise ContractError(
                "each RTL OS requires an explicit source-built software_builds entry"
            )
        if set(config.get("artifacts", {})) - {"baremetal"}:
            raise ContractError(
                "RTL OS uses verified kernel/BSP builds, not unverified external ELF"
            )
        if "qemu_cost_model" in config:
            raise ContractError("RTL CPU uses native RTL counters, not QEMU estimates")
        hz = cpu.get("clock_hz", 50000000)
        if type(hz) is not int or not 100000 <= hz <= 500000000 or 1000000000 % hz:
            raise ContractError("RTL clock requires an integral 2..10000ns period")
        if kernels and hz != 50000000:
            raise ContractError("source-built RTL OS BSP requires its verified 50MHz CPU clock")
        adapter = {
            "adapter": "rtl-obi-rvfi32-" + engine + "-v1",
            "performance": "native-rtl-counters",
            "rtl_manifest": cpu["rtl_manifest"],
        }
    elif config.get("software_builds") or set(config.get("workloads", [])) & {"ucos2", "ucos3"}:
        raise ContractError("these source-built software profiles require an actual RTL CPU")
    result = {
        "engine": engine,
        "provider": cpu["provider"],
        "model": cpu["model"],
        "clock_hz": cpu.get("clock_hz", 50000000 if "rtl_manifest" in cpu else 1000000000),
        "status": "AVAILABLE_ADAPTER" if adapter else "NEEDS_ADAPTER",
        "adapter": None,
        "performance": "native-gem5" if engine == "gem5" else "analytical-qemu",
    }
    if adapter:
        result.update(adapter)
    else:
        result["reason"] = (
            "Requested CPU is not executable on this engine yet. An RTL CPU needs an actual "
            "SystemC bus-master/IRQ/reset/clock adapter; cross-ISS needs an explicit "
            "adapter. CPU substitution is forbidden."
        )
        if require:
            raise ContractError(result["reason"])
    return result


def platform_plan(project, config):
    from .execution_plan import compile_execution_plan
    from .unified import binding_plan

    base = binding_plan(project, config["engine"])
    selected = cpu_selection(config, config["engine"], require=False)
    if selected.get("rocket_profile"):
        from .rocket_cpu import platform_plan as rocket_plan

        return rocket_plan(project, config, selected, base)
    if selected.get("rtl_manifest"):
        from .rtl_cpu import platform_plan as rtl_plan

        return rtl_plan(project, config, selected, base)
    execution = compile_execution_plan(project, config, selected, base["bindings"])
    return {
        "status": selected["status"],
        "cpu": selected,
        "ir_sha256": base["ir_sha256"],
        "board": base["board"],
        "peripheral_bindings": [b for b in base["bindings"] if b["instance"] != "rv64"],
        "workloads": config["workloads"],
        "requested_configuration": config,
        "execution_plan": execution.to_dict(),
    }


def run_platform(project, config, generated, models, bridge, counter, out, rtl_cpu_build=None):
    from .layers import run_layer, software
    from .runner import write_json

    selection = cpu_selection(config, config["engine"])
    if selection.get("rocket_profile"):
        from .rocket_cpu import run_platform as run_rocket

        return run_rocket(project, config, generated, models, bridge, rtl_cpu_build, out)
    if selection.get("rtl_manifest"):
        if set(config["workloads"]) - {"baremetal"}:
            from .rtl_platform import run as run_rtl_software

            return run_rtl_software(project, config, generated, models, bridge, rtl_cpu_build, out)
        from .rtl_cpu import run_platform as run_rtl

        return run_rtl(project, config, generated, models, bridge, rtl_cpu_build, out)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "plan.json", platform_plan(project, config))
    write_json(out / "soc-ir.json", project.ir.to_dict())
    (out / "firmware").mkdir()
    results = {}
    for layer in config["workloads"]:
        override = config.get("artifacts", {}).get(layer)
        elf, _ = software(project, generated.resolve(), layer, override)
        shutil.copy2(elf, out / "firmware" / (layer + ".elf"))
        r = run_layer(
            project,
            generated,
            models,
            config["engine"],
            layer,
            out / layer,
            bridge=bridge,
            counter=counter,
            cost=config.get("qemu_cost_model"),
            cpu=selection,
            artifact=override,
        )
        results[layer] = {
            "status": r["status"],
            "checks": r["checks"],
            "performance": r["performance"],
        }
    passed = all(r["status"] == "PASSED" for r in results.values())
    r = {
        "schema_version": "1",
        "status": "PASSED" if passed else "FAILED",
        "cpu": selection,
        "results": results,
        "scope": "only explicitly selected workloads and CPU adapter",
        "full_architecture_complete": False,
        "files": {
            p.relative_to(out).as_posix(): digest(p) for p in sorted(out.rglob("*")) if p.is_file()
        },
    }
    write_json(out / "report.json", r)
    return r


def verify_platform(out):
    from .performance import gem5_metrics, qemu_metrics

    out = out.resolve()
    r = json.loads((out / "report.json").read_text())
    if r.get("profile") == "rocket-cpu-platform-v1":
        from .rocket_cpu import verify_platform as verify_rocket

        return verify_rocket(out)
    if r.get("profile") == "rtl-software-platform-v1":
        from .rtl_platform import verify as verify_rtl_software

        return verify_rtl_software(out)
    if r.get("profile") == "rtl-cpu-platform-v1":
        from .rtl_cpu import verify_platform as verify_rtl

        return verify_rtl(out)
    if r["schema_version"] != "1" or r["full_architecture_complete"] is not False:
        raise ContractError("invalid CPU platform completion claim")
    required = {"plan.json", "soc-ir.json"}
    if not required.issubset(r["files"]):
        raise ContractError("incomplete CPU platform evidence")
    for name, sha in r["files"].items():
        if digest(inside(out, name)) != sha:
            raise ContractError("modified CPU platform evidence")
    plan = json.loads((out / "plan.json").read_text())
    selection = cpu_selection(plan["requested_configuration"], plan["cpu"]["engine"])
    if (
        selection != r["cpu"]
        or selection != plan["cpu"]
        or set(r["results"]) != set(plan["workloads"])
    ):
        raise ContractError("CPU selection/workload identity mismatch")
    ir_sha = hashlib.sha256(
        json.dumps(json.loads((out / "soc-ir.json").read_text()), sort_keys=True).encode()
    ).hexdigest()
    statuses = []
    for layer, stored in r["results"].items():
        path = out / layer
        row = json.loads((path / "report.json").read_text())
        if (
            row["cpu_selection"] != selection
            or row["ir_sha256"] != ir_sha
            or row["elf_sha256"] != digest(out / "firmware" / (layer + ".elf"))
        ):
            raise ContractError("mixed CPU/software stage identities")
        actual = (
            gem5_metrics(path / "stats.txt")
            if selection["engine"] == "gem5"
            else qemu_metrics(
                json.loads((path / "qemu-counters.json").read_text()),
                row["performance"]["cost_model"],
            )
        )
        if (
            actual != row["performance"]
            or actual != stored["performance"]
            or stored["status"] != row["status"]
            or stored["checks"] != row["checks"]
        ):
            raise ContractError("invalid CPU performance evidence")
        statuses.append(row["status"] == "PASSED" and all(row["checks"].values()))
    if r["status"] != ("PASSED" if all(statuses) else "FAILED"):
        raise ContractError("invalid CPU platform acceptance claim")
    return r


def main(argv):
    parser = argparse.ArgumentParser(prog="usp platform")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify").add_argument("run_dir", type=Path)
    for name in ("plan", "run"):
        p = sub.add_parser(name)
        p.add_argument("project", type=Path)
        p.add_argument("--config", type=Path, required=True)
        if name == "run":
            for key in ("generated", "models", "out", "bridge", "counter", "rtl-cpu-build"):
                p.add_argument(
                    "--" + key, type=Path, required=key in {"generated", "models", "out"}
                )
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            r = verify_platform(args.run_dir)
            print(json.dumps(r, indent=2))
            return 0 if r["status"] == "PASSED" else 1
        config, project = load_platform(args.config), load_project(args.project)
        r = (
            platform_plan(project, config)
            if args.command == "plan"
            else run_platform(
                project,
                config,
                args.generated,
                args.models,
                args.bridge,
                args.counter,
                args.out,
                args.rtl_cpu_build,
            )
        )
        print(json.dumps(r, indent=2))
        return 0 if r["status"] in {"PASSED", "AVAILABLE_ADAPTER"} else 1
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}))
        return 2
