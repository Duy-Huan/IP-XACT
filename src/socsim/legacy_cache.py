"""Explicit QEMU cache reuse after regenerating a legacy profile's metadata."""

import json
import shutil

from .domain import ContractError
from .ipxact import digest
from .pipeline import QEMU_SHA, verify_generated, verify_source
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
