#!/usr/bin/env bash
# Standardized Python virtual environment & pinned dependency installer.
# Supports both `uv sync --frozen` (uv.lock) and standard `python3 -m venv` +
# `pip install -r requirements*.txt` workflows.
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/common.sh
source "$script_dir/lib/common.sh"

REPO=$(repo_root)
METHOD="auto"
EXTRA="all"
VENV_DIR="$REPO/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CHECK_ONLY=0
VERIFY=0

usage() {
    cat <<'EOF'
Usage: scripts/setup_venv.sh [OPTIONS]

Create, synchronize, or audit the socsim-platform Python virtual environment.

Options:
  --method auto|uv|pip   Installer backend (default: auto; prefers uv when available)
  --extra PROFILE        Dependency profile: base|dev|models|bringup|all (default: all)
  --venv PATH            Virtual environment path (default: <repo>/.venv)
  --python BIN           Base Python interpreter >= 3.11 (default: python3)
  --check                Non-destructive audit of venv, packages, and requirements*.txt
  --verify               Run `usp validate examples/riscv-virt` after installation
  -h, --help             Show this help message

Examples:
  bash scripts/setup_venv.sh --extra all --verify
  bash scripts/setup_venv.sh --method pip --extra dev --venv .venv
  bash scripts/setup_venv.sh --check
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --method)
            METHOD=${2:?Missing value for --method}
            shift 2
            ;;
        --extra)
            EXTRA=${2:?Missing value for --extra}
            shift 2
            ;;
        --venv)
            VENV_DIR=${2:?Missing value for --venv}
            shift 2
            ;;
        --python)
            PYTHON_BIN=${2:?Missing value for --python}
            shift 2
            ;;
        --check)
            CHECK_ONLY=1
            shift
            ;;
        --verify)
            VERIFY=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "Unknown option: $1 (use --help)"
            ;;
    esac
done

case "$METHOD" in
    auto|uv|pip) ;;
    *) die "Invalid --method '$METHOD' (expected auto|uv|pip)" ;;
esac

case "$EXTRA" in
    base|dev|models|bringup|all) ;;
    *) die "Invalid --extra '$EXTRA' (expected base|dev|models|bringup|all)" ;;
esac

run_check() {
    local failures=0
    log_info "Auditing Python environment for socsim-platform (venv=$VENV_DIR)"
    check_python_min "$PYTHON_BIN" 3 11 || failures=$((failures + 1))

    for req in requirements.txt requirements-dev.txt requirements-models.txt requirements-bringup.txt requirements-all.txt; do
        if [[ -f "$REPO/$req" ]]; then
            log_ok "Pinned file present: $req"
        else
            log_warn "Missing requirements file: $req"
            failures=$((failures + 1))
        fi
    done

    if command -v uv >/dev/null 2>&1; then
        log_ok "uv available: $(uv --version)"
    else
        log_warn "uv not installed (pip + requirements*.txt fallback remains supported)"
    fi

    if [[ -x "$VENV_DIR/bin/python" ]]; then
        check_python_min "$VENV_DIR/bin/python" 3 11 || failures=$((failures + 1))
        if [[ -x "$VENV_DIR/bin/usp" ]]; then
            log_ok "CLI installed: usp ($("$VENV_DIR/bin/usp" --version 2>&1))"
        else
            log_warn "CLI missing in venv: $VENV_DIR/bin/usp"
            failures=$((failures + 1))
        fi
        if "$VENV_DIR/bin/python" -c "import lxml, yaml, jsonschema, elftools, socsim" 2>/dev/null; then
            log_ok "Core Python imports verified (lxml, yaml, jsonschema, elftools, socsim)"
        else
            log_warn "One or more core Python modules missing in $VENV_DIR"
            failures=$((failures + 1))
        fi
    else
        log_warn "Virtual environment not initialized at $VENV_DIR"
        failures=$((failures + 1))
    fi

    if (( failures > 0 )); then
        log_warn "Python venv audit finished with $failures warning(s)/gap(s)."
        return 1
    fi
    log_ok "Python venv audit PASSED."
    return 0
}

if (( CHECK_ONLY == 1 )); then
    run_check
    exit $?
fi

resolved_method="$METHOD"
if [[ "$resolved_method" == "auto" ]]; then
    if command -v uv >/dev/null 2>&1 && [[ "$VENV_DIR" == "$REPO/.venv" ]]; then
        resolved_method="uv"
    else
        resolved_method="pip"
    fi
fi

if [[ "$resolved_method" == "uv" ]]; then
    command -v uv >/dev/null 2>&1 || die "uv requested (--method uv) but not installed"
    export UV_CACHE_DIR="${UV_CACHE_DIR:-$REPO/.cache/uv}"
    export UV_PROJECT_ENVIRONMENT="$VENV_DIR"
    uv_args=(sync --frozen)
    case "$EXTRA" in
        base) ;;
        dev) uv_args+=(--extra dev) ;;
        models) uv_args+=(--extra models) ;;
        bringup) uv_args+=(--extra bringup) ;;
        all) uv_args+=(--all-extras) ;;
    esac
    log_info "Synchronizing virtual environment via uv (${uv_args[*]}) -> $VENV_DIR"
    (cd "$REPO" && uv "${uv_args[@]}")
else
    check_python_min "$PYTHON_BIN" 3 11 || die "Python >= 3.11 is required for pip venv setup"
    log_info "Creating/updating virtual environment via $PYTHON_BIN -m venv -> $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools==80.9.0
    case "$EXTRA" in
        base) req_file="$REPO/requirements.txt" ;;
        dev) req_file="$REPO/requirements-dev.txt" ;;
        models) req_file="$REPO/requirements-models.txt" ;;
        bringup) req_file="$REPO/requirements-bringup.txt" ;;
        all) req_file="$REPO/requirements-all.txt" ;;
    esac
    log_info "Installing pinned dependencies from $(basename "$req_file")"
    "$VENV_DIR/bin/pip" install -r "$req_file"
    "$VENV_DIR/bin/pip" install --no-deps --no-build-isolation -e "$REPO"
fi

log_ok "Virtual environment ready: $VENV_DIR"
log_info "Activate with: source $VENV_DIR/bin/activate"

if (( VERIFY == 1 )); then
    log_info "Verifying CLI on reference project examples/riscv-virt"
    "$VENV_DIR/bin/usp" validate "$REPO/examples/riscv-virt"
fi
