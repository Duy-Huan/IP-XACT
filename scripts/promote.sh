#!/usr/bin/env bash
# Local promotion only. Publishing is a separate explicit operation.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ "$(git branch --show-current)" == dev ]] || { echo 'Run on dev' >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo 'Working tree must be clean' >&2; exit 1; }
candidate=$(git rev-parse HEAD)
bash scripts/regression.sh
[[ "$(git rev-parse HEAD)" == "$candidate" && -z "$(git status --porcelain)" ]] || exit 1
if git show-ref --verify --quiet refs/heads/main; then
    git merge-base --is-ancestor main dev || { echo 'Reconcile main into dev first' >&2; exit 1; }
    git switch main
    git merge --no-ff dev -m 'Merge regression-verified dev into main'
else
    # An empty repository has no release ancestor. First promotion uses the tested commit.
    git branch main "$candidate"
fi
git switch dev
git merge --ff-only main
