"""Workflow-published Git notes, readable without the GitHub REST API.

These are repository-published reports, not independent API attestations or
claims that the hosted minimal jobs rebuild every native simulator.
"""

import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from .domain import ContractError

REFS = {"regression": "refs/notes/usp-ci", "source-release": "refs/notes/usp-release"}
RESULTS = {"success", "failure", "cancelled", "skipped"}
MAX_NOTE = 512 * 1024


def git(repo, *args, env=None, check=True):
    result = subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, timeout=90
    )
    if check and result.returncode:
        raise ContractError(f"CI evidence Git operation failed ({result.returncode})")
    return result


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def conclusion(jobs):
    if not isinstance(jobs, dict) or not jobs or len(jobs) > 32:
        raise ContractError("invalid hosted job inventory")
    values = []
    for name, result in jobs.items():
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)
            or not isinstance(result, str)
            or result not in RESULTS
        ):
            raise ContractError("invalid hosted job result")
        values.append(result)
    if "failure" in values:
        return "failure"
    if "cancelled" in values:
        return "cancelled"
    if all(value == "success" for value in values):
        return "success"
    return "incomplete"


def validate_record(record):
    required = {
        "kind",
        "repository",
        "run_id",
        "attempt",
        "ref",
        "event",
        "url",
        "created_utc",
        "jobs",
        "conclusion",
        "scope",
        "artifacts",
        "publication",
        "failure_details",
    }
    if (
        not isinstance(record, dict)
        or set(record) != required
        or not isinstance(record["kind"], str)
        or record["kind"] not in REFS
    ):
        raise ContractError("invalid workflow receipt shape")
    if not isinstance(record["repository"], str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", record["repository"]
    ):
        raise ContractError("invalid workflow repository")
    for key in ("run_id", "attempt"):
        if type(record[key]) is not int or not 1 <= record[key] < 2**63:
            raise ContractError("invalid workflow run identity")
    if (
        record["url"]
        != f"https://github.com/{record['repository']}/actions/runs/{record['run_id']}"
    ):
        raise ContractError("workflow run URL differs from its identity")
    if not isinstance(record["ref"], str) or not re.fullmatch(
        r"refs/(?:heads|tags)/[A-Za-z0-9_./-]{1,200}", record["ref"]
    ):
        raise ContractError("invalid source ref")
    if (
        not isinstance(record["event"], str)
        or record["event"] not in {"push", "workflow_dispatch"}
        or record["conclusion"] != conclusion(record["jobs"])
    ):
        raise ContractError("workflow status differs from actual required job outcomes")
    try:
        timestamp = datetime.datetime.fromisoformat(record["created_utc"])
    except (TypeError, ValueError) as exc:
        raise ContractError("invalid workflow timestamp") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != datetime.timedelta(0):
        raise ContractError("workflow timestamp must be UTC")
    if not isinstance(record["scope"], str) or record["scope"] not in {
        "hosted-required-jobs; native full/cold qualification separate",
        "tag source release; native local qualification checked separately",
    }:
        raise ContractError("unknown hosted receipt scope")
    if not isinstance(record["artifacts"], list) or len(record["artifacts"]) > 32:
        raise ContractError("invalid published artifact inventory")
    for artifact in record["artifacts"]:
        if not isinstance(artifact, dict) or set(artifact) != {"name", "sha256", "bytes"}:
            raise ContractError("invalid artifact identity")
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", str(artifact["name"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(artifact["sha256"]))
            or type(artifact["bytes"]) is not int
            or not 0 < artifact["bytes"] < 2**34
        ):
            raise ContractError("invalid artifact hash/size")
    expected = (
        "not-applicable"
        if record["kind"] == "regression"
        else ("workflow-published" if record["conclusion"] == "success" else "unconfirmed")
    )
    if record["publication"] != expected:
        raise ContractError("publication claim differs from job result")
    if (
        record["kind"] == "source-release"
        and record["conclusion"] == "success"
        and not record["artifacts"]
    ):
        raise ContractError("source publication needs actual artifact identities")
    if (
        not isinstance(record["failure_details"], list)
        or len(json.dumps(record["failure_details"])) > 32 * 1024
    ):
        raise ContractError("oversized failure details")
    return record


