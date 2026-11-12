"""Download only the owner's public reference URLs; preserve observable failures.

PDF content is research data, never executable instructions. This script does
not change network policy or bypass authentication/TLS/publisher access controls.
"""

import argparse
import hashlib
import json
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
HOSTS = {"arxiv.org", "www.mdpi.com", "ieeexplore.ieee.org", "dvcon-proceedings.org", "cds.cern.ch"}


def fetch(ref, record):
    name, url = record["id"], record["download_url"]
    if not re.fullmatch(r"[A-Za-z0-9.-]+", name):
        raise ValueError("unsafe reference identifier")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.username:
        raise ValueError("only the explicitly requested official HTTPS sources are supported")
    target = ref / "papers" / (name + ".pdf")
    if target.exists() and not record.get("sha256"):
        raise ValueError("preserve existing untracked PDF before fetching")
    if record.get("sha256") and target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError("preserve changed PDF before fetching another version")
        return record
    part = target.with_suffix(".part")
    headers = ref / "metadata" / (name + ".headers")
    command = [
        "curl",
        "--location",
        "--max-redirs",
        "3",
        "--proto",
        "=https",
        "--proto-redir",
        "=https",
        "--silent",
        "--show-error",
        "--max-time",
        "45",
        "--max-filesize",
        "33554432",
        "--dump-header",
        str(headers),
        "--output",
        str(part),
        "--write-out",
        "%{http_code}",
        url,
    ]
    response = subprocess.run(command, capture_output=True, text=True, timeout=60)
    data = part.read_bytes() if part.exists() else b""
    observed = {
        "http_status": response.stdout,
        "returncode": response.returncode,
        "error": response.stderr[:500],
        "observed_at": datetime.now(UTC).isoformat(),
    }
    record["last_attempt"] = observed
    if response.returncode == 0 and response.stdout == "200" and data.startswith(b"%PDF"):
        part.replace(target)
        record.update(
            status="DOWNLOADED",
            sha256=hashlib.sha256(data).hexdigest(),
            bytes=len(data),
            retrieved_at=datetime.now(UTC).isoformat(),
        )
        text = ref / "text" / (name + ".txt")
        result = subprocess.run(
            ["pdftotext", "-layout", str(target), str(text)],
            capture_output=True,
            text=True,
            timeout=45,
        )
        record["text_status"] = "EXTRACTED" if result.returncode == 0 else "FAILED"
        # Extraction is not evidence that a person/agent reviewed the paper.
        record["read_status"] = "NOT_READ"
    else:
        record["status"] = (
            "BLOCKED_NETWORK" if "CONNECT tunnel failed" in response.stderr else "FETCH_FAILED"
        )
        if part.exists():
            part.replace(ref / "metadata" / (name + ".response"))
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", action="append", default=[])
    args = parser.parse_args()
    ref = ROOT / "ref"
    manifest = ref / "papers.json"
    inventory = json.loads(manifest.read_text())
    for directory in ("papers", "metadata", "text"):
        (ref / directory).mkdir(exist_ok=True)
    ids = {p["id"] for p in inventory["papers"]}
    if not set(args.id).issubset(ids):
        parser.error("unknown reference ID")
    failed = False
    for record in inventory["papers"]:
        if args.id and record["id"] not in args.id:
            continue
        try:
            fetch(ref, record)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            record["status"] = "FETCH_FAILED"
            record["last_error"] = str(exc)
        print(record["id"], record["status"])
        failed |= record["status"] != "DOWNLOADED"
        manifest.write_text(json.dumps(inventory, indent=2) + "\n")
        if urlparse(record["download_url"]).hostname == "arxiv.org":
            time.sleep(3)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
