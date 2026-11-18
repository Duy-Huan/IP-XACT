"""Version-matched, annotated source releases with actual local gate evidence."""

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

from .domain import ContractError
from .runner import write_json
from .version import RELEASE_TAG, __version__


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def version_check(root):
    project = tomllib.loads((root / "pyproject.toml").read_text())
    lock = tomllib.loads((root / "uv.lock").read_text())
    package = next(p for p in lock["package"] if p["name"] == "socsim-platform")
    m = re.fullmatch(r"(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?", __version__)
    if m is None:
        raise ContractError("release requires a supported PEP 440 version")
    suffix = {"a": "alpha", "b": "beta", "rc": "rc"}.get(m[2])
    expected = "v" + m[1] + (f"-{suffix}.{m[3]}" if suffix else "")
    if (
        project["project"]["version"] != __version__
        or package["version"] != __version__
        or RELEASE_TAG != expected
    ):
        raise ContractError("package, CLI, lock and release-tag version mismatch")
    notes = root / f"docs/releases/{RELEASE_TAG}.md"
    if not notes.is_file() or f"## {__version__}" not in (root / "CHANGELOG.md").read_text():
        raise ContractError("version-matched changelog/release notes required")
    return notes


def junit_counts(data):
    if len(data) > 8 * 1024**2 or b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ContractError("invalid/oversized JUnit")
    try:
        doc = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ContractError("invalid JUnit XML") from exc
    cases = doc.findall(".//testcase")
    result = {"tests": len(cases), "failures": 0, "errors": 0, "skipped": 0}
    for c in cases:
        for key, tag in (("failures", "failure"), ("errors", "error"), ("skipped", "skipped")):
            result[key] += c.find(tag) is not None
    if not cases or any(result[k] for k in ("failures", "errors", "skipped")):
        raise ContractError("release requires nonempty JUnit with no failures/errors/skips")
    suites = [doc] if doc.tag == "testsuite" else doc.findall("testsuite")
    for key in result:
        if sum(int(s.get(key, "0")) for s in suites) != result[key]:
            raise ContractError("JUnit summary and testcase evidence differ")
    return result


def required_flags(root):
    return sorted(
        set(re.findall(r"(USP_REQUIRE_[A-Z_]+)=1", (root / "scripts/promote.sh").read_text()))
    )


def collected_tests(root):
    env = dict(os.environ, PYTEST_ADDOPTS="")
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-o", "addopts="],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    match = re.search(r"(\d+) tests? collected", run.stdout)
    if match is None:
        raise ContractError("could not verify complete release test inventory")
    return int(match[1])


def receipt(root, candidate, junit):
    version_check(root)
    if git("status", "--porcelain") or git("rev-parse", "HEAD") != git("rev-parse", "main"):
        raise ContractError("release requires clean regression-promoted main tree")
    if git("rev-parse", candidate + "^{tree}") != git("rev-parse", "HEAD^{tree}"):
        raise ContractError("candidate differs from promoted tree")
    flags = required_flags(root)
    if not flags or any(os.environ.get(f) != "1" for f in flags):
        raise ContractError("mandatory native promotion flags are missing")
    data = junit.read_bytes()
    counts = junit_counts(data)
    if counts["tests"] != collected_tests(root):
        raise ContractError("JUnit does not cover the complete candidate test inventory")
    return {
        "contract": "usp-qualified-release-v1",
        "package_version": __version__,
        "release_tag": RELEASE_TAG,
        "candidate_commit": git("rev-parse", candidate),
        "candidate_tree": git("rev-parse", candidate + "^{tree}"),
        "promoted_commit": git("rev-parse", "HEAD"),
        "tests": counts,
        "required_flags": {f: "1" for f in flags},
        "junit_sha256": hashlib.sha256(data).hexdigest(),
        "junit_gzip_base64": base64.b64encode(gzip.compress(data, mtime=0)).decode(),
        "qualification": "complete local promotion; verified caches may be reused explicitly",
        "hosted_native_ci": "unobserved",
        "cold_all_engine_rebuild": False,
    }


def check_tag(root, tag, out, main_ref="origin/main"):
    version_check(root)
    if tag != RELEASE_TAG or git("cat-file", "-t", "refs/tags/" + tag) != "tag":
        raise ContractError("version-matched annotated release tag required")
    raw = git("for-each-ref", "--format=%(contents)", "refs/tags/" + tag)
    if len(raw) > 2 * 1024**2:
        raise ContractError("oversized release annotation")
    r = json.loads(raw)
    if (
        r["contract"] != "usp-qualified-release-v1"
        or r["package_version"] != __version__
        or r["release_tag"] != tag
        or r["promoted_commit"] != git("rev-parse", "HEAD")
        or r["candidate_tree"] != git("rev-parse", "HEAD^{tree}")
        or r["candidate_tree"] != git("rev-parse", r["candidate_commit"] + "^{tree}")
        or r["required_flags"] != {f: "1" for f in required_flags(root)}
    ):
        raise ContractError("release annotation does not qualify this source tree")
    subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", main_ref], check=True)
    compressed = base64.b64decode(r["junit_gzip_base64"], validate=True)
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        data = stream.read(8 * 1024**2 + 1)
    if hashlib.sha256(data).hexdigest() != r["junit_sha256"] or junit_counts(data) != r["tests"]:
        raise ContractError("release JUnit identity/counts differ")
    if r["tests"]["tests"] != collected_tests(root):
        raise ContractError("release evidence omits candidate tests")
    out.mkdir(parents=True, exist_ok=True)
    (out / "local-native-junit.xml").write_bytes(data)
    write_json(out / "qualification.json", {k: v for k, v in r.items() if k != "junit_gzip_base64"})
    return {
        "status": "PASSED",
        "tag": tag,
        "local_tests": r["tests"]["tests"],
        "scope": "source/local qualification; hosted native/cold/OCI acceptance separate",
    }


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m socsim.release")
    sub = p.add_subparsers(dest="action", required=True)
    r = sub.add_parser("receipt")
    r.add_argument("--candidate", required=True)
    r.add_argument("--junit", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("check-tag")
    c.add_argument("tag")
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--main-ref", choices=["main", "origin/main"], default="origin/main")
    args = p.parse_args(argv)
    try:
        root = Path(git("rev-parse", "--show-toplevel"))
        if args.action == "receipt":
            write_json(args.out, receipt(root, args.candidate, args.junit))
            print(json.dumps({"status": "QUALIFIED_LOCAL", "receipt": str(args.out)}))
        else:
            print(json.dumps(check_tag(root, args.tag, args.out, args.main_ref)))
        return 0
    except (ContractError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
