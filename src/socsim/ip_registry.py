"""Versioned, SHA-256-locked IP Plugin Registry for multi-team SoC collaboration (E3).

Enables independent hardware/IP/firmware teams to publish immutable versioned
`native-model-package-1` archives, resolve lockfiles safely with tar-traversal protection,
and verify package integrity offline.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import tarfile
from pathlib import Path
from typing import Any

from .domain import ContractError
from .ipxact import digest
from .native_package import package_capabilities, read_package
from .runner import write_json

CONTRACT = "usp-ip-registry-1"
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")


def _load_index(registry_dir: Path) -> dict[str, Any]:
    index_path = registry_dir / "registry-index.json"
    if not index_path.is_file():
        return {"contract": CONTRACT, "packages": {}}
    data = json.loads(index_path.read_text())
    if data.get("contract") != CONTRACT or not isinstance(data.get("packages"), dict):
        raise ContractError("E_REGISTRY_INDEX corrupted or unsupported registry index")
    return data


def _deterministic_tar_gz(pkg_dir: Path, files: list[str], out_archive: Path) -> str:
    out_archive.parent.mkdir(parents=True, exist_ok=True)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz", compresslevel=6) as tar:
        for rel in sorted(files):
            src = pkg_dir / rel
            data = src.read_bytes()
            info = tarfile.TarInfo(name=rel)
            info.size = len(data)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    out_archive.write_bytes(raw.getvalue())
    return digest(out_archive)


def _safe_extract_tar_gz(archive: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    resolved_dest = dest_dir.resolve()
    with tarfile.open(archive, mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile() or member.issym() or member.islnk():
                raise ContractError(f"E_REGISTRY_ARCHIVE unsupported tar entry: {member.name}")
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise ContractError(f"E_REGISTRY_ARCHIVE path traversal blocked: {member.name}")
            target = (resolved_dest / member.name).resolve()
            try:
                target.relative_to(resolved_dest)
            except ValueError as exc:
                raise ContractError(f"E_REGISTRY_ARCHIVE escape blocked: {member.name}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            if extracted is None:
                raise ContractError(f"E_REGISTRY_ARCHIVE unreadable member: {member.name}")
            target.write_bytes(extracted.read())


def publish_package(package_yaml: Path, registry_dir: Path, *, version: str) -> dict[str, Any]:
    """Validate and immutably publish a pinned native-model-package-1 archive."""
    if not SEMVER_RE.fullmatch(version):
        raise ContractError(f"E_REGISTRY_VERSION invalid semantic version: {version}")
    package_yaml = package_yaml.resolve()
    registry_dir = registry_dir.resolve()
    registry_dir.mkdir(parents=True, exist_ok=True)

    pkg, hashes = read_package(package_yaml)
    pkg_id = str(pkg["id"])
    key = f"{pkg_id}@{version}"
    index = _load_index(registry_dir)
    if key in index["packages"]:
        raise ContractError(f"E_REGISTRY_IMMUTABLE package version already published: {key}")

    archive_rel = f"packages/{pkg_id}-{version}.tar.gz"
    archive_path = registry_dir / archive_rel
    archive_sha = _deterministic_tar_gz(package_yaml.parent, list(hashes.keys()), archive_path)
    entry = {
        "key": key,
        "package_id": pkg_id,
        "version": version,
        "vlnv": pkg["vlnv"],
        "license": pkg["license"]["spdx"],
        "protocol": pkg["interface"]["protocol"],
        "capabilities": package_capabilities(pkg),
        "archive": archive_rel,
        "archive_sha256": archive_sha,
        "input_hashes": hashes,
    }
    index["packages"][key] = entry
    write_json(registry_dir / "registry-index.json", index)
    return entry


def list_registry(registry_dir: Path) -> dict[str, Any]:
    """List all published IP packages and verify archive presence."""
    registry_dir = registry_dir.resolve()
    index = _load_index(registry_dir)
    return {
        "contract": CONTRACT,
        "registry_dir": str(registry_dir),
        "count": len(index["packages"]),
        "packages": index["packages"],
    }


def _parse_semver(ver: str) -> tuple[int, int, int]:
    core = ver.split("-", 1)[0]
    parts = core.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ContractError(f"E_REGISTRY_VERSION invalid semver: {ver}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _matches_constraint(version: str, constraint: str) -> bool:
    cand = _parse_semver(version)
    constraint = constraint.strip()
    if constraint.startswith("^"):
        maj, min_, pat = _parse_semver(constraint[1:])
        return cand >= (maj, min_, pat) and cand < (maj + 1, 0, 0)
    if constraint.startswith("~"):
        maj, min_, pat = _parse_semver(constraint[1:])
        return cand >= (maj, min_, pat) and cand < (maj, min_ + 1, 0)
    if "," in constraint or constraint.startswith((">=", "<=", ">", "<")):
        for clause in constraint.split(","):
            c = clause.strip()
            if c.startswith(">="):
                if not (cand >= _parse_semver(c[2:])):
                    return False
            elif c.startswith("<="):
                if not (cand <= _parse_semver(c[2:])):
                    return False
            elif c.startswith(">"):
                if not (cand > _parse_semver(c[1:])):
                    return False
            elif c.startswith("<"):
                if not (cand < _parse_semver(c[1:])):
                    return False
            elif version != c:
                return False
        return True
    return version == constraint


def resolve_packages(
    registry_dir: Path,
    requirements: dict[str, str],
    dest_dir: Path,
) -> dict[str, Any]:
    """Verify SHA-256 archives, resolve semver/ranges, unpack safely, and emit lockfile."""
    registry_dir = registry_dir.resolve()
    dest_dir = dest_dir.resolve()
    index = _load_index(registry_dir)
    resolved: dict[str, Any] = {}

    for pkg_id, constraint in sorted(requirements.items()):
        candidates = [
            entry
            for entry in index["packages"].values()
            if entry["package_id"] == pkg_id and _matches_constraint(entry["version"], constraint)
        ]
        if not candidates:
            raise ContractError(f"E_REGISTRY_MISSING unresolved IP package: {pkg_id}@{constraint}")
        entry = max(candidates, key=lambda e: _parse_semver(e["version"]))
        key = entry["key"]
        version = entry["version"]
        archive_path = registry_dir / entry["archive"]
        if not archive_path.is_file() or digest(archive_path) != entry["archive_sha256"]:
            raise ContractError(f"E_REGISTRY_TAMPER archive hash mismatch for {key}")
        target_dir = dest_dir / pkg_id.replace(".", "_")
        _safe_extract_tar_gz(archive_path, target_dir)
        _pkg, extracted_hashes = read_package(target_dir / "package.yaml")
        if extracted_hashes != entry["input_hashes"]:
            raise ContractError(f"E_REGISTRY_TAMPER extracted package inputs mismatch for {key}")
        resolved[key] = {
            "package_id": pkg_id,
            "requested": constraint,
            "version": version,
            "vlnv": entry["vlnv"],
            "archive_sha256": entry["archive_sha256"],
            "package_yaml": str(target_dir / "package.yaml"),
        }

    lockfile = {
        "contract": "usp-ip-registry-lock-1",
        "registry_dir": str(registry_dir),
        "resolved": resolved,
    }
    write_json(dest_dir / "ip-registry.lock.json", lockfile)
    return lockfile


def verify_registry(registry_dir: Path) -> dict[str, Any]:
    """Audit every archive hash in a registry directory."""
    registry_dir = registry_dir.resolve()
    index = _load_index(registry_dir)
    checked: dict[str, bool] = {}
    for key, entry in index["packages"].items():
        archive = registry_dir / entry["archive"]
        ok = archive.is_file() and digest(archive) == entry["archive_sha256"]
        checked[key] = ok
    if not all(checked.values()):
        bad = [k for k, v in checked.items() if not v]
        raise ContractError(f"E_REGISTRY_TAMPER corrupted registry archives: {bad}")
    return {"status": "PASSED", "verified_count": len(checked), "packages": checked}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="usp registry")
    commands = parser.add_subparsers(dest="command", required=True)

    pub = commands.add_parser("publish", help="publish a pinned IP package into a registry")
    pub.add_argument("package", type=Path)
    pub.add_argument("--registry", type=Path, required=True)
    pub.add_argument("--version", required=True)

    ls_cmd = commands.add_parser("list", help="list published IP packages in a registry")
    ls_cmd.add_argument("--registry", type=Path, required=True)

    res_cmd = commands.add_parser("resolve", help="resolve id@version packages into a workspace")
    res_cmd.add_argument("--registry", type=Path, required=True)
    res_cmd.add_argument("--out", type=Path, required=True)
    res_cmd.add_argument("--require", action="append", required=True, help="pkg_id@version")

    ver_cmd = commands.add_parser("verify", help="verify SHA-256 integrity of all archives")
    ver_cmd.add_argument("--registry", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        out: dict[str, Any]
        if args.command == "publish":
            out = publish_package(args.package, args.registry, version=args.version)
        elif args.command == "list":
            out = list_registry(args.registry)
        elif args.command == "resolve":
            reqs: dict[str, str] = {}
            for item in args.require:
                if "@" not in item:
                    raise ContractError(f"E_REGISTRY_ARG expected pkg_id@version, got {item}")
                pid, ver = item.split("@", 1)
                reqs[pid] = ver
            out = resolve_packages(args.registry, reqs, args.out)
        else:
            out = verify_registry(args.registry)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}))
        return 2
