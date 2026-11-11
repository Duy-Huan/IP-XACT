"""Strict project input and model resolution; configuration never executes code."""

from dataclasses import dataclass
from pathlib import Path

import yaml

from .domain import ContractError, SoCIR
from .ipxact import digest, elaborate


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ContractError("YAML mapping keys must be unique strings")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def keys(value, required: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != required:
        raise ContractError(f"{label}: expected exactly keys {sorted(required)}")
    return value


def read_yaml(path: Path) -> dict:
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ContractError(f"{path.name}: YAML exceeds 1 MiB limit")
        result = yaml.load(path.read_text(), Loader=UniqueLoader)
        if not isinstance(result, dict):
            raise ContractError(f"{path.name}: expected a mapping")
        return result
    except (OSError, yaml.YAMLError) as exc:
        raise ContractError(f"{path.name}: {exc}") from exc


def inside(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ContractError("expected a project-relative path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ContractError("path escapes project root")
    return path


@dataclass(frozen=True)
class Project:
    root: Path
    ir: SoCIR
    manifest: dict
    models: dict
    lock: dict


def load_project(root: Path) -> Project:
    root = root.resolve()
    manifest_path = root / "simulation.yaml"
    if not manifest_path.resolve().is_relative_to(root):
        raise ContractError("manifest escapes project root")
    manifest = keys(
        read_yaml(manifest_path),
        {
            "schema_version",
            "design",
            "models",
            "toolchain",
            "placements",
            "profile",
        },
        "simulation.yaml",
    )
    if manifest["schema_version"] != "1":
        raise ContractError("unsupported manifest schema_version")
    design_path = inside(root, manifest["design"])
    if not design_path.is_relative_to(root / "ipxact") or design_path.suffix != ".xml":
        raise ContractError("design must be an XML file under ipxact/")
    if not isinstance(manifest["placements"], dict):
        raise ContractError("placements must be a mapping")
    models_path = inside(root, manifest["models"])
    lock_path = inside(root, manifest["toolchain"])
    models = keys(read_yaml(models_path), {"schema_version", "bindings"}, "models.yaml")
    lock = keys(read_yaml(lock_path), {"schema_version", "qemu_version"}, "toolchain.lock.yaml")
    if models["schema_version"] != "1" or lock["schema_version"] != "1":
        raise ContractError("unsupported models/toolchain schema version")
    if not isinstance(models["bindings"], list) or not isinstance(lock["qemu_version"], str):
        raise ContractError("invalid bindings/toolchain types")
    ir = elaborate(root, manifest["placements"], design_path)
    # The IR includes every configuration input by content, never absolute host paths.
    from dataclasses import replace

    ir = replace(
        ir,
        sources=tuple(
            sorted(
                (
                    *ir.sources,
                    *(
                        (p.relative_to(root).as_posix(), digest(p))
                        for p in (manifest_path, models_path, lock_path)
                    ),
                )
            )
        ),
    )
    return Project(root, ir, manifest, models, lock)
