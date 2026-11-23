#!/usr/bin/env bash
# Unified environment & multi-platform setup orchestrator for socsim-platform.
# Coordinates Python venv + QEMU + gem5 + Chipyard + OpenTitan setup scripts.
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/common.sh
source "$script_dir/lib/common.sh"

REPO=$(repo_root)
MODE="check"
VENV_DIR="$REPO/.venv"
METHOD="auto"
EXTRA="all"
JOBS=$(default_jobs)

SEL_VENV=0
SEL_QEMU=0
SEL_GEM5=0
SEL_CHIPYARD=0
SEL_OPENTITAN=0

usage() {
    cat <<'EOF'
Usage: scripts/setup_all.sh [ACTION] [COMPONENTS...] [OPTIONS]

Unified orchestrator for Python venv and pinned multi-platform toolchains
(QEMU v10.0.0, gem5 v24.1.0.3, Chipyard 1.13.0, OpenTitan earlgrey_silver_release_v5).

Actions (choose one; default: --check):
  --check              Non-destructive readiness audit of selected components (default)
  --fetch-only         Initialize venv + clone/verify pinned upstream repos (no heavy C++ compile)
  --build              Initialize venv + fetch + compile QEMU/gem5 + fetch Chipyard sbt/firtool

Components (if none specified, defaults to --all):
  --all                Select venv, qemu, gem5, chipyard, and opentitan
  --venv               Select Python virtual environment (scripts/setup_venv.sh)
  --qemu               Select pinned QEMU v10.0.0 (scripts/setup_qemu.sh)
  --gem5               Select pinned gem5 v24.1.0.3 (scripts/setup_gem5.sh)
  --chipyard           Select pinned Chipyard 1.13.0 + sbt/firtool (scripts/setup_chipyard.sh)
  --opentitan          Select pinned OpenTitan RTL + Verilator/SystemC (scripts/setup_opentitan.sh)

Options:
  --venv-path PATH     Virtual environment path (default: <repo>/.venv)
  --method auto|uv|pip Python installer backend for setup_venv.sh (default: auto)
  --extra PROFILE      Python dependency profile (default: all)
  --jobs N             Parallel jobs for native builds (default: nproc or $USP_JOBS)
  -h, --help           Show this help message

Examples:
  bash scripts/setup_all.sh --check
  bash scripts/setup_all.sh --venv --fetch-only
  bash scripts/setup_all.sh --all --fetch-only
  bash scripts/setup_all.sh --qemu --gem5 --build --jobs 4
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check) MODE="check"; shift ;;
        --fetch-only) MODE="fetch"; shift ;;
        --build) MODE="build"; shift ;;
        --all)
            SEL_VENV=1; SEL_QEMU=1; SEL_GEM5=1; SEL_CHIPYARD=1; SEL_OPENTITAN=1
            shift
            ;;
        --venv) SEL_VENV=1; shift ;;
        --qemu) SEL_QEMU=1; shift ;;
        --gem5) SEL_GEM5=1; shift ;;
        --chipyard) SEL_CHIPYARD=1; shift ;;
        --opentitan) SEL_OPENTITAN=1; shift ;;
        --venv-path) VENV_DIR=${2:?Missing value for --venv-path}; shift 2 ;;
        --method) METHOD=${2:?Missing value for --method}; shift 2 ;;
        --extra) EXTRA=${2:?Missing value for --extra}; shift 2 ;;
        --jobs) JOBS=${2:?Missing value for --jobs}; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1 (use --help)" ;;
    esac
done

if (( SEL_VENV + SEL_QEMU + SEL_GEM5 + SEL_CHIPYARD + SEL_OPENTITAN == 0 )); then
    SEL_VENV=1; SEL_QEMU=1; SEL_GEM5=1; SEL_CHIPYARD=1; SEL_OPENTITAN=1
fi

declare -A STATUS=()
overall_rc=0

run_step() {
    local label=$1
    shift
    printf '\n=== [%s] ===\n' "$label"
    if "$@"; then
        STATUS["$label"]="READY"
    else
        STATUS["$label"]="NEEDS_SETUP"
        overall_rc=1
    fi
}

if (( SEL_VENV == 1 )); then
    if [[ "$MODE" == "check" ]]; then
        run_step "python-venv" bash "$script_dir/setup_venv.sh" --check --venv "$VENV_DIR"
    else
        run_step "python-venv" bash "$script_dir/setup_venv.sh" --method "$METHOD" --extra "$EXTRA" --venv "$VENV_DIR" --verify
    fi
fi

if (( SEL_QEMU == 1 )); then
    case "$MODE" in
        check) run_step "qemu-v10.0.0" bash "$script_dir/setup_qemu.sh" --check ;;
        fetch) run_step "qemu-v10.0.0" bash "$script_dir/setup_qemu.sh" --fetch-only ;;
        build) run_step "qemu-v10.0.0" bash "$script_dir/setup_qemu.sh" --build --jobs "$JOBS" ;;
    esac
fi

if (( SEL_GEM5 == 1 )); then
    case "$MODE" in
        check) run_step "gem5-v24.1.0.3" bash "$script_dir/setup_gem5.sh" --check ;;
        fetch) run_step "gem5-v24.1.0.3" bash "$script_dir/setup_gem5.sh" --fetch-only ;;
        build) run_step "gem5-v24.1.0.3" bash "$script_dir/setup_gem5.sh" --build --jobs "$JOBS" ;;
    esac
fi

if (( SEL_CHIPYARD == 1 )); then
    case "$MODE" in
        check) run_step "chipyard-1.13.0" bash "$script_dir/setup_chipyard.sh" --check ;;
        fetch) run_step "chipyard-1.13.0" bash "$script_dir/setup_chipyard.sh" --fetch-only ;;
        build) run_step "chipyard-1.13.0" bash "$script_dir/setup_chipyard.sh" --fetch-only --tools ;;
    esac
fi

if (( SEL_OPENTITAN == 1 )); then
    case "$MODE" in
        check) run_step "opentitan-earlgrey" bash "$script_dir/setup_opentitan.sh" --check ;;
        fetch|build) run_step "opentitan-earlgrey" bash "$script_dir/setup_opentitan.sh" --fetch-only --verify-rtl ;;
    esac
fi

printf '\n=== Environment Summary (mode=%s) ===\n' "$MODE"
for key in python-venv qemu-v10.0.0 gem5-v24.1.0.3 chipyard-1.13.0 opentitan-earlgrey; do
    if [[ -n "${STATUS[$key]+x}" ]]; then
        printf '  %-20s : %s\n' "$key" "${STATUS[$key]}"
    fi
done

if (( overall_rc != 0 )) && [[ "$MODE" == "check" ]]; then
    log_info "Tip: Run 'bash scripts/podman.sh run' for a pre-packaged QEMU + SystemC 3.0.1 + Verilator 5.032 + RISC-V GCC SDK, or use --fetch-only / --build."
fi

exit "$overall_rc"