def read_journal(repo, kind, commit):
    note = git(repo, "notes", "--ref=" + REFS[kind], "list", commit, check=False)
    if note.returncode == 1 or not note.stdout.strip():
        return None
    if note.returncode:
        raise ContractError("cannot read workflow evidence note")
    blob = note.stdout.split()[0]
    if not re.fullmatch("[0-9a-f]{40}", blob):
        raise ContractError("invalid note object")
    size = int(git(repo, "cat-file", "-s", blob).stdout)
    if not 0 < size <= MAX_NOTE:
        raise ContractError("workflow note exceeds size limit")
    data = json.loads(git(repo, "cat-file", "blob", blob).stdout)
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "source_commit", "records"}
        or data["schema_version"] != "hosted-git-receipt-1"
        or data["source_commit"] != commit
    ):
        raise ContractError("workflow evidence belongs to another source commit")
    if not isinstance(data["records"], list) or not 1 <= len(data["records"]) <= 32:
        raise ContractError("invalid workflow receipt history")
    for record in data["records"]:
        validate_record(record)
        if record["kind"] != kind:
            raise ContractError("workflow receipt kind mismatch")
    return data


def fetch_notes(repo, kind, remote, env=None):
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", remote):
        raise ContractError("use a configured Git remote name")
    ref = REFS[kind]
    result = git(repo, "ls-remote", "--exit-code", remote, ref, env=env, check=False)
    if result.returncode == 2:
        return False
    if result.returncode:
        raise ContractError("workflow receipt transport unavailable")
    git(repo, "fetch", "--no-tags", remote, "+" + ref + ":" + ref, env=env)
    return True


