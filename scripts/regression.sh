#!/usr/bin/env bash
# External tools are required here: absence is a failure of the release gate.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PATH="$PWD/.venv/bin:$PATH"
ruff check src tests scripts/check_docs.py
ruff format --check src tests scripts/check_docs.py
python scripts/check_docs.py
SOCSIM_REQUIRE_INTEGRATION=1 python -m pytest -v --junitxml=build/junit.xml
