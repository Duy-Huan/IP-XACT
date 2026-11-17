"""Reproduce run steps from the two hosted Debian jobs; no publish/upload steps."""

import argparse
import shlex
from pathlib import Path

import yaml

parser = argparse.ArgumentParser()
parser.add_argument("job", choices=["regression", "custom-zephyr"])
args = parser.parse_args()
workflow = yaml.load(Path(".github/workflows/regression.yml").read_text(), Loader=yaml.BaseLoader)
job = workflow["jobs"][args.job]
print("""#!/bin/bash
set -euo pipefail
mkdir -p /work
tar -xf - -C /work
cd /work
export GITHUB_PATH=/tmp/github-path
: > "$GITHUB_PATH"
collect() {
    rc=$?
    trap - EXIT
    mkdir -p /results/logs
    if [ -d /work/build ]; then
        find /work/build -maxdepth 2 -type f -name '*.xml' -exec cp {} /results/ \\; || true
        find /work/build -path '*/logs/*.log' -type f \\
            -exec cp --parents {} /results/logs/ \\; || true
    fi
    echo "$rc" > /results/exit-code
    exit "$rc"
}
trap collect EXIT
""")
for step in job["steps"]:
    if "run" not in step:
        continue
    if "if" in step:
        raise SystemExit("Conditional run steps require explicit runner context")
    print("printf '%s\\n' " + shlex.quote("CI step: " + step["name"]))
    for key, value in step.get("env", {}).items():
        if "${{" in value:
            raise SystemExit("GitHub expressions require explicit runner context")
        print("export " + key + "=" + shlex.quote(value))
    print(step["run"])
    print('while IFS= read -r directory; do export PATH="$directory:$PATH"; done < "$GITHUB_PATH"')
    print(': > "$GITHUB_PATH"')