def publish_record(repo, kind, commit, record, remote="origin", env=None):
    """Actual Git transport; tests use isolated local bare repositories."""
    validate_record(record)
    if record["kind"] != kind or git(repo, "rev-parse", "HEAD").stdout.strip() != commit:
        raise ContractError("publisher source/kind mismatch")
    for _ in range(4):
        fetch_notes(repo, kind, remote, env)
        journal = read_journal(repo, kind, commit) or {
            "schema_version": "hosted-git-receipt-1",
            "source_commit": commit,
            "records": [],
        }

        def key(r):
            return (r["run_id"], r["attempt"])

        records = [r for r in journal["records"] if key(r) != key(record)]
        records.append(record)
        journal["records"] = sorted(
            records, key=lambda r: (r["created_utc"], r["run_id"], r["attempt"])
        )[-32:]
        payload = json.dumps(journal, sort_keys=True, indent=2) + "\n"
        if len(payload) > MAX_NOTE:
            raise ContractError("workflow receipt history too large")
        author = dict(
            os.environ if env is None else env,
            GIT_AUTHOR_NAME="github-actions[bot]",
            GIT_AUTHOR_EMAIL="41898282+github-actions[bot]@users.noreply.github.com",
            GIT_COMMITTER_NAME="github-actions[bot]",
            GIT_COMMITTER_EMAIL="41898282+github-actions[bot]@users.noreply.github.com",
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as file:
            file.write(payload)
            file.flush()
            git(
                repo,
                "notes",
                "--ref=" + REFS[kind],
                "add",
                "-f",
                "-F",
                file.name,
                commit,
                env=author,
            )
        result = git(repo, "push", remote, REFS[kind], env=env, check=False)
        if result.returncode == 0:
            return
    raise ContractError("workflow receipt publication conflicted repeatedly")


def query(repo, kind, commit="HEAD", remote="origin", fetch=True, repository=None, run_ref=None):
    commit = git(
        repo, "rev-parse", "--verify", "--end-of-options", commit + "^{commit}"
    ).stdout.strip()
    if fetch:
        fetch_notes(repo, kind, remote)
    data = read_journal(repo, kind, commit)
    if data is None:
        return {
            "status": "UNOBSERVED",
            "source": "repository Git notes",
            "source_commit": commit,
            "records": [],
        }
    records = data["records"]
    if repository is None:
        url = git(repo, "remote", "get-url", remote, check=False).stdout.strip()
        match = re.fullmatch(
            r"(?:https://github.com/|git@github.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?",
            url,
        )
        if match:
            repository = match[1]
    if repository is not None:
        records = [r for r in records if r["repository"] == repository]
    if run_ref is not None:
        records = [r for r in records if r["ref"] == run_ref]
    latest = {}
    for r in sorted(records, key=lambda r: (r["created_utc"], r["run_id"], r["attempt"])):
        latest[r["ref"]] = r
    conclusions = [r["conclusion"] for r in latest.values()]
    state = (
        "SUCCESS"
        if conclusions and all(c == "success" for c in conclusions)
        else ("FAILURE" if "failure" in conclusions else "INCOMPLETE")
    )
    return {
        "status": "HOSTED_REPORTED_" + state if latest else "UNOBSERVED",
        "source": "repository Git notes; not independent REST API attestation",
        "source_commit": commit,
        "records": list(latest.values()),
    }


def artifact_inventory(directory):
    result = []
    for p in sorted(directory.iterdir()):
        if (
            p.is_file()
            and not p.is_symlink()
            and (
                p.name.endswith((".whl", ".tar.gz", ".xml"))
                or p.name in {"qualification.json", "SHA256SUMS"}
            )
        ):
            result.append({"name": p.name, "sha256": sha(p), "bytes": p.stat().st_size})
    return result


def failure_details(repository, run_id, jobs):
    failed = {n for n, v in jobs.items() if v in {"failure", "cancelled"}}
    if not failed:
        return []
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repository}/actions/runs/{run_id}/jobs?per_page=100"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        details = []
        for job in json.loads(result.stdout)["jobs"]:
            if job["name"] not in failed:
                continue
            with tempfile.TemporaryFile() as file:
                r = subprocess.run(
                    ["gh", "api", f"repos/{repository}/actions/jobs/{int(job['id'])}/logs"],
                    stdout=file,
                    stderr=subprocess.DEVNULL,
                    timeout=45,
                )
                if r.returncode:
                    continue
                file.seek(0)
                raw = file.read(8 * 1024**2)
            text = raw.decode("utf-8", errors="replace")
            lines = text.splitlines()
            interesting = [
                i
                for i, line in enumerate(lines)
                if re.search(
                    r"error:|FAILED|fatal:|Traceback|CMake Error|No such file|exit code [1-9]",
                    line,
                    re.I,
                )
            ]
            pos = interesting[0] if interesting else max(0, len(lines) - 30)
            excerpt = "\n".join(lines[max(0, pos - 3) : pos + 22])[-5000:]
            excerpt = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", excerpt)
            details.append(
                {
                    "job": job["name"],
                    "log_prefix_sha256": hashlib.sha256(raw).hexdigest(),
                    "excerpt": excerpt,
                }
            )
            if len(details) >= 4:
                break
        return details
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return [
            {
                "status": "UNAVAILABLE",
                "reason": "job log retrieval failed; required job conclusions retained",
            }
        ]


