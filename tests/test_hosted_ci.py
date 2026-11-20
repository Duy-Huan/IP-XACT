"""Synthetic isolated Git repositories test transport, never real hosted results."""

import hashlib
import json
import subprocess

import pytest

from socsim.domain import ContractError
from socsim.hosted_ci import (
    REFS,
    artifact_inventory,
    conclusion,
    git,
    publish_from_environment,
    publish_record,
    query,
    validate_record,
)


@pytest.fixture
def repositories(tmp_path):
    publisher, remote, reader = (tmp_path / n for n in ("publisher", "remote.git", "reader"))
    subprocess.run(["git", "init", "-b", "dev", str(publisher)], check=True, capture_output=True)
    git(publisher, "config", "user.name", "Synthetic unit fixture")
    git(publisher, "config", "user.email", "fixture@example.invalid")
    (publisher / "README").write_text("Original synthetic Git transport fixture\n")
    git(publisher, "add", "README")
    git(publisher, "commit", "-m", "Synthetic fixture")
    git(publisher, "branch", "main")
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(publisher, "remote", "add", "origin", str(remote))
    git(publisher, "push", "--all", "origin")
    subprocess.run(
        ["git", "clone", "--branch", "dev", str(remote), str(reader)],
        check=True,
        capture_output=True,
    )
    return publisher, reader, git(publisher, "rev-parse", "HEAD").stdout.strip()


def record(ref="refs/heads/main", run_id=101, state="success"):
    return {
        "kind": "regression",
        "repository": "example/unit-fixture",
        "run_id": run_id,
        "attempt": 1,
        "ref": ref,
        "event": "push",
        "url": f"https://github.com/example/unit-fixture/actions/runs/{run_id}",
        "created_utc": "2026-11-18T00:00:00+00:00",
        "jobs": {"regression": state, "custom-zephyr": "success"},
        "conclusion": state,
        "scope": "hosted-required-jobs; native full/cold qualification separate",
        "artifacts": [],
        "publication": "not-applicable",
        "failure_details": [],
    }


def test_missing_real_receipt_remains_unobserved(repositories):
    _, reader, commit = repositories
    assert query(reader, "regression", commit)["status"] == "UNOBSERVED"


def test_actual_git_note_round_trip_preserves_two_source_branches(repositories):
    publisher, reader, commit = repositories
    publish_record(publisher, "regression", commit, record())
    observed = query(reader, "regression", commit, run_ref="refs/heads/main")
    assert observed["status"] == "HOSTED_REPORTED_SUCCESS"
    assert observed["source_commit"] == commit
    assert observed["records"] == [record()]
    assert git(reader, "ls-remote", "--heads", "origin").stdout.count("\n") == 2
    assert query(reader, "regression", commit, run_ref="refs/heads/dev")["status"] == "UNOBSERVED"
    assert (
        query(reader, "regression", commit, repository="other/repository")["status"] == "UNOBSERVED"
    )


def test_failed_run_cannot_be_hidden_by_other_branch_or_earlier_success(repositories):
    publisher, reader, commit = repositories
    publish_record(publisher, "regression", commit, record())
    publish_record(publisher, "regression", commit, record(run_id=102, state="failure"))
    publish_record(publisher, "regression", commit, record(ref="refs/heads/dev", run_id=103))
    assert (
        query(reader, "regression", commit, run_ref="refs/heads/main")["status"]
        == "HOSTED_REPORTED_FAILURE"
    )
    assert (
        query(reader, "regression", commit, run_ref="refs/heads/dev")["status"]
        == "HOSTED_REPORTED_SUCCESS"
    )
    assert query(reader, "regression", commit)["status"] == "HOSTED_REPORTED_FAILURE"


@pytest.mark.parametrize("case", ["status", "url", "artifact", "publication", "timestamp", "extra"])
def test_invalid_or_fabricated_receipt_claim_is_rejected(case):
    data = record()
    if case == "status":
        data["jobs"]["regression"] = "failure"
    elif case == "url":
        data["url"] = "https://example.invalid/fake"
    elif case == "artifact":
        data["artifacts"] = [{"name": "x.whl", "sha256": "not-a-hash", "bytes": 5}]
    elif case == "publication":
        data["publication"] = "workflow-published"
    elif case == "timestamp":
        data["created_utc"] = "2026-11-18T00:00:00"
    else:
        data["ignored"] = True
    with pytest.raises(ContractError):
        validate_record(data)


