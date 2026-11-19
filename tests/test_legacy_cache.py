"""Provenance-preserving software transfer must not bless stale/tampered inputs."""

import json
from pathlib import Path

import pytest

from socsim.domain import ContractError
from socsim.ipxact import digest
from socsim.legacy_cache import copy_software_record
from socsim.pipeline import generate
from socsim.project import load_project

ROOT = Path(__file__).resolve().parents[1]


def inputs(tmp_path):
    project = load_project(ROOT / "examples/rv64-minimal")
    old, new = tmp_path / "old", tmp_path / "new"
    for folder in (old, new):
        generate(project, folder)
    # A historical parser identity may differ, but materialized inputs may not.
    path = old / "manifest.json"
    record = json.loads(path.read_text())
    record["compiler_identity"] = "historical parser fixture"
    path.write_text(json.dumps(record))
    (old / "kernel.elf").write_bytes(b"unit artifact; not an executed kernel")
    (old / "kernel.config").write_text("unit config\n")
    record = {
        "generated_manifest_sha256": digest(path),
        "elf_sha256": digest(old / "kernel.elf"),
        "config_sha256": digest(old / "kernel.config"),
    }
    (old / "software.json").write_text(json.dumps(record))
    return project, old, new


ARTIFACTS = (("kernel.elf", "elf_sha256"), ("kernel.config", "config_sha256"))


def test_software_transfer_preserves_original_and_records_explicit_provenance(tmp_path):
    project, old, new = inputs(tmp_path)
    originals = {p.name: p.read_bytes() for p in old.iterdir() if p.is_file()}
    record = copy_software_record(project, new, old, "software.json", ARTIFACTS)
    assert record["generated_manifest_sha256"] == digest(new / "manifest.json")
    assert record["reused_software"]["original_manifest_sha256"] == digest(old / "manifest.json")
    assert record["reused_software"]["original_build_sha256"] == digest(old / "software.json")
    assert all((old / n).read_bytes() == b for n, b in originals.items())
    assert all((new / n).read_bytes() == (old / n).read_bytes() for n, _ in ARTIFACTS)
    with pytest.raises(ContractError, match="preserve"):
        copy_software_record(project, new, old, "software.json", ARTIFACTS)


@pytest.mark.parametrize("case", ["generation", "manifest", "artifact", "existing", "escape"])
def test_software_transfer_rejects_mixed_or_modified_inputs_before_copy(tmp_path, case):
    project, old, new = inputs(tmp_path)
    artifacts = ARTIFACTS
    if case == "generation":
        (old / "machine.c").write_text("changed generated input")
    elif case == "manifest":
        (old / "manifest.json").write_text((old / "manifest.json").read_text() + "\n")
    elif case == "artifact":
        (old / "kernel.config").write_text("modified config")
    elif case == "existing":
        (new / "kernel.config").write_text("preserve user output")
    else:
        artifacts = (ARTIFACTS[0], ("../outside", "config_sha256"))
    with pytest.raises(ContractError):
        copy_software_record(project, new, old, "software.json", artifacts)
    assert not (new / "kernel.elf").exists()
    assert not (new / "software.json").exists()