def publish_from_environment(repo, kind):
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise ContractError("hosted publication only runs inside GitHub Actions")
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise ContractError("workflow publication needs its scoped job token")
    commit = os.environ["GITHUB_SHA"]
    needs = json.loads(os.environ["USP_NEEDS"])
    required = (
        {"workflow-lint", "branch-policy", "regression", "custom-zephyr"}
        if kind == "regression"
        else {"source-release"}
    )
    if kind == "regression" and os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch":
        required.add("gem5-se")
    if not required.issubset(needs):
        raise ContractError("missing required hosted job outcomes")
    jobs = {n: needs[n]["result"] for n in sorted(required)}
    artifacts = []
    if kind == "source-release":
        artifacts = json.loads(needs["source-release"].get("outputs", {}).get("artifacts") or "[]")
    state = conclusion(jobs)
    repository = os.environ["GITHUB_REPOSITORY"]
    run_id = int(os.environ["GITHUB_RUN_ID"])
    record = {
        "kind": kind,
        "repository": repository,
        "run_id": run_id,
        "attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
        "ref": os.environ["GITHUB_REF"],
        "event": os.environ["GITHUB_EVENT_NAME"],
        "url": f"https://github.com/{repository}/actions/runs/{run_id}",
        "created_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "jobs": jobs,
        "conclusion": state,
        "scope": "hosted-required-jobs; native full/cold qualification separate"
        if kind == "regression"
        else "tag source release; native local qualification checked separately",
        "artifacts": artifacts,
        "publication": "not-applicable"
        if kind == "regression"
        else ("workflow-published" if state == "success" else "unconfirmed"),
        "failure_details": failure_details(repository, run_id, jobs),
    }
    validate_record(record)
    env = dict(os.environ)
    count = int(env.get("GIT_CONFIG_COUNT", "0"))
    if not 0 <= count < 16:
        raise ContractError("invalid Git transport configuration")
    env["GIT_CONFIG_COUNT"] = str(count + 1)
    env["GIT_CONFIG_KEY_" + str(count)] = "http.https://github.com/.extraheader"
    env["GIT_CONFIG_VALUE_" + str(count)] = (
        "AUTHORIZATION: basic " + base64.b64encode(("x-access-token:" + token).encode()).decode()
    )
    publish_record(repo, kind, commit, record, env=env)
    return {
        "status": "WORKFLOW_RECEIPT_PUBLISHED",
        "kind": kind,
        "conclusion": state,
        "source_commit": commit,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog="usp ci")
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status")
    status.add_argument("--kind", choices=tuple(REFS), default="regression")
    status.add_argument("--commit", default="HEAD")
    status.add_argument("--remote", default="origin")
    status.add_argument("--repository")
    status.add_argument("--no-fetch", action="store_true")
    status.add_argument("--run-ref")
    status.add_argument("--wait-seconds", type=int, default=0)
    pub = commands.add_parser("publish")
    pub.add_argument("--kind", choices=tuple(REFS), required=True)
    inv = commands.add_parser("inventory")
    inv.add_argument("--directory", type=Path, default=Path("dist"))
    args = parser.parse_args(argv)
    try:
        repo = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").stdout.strip())
        if args.command == "publish":
            result = publish_from_environment(repo, args.kind)
        elif args.command == "inventory":
            result = artifact_inventory(args.directory)
            output = os.environ.get("GITHUB_OUTPUT")
            if output:
                with Path(output).open("a") as file:
                    file.write("artifacts=" + json.dumps(result, separators=(",", ":")) + "\n")
        else:
            if not 0 <= args.wait_seconds <= 3600:
                raise ContractError("wait must be 0..3600 seconds")
            deadline = time.monotonic() + args.wait_seconds
            while True:
                result = query(
                    repo,
                    args.kind,
                    args.commit,
                    args.remote,
                    not args.no_fetch,
                    args.repository,
                    args.run_ref,
                )
                if result["status"] != "UNOBSERVED" or time.monotonic() >= deadline:
                    break
                time.sleep(min(20, max(0, deadline - time.monotonic())))
        print(json.dumps(result, indent=2))
        return 0
    except (ContractError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print(
            json.dumps(
                {
                    "status": "ERROR",
                    "reason": "hosted receipt validation/transport failed; no success inferred",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