def test_source_commit_binding_cannot_be_rewritten(repositories, tmp_path):
    publisher, reader, commit = repositories
    publish_record(publisher, "regression", commit, record())
    journal = json.loads(
        git(publisher, "notes", "--ref=" + REFS["regression"], "show", commit).stdout
    )
    journal["source_commit"] = "0" * 40
    file = tmp_path / "bad.json"
    file.write_text(json.dumps(journal))
    git(publisher, "notes", "--ref=" + REFS["regression"], "add", "-f", "-F", str(file), commit)
    git(publisher, "push", "origin", REFS["regression"])
    with pytest.raises(ContractError, match="another source commit"):
        query(reader, "regression", commit)


def test_source_release_success_requires_real_artifact_identities():
    data = record()
    data.update(
        kind="source-release",
        publication="workflow-published",
        scope="tag source release; native local qualification checked separately",
    )
    with pytest.raises(ContractError, match="artifact identities"):
        validate_record(data)
    data["artifacts"] = [{"name": "synthetic-fixture.whl", "sha256": "a" * 64, "bytes": 1}]
    assert validate_record(data)["publication"] == "workflow-published"
    data["jobs"]["regression"] = "failure"
    data["conclusion"] = "failure"
    with pytest.raises(ContractError, match="publication claim"):
        validate_record(data)


def test_inventory_hashes_only_selected_actual_files(tmp_path):
    (tmp_path / "unit.whl").write_bytes(b"unit fixture bytes, not a published wheel")
    (tmp_path / "private-input.txt").write_text("not selected for publication")
    (tmp_path / "symlink.whl").symlink_to(tmp_path / "unit.whl")
    assert artifact_inventory(tmp_path) == [
        {
            "name": "unit.whl",
            "sha256": hashlib.sha256((tmp_path / "unit.whl").read_bytes()).hexdigest(),
            "bytes": len((tmp_path / "unit.whl").read_bytes()),
        }
    ]


def test_cli_publisher_refuses_non_workflow_context(repositories, monkeypatch):
    publisher, _, _ = repositories
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(ContractError, match="inside GitHub Actions"):
        publish_from_environment(publisher, "regression")


def test_local_workflow_context_never_persists_credentials(repositories, monkeypatch):
    publisher, reader, commit = repositories
    config = (publisher / ".git/config").read_bytes()
    env = {
        "GITHUB_ACTIONS": "true",
        "GH_TOKEN": "synthetic-unit-token-not-a-credential",
        "GITHUB_SHA": commit,
        "GITHUB_REPOSITORY": "example/unit-fixture",
        "GITHUB_RUN_ID": "400",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "push",
        "USP_NEEDS": json.dumps(
            {
                n: {"result": "success"}
                for n in ("workflow-lint", "branch-policy", "regression", "custom-zephyr")
            }
        ),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    result = publish_from_environment(publisher, "regression")
    assert result["status"] == "WORKFLOW_RECEIPT_PUBLISHED"
    assert (publisher / ".git/config").read_bytes() == config
    assert query(reader, "regression", commit)["records"][0]["jobs"] == {
        n: "success" for n in ("workflow-lint", "branch-policy", "regression", "custom-zephyr")
    }


def test_skipped_required_job_is_never_success():
    assert conclusion({"required": "skipped"}) == "incomplete"
    assert conclusion({"required": "cancelled"}) == "cancelled"
    with pytest.raises(ContractError):
        conclusion({"required": "invented"})


def test_container_workflow_jobs_configure_git_safe_directory():
    import sys
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[1]
    expected = 'git config --global --add safe.directory "${GITHUB_WORKSPACE:-$PWD}"'
    for name in ("regression.yml", "application-release.yml"):
        workflow = yaml.safe_load((root / ".github/workflows" / name).read_text())
        for job_name, job in workflow["jobs"].items():
            if "container" not in job:
                continue
            steps = job["steps"]
            checkout_index = next(
                i
                for i, step in enumerate(steps)
                if str(step.get("uses", "")).startswith("actions/checkout@")
            )
            post_runs = [step["run"] for step in steps[checkout_index + 1 :] if "run" in step]
            assert post_runs, f"{name}:{job_name} missing post-checkout run step"
            assert expected in post_runs[0], (
                f"{name}:{job_name} must configure git safe.directory right after checkout"
            )
    for job in ("regression", "custom-zephyr"):
        emitted = subprocess.run(
            [sys.executable, str(root / "scripts/emit_ci_job.py"), job],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        assert expected in emitted.stdout
        subprocess.run(["bash", "-n"], input=emitted.stdout, text=True, check=True)
