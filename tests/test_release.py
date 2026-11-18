"""Reject incomplete release evidence and exercise a real legacy cache migration."""

import os
import shutil
from pathlib import Path

import pytest

from socsim.domain import ContractError
from socsim.legacy_cache import reuse_qemu
from socsim.pipeline import generate, run_custom
from socsim.project import load_project
from socsim.release import junit_counts, version_check
from socsim.version import RELEASE_TAG

ROOT = Path(__file__).resolve().parents[1]


def test_release_version_lock_changelog_and_notes_are_consistent():
    notes = version_check(ROOT)
    assert notes.name == RELEASE_TAG + ".md"


@pytest.mark.parametrize("kind", ["empty", "failure", "error", "skip", "count", "dtd"])
def test_incomplete_junit_cannot_qualify_a_release(kind):
    if kind == "empty":
        data = b'<testsuites><testsuite tests="0"/></testsuites>'
    elif kind == "dtd":
        data = b"<!DOCTYPE testsuites><testsuites/>"
    elif kind == "count":
        data = b'<testsuite tests="2"><testcase name="one"/></testsuite>'
    else:
        tag = {"failure": "failure", "error": "error", "skip": "skipped"}[kind]
        count = {"failure": "failures", "error": "errors", "skip": "skipped"}[kind]
        data = (
            f'<testsuite tests="1" {count}="1"><testcase name="negative">'
            f"<{tag}/></testcase></testsuite>"
        ).encode()
    with pytest.raises(ContractError):
        junit_counts(data)


def test_actual_qemu_cache_migration_boots_and_rejects_changed_binary(tmp_path):
    if os.environ.get("USP_REQUIRE_BRINGUP") != "1":
        pytest.skip("requires actual generated machine, source pin and Zephyr build")
    src = Path(os.environ["USP_GENERATED_DIR"])
    project = load_project(ROOT / "examples/rv64-minimal")
    out = tmp_path / "generated"
    generate(project, out)
    reuse_qemu(project, out, ROOT / ".tools/upstream/qemu", src)
    (out / "zephyr-build/zephyr").mkdir(parents=True)
    shutil.copy2(src / "zephyr-build/zephyr/zephyr.elf", out / "zephyr-build/zephyr/zephyr.elf")
    shutil.copy2(src / "zephyr-build.json", out / "zephyr-build.json")
    report = run_custom(project, out, tmp_path / "run")
    assert report["status"] == "PASSED"
    binary = out / "qemu-build/qemu-system-riscv64"
    with binary.open("r+b") as stream:
        stream.seek(16)
        stream.write(b"corrupt")
    other = tmp_path / "other"
    generate(project, other)
    with pytest.raises(ContractError, match="cache"):
        reuse_qemu(project, other, ROOT / ".tools/upstream/qemu", out)
