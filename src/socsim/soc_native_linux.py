"""Native-spec Linux software adapter with independent, verified build identity."""

import json
import shutil
from pathlib import Path

from .domain import ContractError
from .execution import execute_build
from .ipxact import digest
from .linux import LINUX_SHA, OPENSBI_SHA, build_payload
from .pipeline import verify_source
from .project import inside
from .runner import write_json
from .soc_spec import load_spec, selected

MARKERS = ("OpenSBI", "Linux version", "USP_LINUX_USERSPACE_OK", "Power down")
FILES = (
    "linux/opensbi/platform/generic/firmware/fw_payload.elf",
    "linux/soc.dtb",
    "linux/kernel/arch/riscv/boot/Image",
    "linux/kernel/.config",
    "linux/init",
    "linux/workload",
)


def software_identity():
    root = Path(__file__).parent
    return {
        n: digest(root / n)
        for n in (
            "soc_native_linux.py",
            "linux.py",
            "templates/linux-init.c.in",
            "templates/shared.S.in",
        )
    }


def build(generated, linux, opensbi, cross_compile, jobs=2, reuse_linux=None):
    from .soc_native import verify_generated

    generated = generated.resolve()
    reuse_linux = reuse_linux.resolve() if reuse_linux is not None else None
    spec = verify_generated(generated)
    identity = software_identity()
    ram = selected(spec, "ram")
    if ram["size"] < 64 * 1024**2 or spec["boot"]["entry"] != ram["base"] or ram["base"] % 0x200000:
        raise ContractError(
            "native Linux requires >=64 MiB boot RAM and entry at its 2 MiB aligned base"
        )
    if not 1 <= jobs <= 8:
        raise ContractError("native Linux jobs must be 1..8")
    if (generated / "linux").exists():
        raise ContractError(
            "Linux output already exists; preserve it and use a fresh generated design"
        )
    reused = None
    if reuse_linux is None:
        build_payload(
            generated,
            linux,
            opensbi,
            cross_compile,
            jobs,
            spec["boot"]["entry"],
            generated / "soc.dts",
            smp=spec["cpu"]["harts"] > 1,
        )
    else:
        verify_source(linux, LINUX_SHA)
        verify_source(opensbi, OPENSBI_SHA)
        old = json.loads((reuse_linux / "linux-build.json").read_text())
        compiled_inputs = {
            k: v for k, v in software_identity().items() if k != "soc_native_linux.py"
        }
        if (
            old.get("contract") != "native-linux-v1"
            or load_spec(reuse_linux / "spec.yaml") != spec
            or old["generated_manifest_sha256"] != digest(reuse_linux / "manifest.json")
            or old["linux_source_commit"] != LINUX_SHA
            or old["opensbi_source_commit"] != OPENSBI_SHA
            or any(old["software_identity"].get(k) != v for k, v in compiled_inputs.items())
            or set(old.get("files", {})) != set(FILES)
        ):
            raise ContractError("Linux cache does not match spec/kernel/compiler inputs")
        for filename, sha in old["files"].items():
            source = inside(reuse_linux, filename)
            if digest(source) != sha:
                raise ContractError("modified native Linux cache: " + filename)
        for filename in old["files"]:
            destination = inside(generated, filename)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(inside(reuse_linux, filename), destination)
        execute_build(
            [
                "dtc",
                "-I",
                "dts",
                "-O",
                "dtb",
                "-o",
                str(generated / "linux/soc.dtb"),
                str(generated / "soc.dts"),
            ],
            generated,
            generated / "logs/linux-dtc.log",
            30,
        )
        write_json(generated / "linux-build.json", old)
        reused = {
            "build_record_sha256": digest(reuse_linux / "linux-build.json"),
            "compiled_inputs": compiled_inputs,
            "policy": "identical spec and compiled inputs; regenerate DTB from current frontend",
        }
    record = json.loads((generated / "linux-build.json").read_text())
    record["contract"] = "native-linux-v1"
    if identity != software_identity():
        raise ContractError("native Linux adapter changed during build")
    record["software_identity"] = identity
    record["hart_count"] = spec["cpu"]["harts"]
    record["generated_manifest_sha256"] = digest(generated / "manifest.json")
    record["reused_kernel"] = reused
    record["dtb_sha256"] = digest(generated / "linux/soc.dtb")
    record["files"] = {n: digest(generated / n) for n in FILES}
    verify_generated(generated)
    write_json(generated / "linux-build.json", record)
    return {"status": "BUILT", "software": "linux", "harts": spec["cpu"]["harts"]}


def verify(generated):
    generated = generated.resolve()
    record = json.loads((generated / "linux-build.json").read_text())
    if (
        record.get("contract") != "native-linux-v1"
        or record.get("software_identity") != software_identity()
        or record["generated_manifest_sha256"] != digest(generated / "manifest.json")
        or set(record.get("files", {})) != set(FILES)
    ):
        raise ContractError("stale native Linux software build")
    for filename, sha in record["files"].items():
        if digest(inside(generated, filename)) != sha:
            raise ContractError("modified native Linux artifact: " + filename)
    return record


def checks(text, harts=1):
    return (
        {marker: marker in text for marker in MARKERS}
        | {"no_userspace_failure": "USP_LINUX_USERSPACE_FAIL" not in text}
        | (
            {"all_linux_harts_online": f"smp: Brought up 1 node, {harts} CPUs" in text}
            if harts > 1
            else {}
        )
    )
