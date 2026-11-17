"""Explicit local MMIO model adapter; metadata never invents peripheral behavior."""

import json
import os
import re
import shutil
from pathlib import Path, PurePosixPath

from .domain import ContractError
from .ipxact import digest
from .project import inside, keys


def validate(models, builtin_ids, builtin_vlnvs):
    if not isinstance(models, dict) or len(models) > 16:
        raise ContractError("external_models must be a mapping of at most 16 model descriptors")
    vlnvs = set(builtin_vlnvs)
    filenames = {n.replace(".", "_") for n in builtin_ids}
    for model, descriptor in models.items():
        if (
            not isinstance(model, str)
            or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", model)
            or model in builtin_ids
            or model in {"cpu", "design"}
            or model.replace(".", "_") in filenames
        ):
            raise ContractError("invalid or colliding external model identity")
        keys(
            descriptor,
            {"vlnv", "executable", "sha256", "license", "protocol", "compatible"},
            "external model",
        )
        if (
            descriptor["protocol"] != "usp-mmio32-v1"
            or not isinstance(descriptor["sha256"], str)
            or not re.fullmatch("[a-f0-9]{64}", descriptor["sha256"])
            or not isinstance(descriptor["vlnv"], str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+(:[A-Za-z0-9_.-]+){3}", descriptor["vlnv"])
            or descriptor["vlnv"] in vlnvs
            or not isinstance(descriptor["compatible"], str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+,[A-Za-z0-9_.-]+", descriptor["compatible"])
            or not isinstance(descriptor["license"], str)
            or not 1 <= len(descriptor["license"]) <= 120
            or not isinstance(descriptor["executable"], str)
            or not descriptor["executable"].startswith("models/")
            or ".." in Path(descriptor["executable"]).parts
            or ".." in PurePosixPath(descriptor["executable"]).parts
            or "\\" in descriptor["executable"]
        ):
            raise ContractError("invalid external model protocol/provenance or executable path")
        vlnvs.add(descriptor["vlnv"])
        filenames.add(model.replace(".", "_"))
    return models


def stage(spec, source_root, output):
    for model, descriptor in spec.get("external_models", {}).items():
        if not any(d["model"] == model for d in spec["devices"]):
            continue
        source = inside(source_root, descriptor["executable"])
        if (
            not source.is_file()
            or not os.access(source, os.X_OK)
            or digest(source) != descriptor["sha256"]
        ):
            raise ContractError("missing, non-executable or modified external model")
        destination = inside(output, descriptor["executable"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if digest(destination) != descriptor["sha256"]:
            raise ContractError("external model changed while staging")


def preflight(spec, source_root):
    for model, descriptor in spec.get("external_models", {}).items():
        if not any(d["model"] == model for d in spec["devices"]):
            continue
        source = inside(source_root, descriptor["executable"])
        if (
            not source.is_file()
            or not os.access(source, os.X_OK)
            or digest(source) != descriptor["sha256"]
        ):
            raise ContractError("missing, non-executable or modified external model")


def descriptor(model):
    return {
        "vlnv": model["vlnv"],
        "id": "external.usp-mmio32-v1",
        "source": {
            "sha256": model["sha256"],
            "license": model["license"],
            "origin": "explicit project-local executable",
        },
    }


def trace_checks(path):
    tx = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return (
        bool(tx)
        and all(t.get("status") == 0 for t in tx)
        and any(t.get("op") in (0, 1) for t in tx)
    )
