#!/usr/bin/env bash
# Uses installed tools; never changes host packages, Git branches, or upstream trees.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.cache/uv}"
uv sync --frozen --extra dev
"$PWD/.venv/bin/socsim" validate examples/riscv-virt
