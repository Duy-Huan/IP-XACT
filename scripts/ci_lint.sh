#!/usr/bin/env bash
# Validate the actual workflows and shell syntax before the required native gate.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PATH="$PWD/.tools/ci/bin:$PATH"
command -v actionlint >/dev/null || { echo 'Install actionlint v1.7.9' >&2; exit 1; }
command -v shellcheck >/dev/null || { echo 'Install ShellCheck 0.10.0' >&2; exit 1; }
actionlint -shellcheck "$(command -v shellcheck)"
while IFS= read -r -d '' script; do
    bash -n "$script"
done < <(git ls-files -z '*.sh')
