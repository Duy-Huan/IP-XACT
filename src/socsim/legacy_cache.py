"""Explicit, input-checked cache transfer after legacy metadata regeneration."""

import json
import shutil
from pathlib import Path

from .domain import ContractError
from .ipxact import digest
from .pipeline import QEMU_SHA, ZEPHYR_SHA, verify_generated, verify_source
from .project import inside
from .runner import write_json


def reuse_qemu(project, out, source, previous):
    out, previous = out.resolve(), previous.resolve()
    verify_generated(project, out)
    verify_source(source, QEMU_SHA)
    record = json.loads((previous / "qemu-build.json").read_text())
    manifest = json.loads((previous / "manifest.json").read_text())
    binary = previous / "qemu-build/qemu-system-riscv64"
    machine = digest(out / "machine.c")
    if (
        out == previous
        or record["source_commit"] != QEMU_SHA
        or manifest["qemu_commit"] != QEMU_SHA
        or record["generated_manifest_sha256"] != digest(previous / "manifest.json")
        or record["generated_machine_sha256"] != machine
        or manifest["files"]["machine.c"] != machine
        or digest(previous / "machine.c") != machine
        or record["binary_sha256"] != digest(binary)
    ):
        raise ContractError("QEMU cache source/C/original manifest/binary identity mismatch")
    destination = out / "qemu-build/qemu-system-riscv64"
    if (out / "qemu-build.json").exists() or destination.exists():
        raise ContractError("preserve existing QEMU build; select a fresh generated directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, destination)
    if digest(destination) != record["binary_sha256"]:
        raise ContractError("QEMU binary changed during cache copy")
    write_json(
        out / "qemu-build.json",
        {
            "generated_manifest_sha256": digest(out / "manifest.json"),
            "binary_sha256": digest(destination),
            "source_commit": QEMU_SHA,
            "generated_machine_sha256": machine,
            "reused_qemu": {
                "original_build_sha256": digest(previous / "qemu-build.json"),
                "original_manifest_sha256": digest(previous / "manifest.json"),
                "policy": "identical QEMU source and generated C; verified original binary; "
                "no original hash updates",
            },
        },
    )
    return destination


def identical_generated_inputs(project, out, previous):
    """Parser identity may change; every materialized software input must match."""
    verify_generated(project, out)
    old = json.loads((previous / "manifest.json").read_text())
    current = json.loads((out / "manifest.json").read_text())
    if out.resolve() == previous.resolve() or old["files"] != current["files"]:
        raise ContractError("software reuse requires byte-identical complete generated inputs")
    for name, sha in old["files"].items():
        if digest(inside(previous, name)) != sha or digest(inside(out, name)) != sha:
            raise ContractError("modified software generation input")


def copy_software_record(project, out, previous, record_name, artifacts):
    out, previous = out.resolve(), previous.resolve()
    identical_generated_inputs(project, out, previous)
    old_path = previous / record_name
    old = json.loads(old_path.read_text())
    if old["generated_manifest_sha256"] != digest(previous / "manifest.json"):
        raise ContractError("software cache has a mixed original generation identity")
    if (out / record_name).exists():
        raise ContractError("preserve existing software build records")
    hashes = {}
    for name, key in artifacts:
        source = inside(previous, name)
        if digest(source) != old[key]:
            raise ContractError("modified original software cache artifact")
        target = inside(out, name)
        if target.exists():
            raise ContractError("preserve existing software cache output")
        hashes[name] = old[key]
    # Validate every original and destination before transferring the first file.
    for name, key in artifacts:
        source, target = inside(previous, name), inside(out, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if digest(target) != old[key]:
            raise ContractError("software cache changed during transfer")
    record = dict(old)
    record["generated_manifest_sha256"] = digest(out / "manifest.json")
    record["reused_software"] = {
        "original_build_sha256": digest(old_path),
        "original_manifest_sha256": digest(previous / "manifest.json"),
        "copied_artifacts": hashes,
        "policy": "all generated inputs byte-identical; pinned sources and original artifact "
        "hashes verified; original records unchanged",
    }
    write_json(out / record_name, record)
    return record


def reuse_zephyr(project, out, source, previous):
    verify_source(source, ZEPHYR_SHA)
    record = json.loads((previous / "zephyr-build.json").read_text())
    if record["source_commit"] != ZEPHYR_SHA:
        raise ContractError("Zephyr cache source pin differs")
    return copy_software_record(
        project,
        out,
        previous,
        "zephyr-build.json",
        (
            ("zephyr-build/zephyr/zephyr.elf", "elf_sha256"),
            ("zephyr-build/zephyr/.config", "config_sha256"),
            ("zephyr-build/zephyr/zephyr.dts", "dts_sha256"),
        ),
    )


def reuse_linux(project, out, linux, opensbi, previous):
    from .linux import LINUX_SHA, OPENSBI_SHA

    verify_source(linux, LINUX_SHA)
    verify_source(opensbi, OPENSBI_SHA)
    record = json.loads((previous / "linux-build.json").read_text())
    if record["linux_source_commit"] != LINUX_SHA or record["opensbi_source_commit"] != OPENSBI_SHA:
        raise ContractError("Linux/OpenSBI cache source pins differ")
    artifacts = (
        ("linux/opensbi/platform/generic/firmware/fw_payload.elf", "elf_sha256"),
        ("linux/kernel/arch/riscv/boot/Image", "image_sha256"),
        ("linux/kernel/.config", "config_sha256"),
        ("linux/soc.dtb", "dtb_sha256"),
        ("linux/init", "init_sha256"),
        ("linux/workload", "workload_sha256"),
    )
    for name in ("init.c", "workload.S"):
        template = (
            Path(__file__).parent
            / "templates"
            / ("linux-init.c.in" if name == "init.c" else "shared.S.in")
        )
        if (previous / "linux" / name).read_bytes() != template.read_bytes():
            raise ContractError("Linux cached userspace sources differ from owned templates")
    result = copy_software_record(project, out, previous, "linux-build.json", artifacts)
    for name in ("init.c", "workload.S"):
        shutil.copy2(previous / "linux" / name, out / "linux" / name)
    return result
