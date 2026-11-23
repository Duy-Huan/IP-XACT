#!/usr/bin/env bash
# Pinned gem5 v24.1.0.3 source acquisition, prerequisite audit, and optional RISCV/gem5.opt build.
# Uses the external SystemC process profile (USE_SYSTEMC=0) per repo build policy.
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/common.sh
source "$script_dir/lib/common.sh"

REPO=$(repo_root)
GEM5_URL="https://github.com/gem5/gem5.git"
GEM5_TAG="v24.1.0.3"
GEM5_SHA="b9da2bfe1e21f6efa2730edd4e80e58c5d965458"

UPSTREAM_ROOT="${USP_UPSTREAM_ROOT:-$REPO/.tools/upstream}"
JOBS=$(default_jobs)
MODE="check"
TARGET="build/RISCV/gem5.opt"

usage() {
    cat <<'EOF'
Usage: scripts/setup_gem5.sh [OPTIONS]

Audit, fetch, or build pinned gem5 v24.1.0.3 (RISCV ISA + Garnet NoC support).

Options:
  --check              Audit host compiler/SCons/Python-dev prerequisites and pinned source (default)
  --fetch-only         Clone and SHA-verify gem5 v24.1.0.3 without compiling
  --build              Fetch (if absent) and compile build/RISCV/gem5.opt via SCons
  --target PATH        SCons build target (default: build/RISCV/gem5.opt)
  --upstream-dir DIR   Upstream source root (default: <repo>/.tools/upstream)
  --jobs N             Parallel SCons build jobs (default: nproc or $USP_JOBS)
  -h, --help           Show this help message

Pinned Source:
  Repo: https://github.com/gem5/gem5.git
  Tag:  v24.1.0.3
  SHA:  b9da2bfe1e21f6efa2730edd4e80e58c5d965458
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check) MODE="check"; shift ;;
        --fetch-only) MODE="fetch"; shift ;;
        --build) MODE="build"; shift ;;
        --target) TARGET=${2:?Missing value for --target}; shift 2 ;;
        --upstream-dir) UPSTREAM_ROOT=${2:?Missing value for --upstream-dir}; shift 2 ;;
        --jobs) JOBS=${2:?Missing value for --jobs}; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1 (use --help)" ;;
    esac
done

GEM5_SRC="$UPSTREAM_ROOT/gem5"

find_scons() {
    if command -v scons >/dev/null 2>&1; then
        command -v scons
    elif [[ -x "$REPO/.venv/bin/scons" ]]; then
        printf '%s\n' "$REPO/.venv/bin/scons"
    else
        return 1
    fi
}

run_check() {
    local gaps=0
    log_info "Auditing gem5 environment (pinned $GEM5_TAG @ ${GEM5_SHA:0:12})"
    for tool in gcc g++ m4 pkg-config python3-config; do
        check_cmd "$tool" || gaps=$((gaps + 1))
    done

    if scons_bin=$(find_scons); then
        log_ok "SCons available: $scons_bin"
    else
        log_warn "SCons missing (install via: bash scripts/setup_venv.sh --extra models)"
        gaps=$((gaps + 1))
    fi

    check_pkg_config "zlib" || gaps=$((gaps + 1))
    verify_pinned_repo_readonly "gem5" "$GEM5_SHA" "$GEM5_SRC" || gaps=$((gaps + 1))

    if [[ -x "$GEM5_SRC/$TARGET" ]]; then
        log_ok "Built gem5 binary ready: $GEM5_SRC/$TARGET"
    else
        log_info "gem5 binary not yet built at $GEM5_SRC/$TARGET"
    fi

    if (( gaps > 0 )); then
        log_warn "gem5 audit found $gaps missing prerequisite(s)/source(s)."
        return 1
    fi
    log_ok "gem5 environment audit PASSED."
    return 0
}

case "$MODE" in
    check)
        run_check
        ;;
    fetch)
        clone_or_verify_pinned_repo "gem5" "$GEM5_URL" "$GEM5_TAG" "$GEM5_SHA" "$GEM5_SRC"
        ;;
    build)
        clone_or_verify_pinned_repo "gem5" "$GEM5_URL" "$GEM5_TAG" "$GEM5_SHA" "$GEM5_SRC"
        scons_bin=$(find_scons) || die "SCons not found; run bash scripts/setup_venv.sh --extra models"
        for tool in g++ m4 python3-config; do
            command -v "$tool" >/dev/null 2>&1 || die "Required gem5 build tool missing: $tool"
        done
        log_info "Building gem5 target $TARGET with $JOBS job(s) (USE_SYSTEMC=0 external profile)"
        (cd "$GEM5_SRC" && "$scons_bin" "$TARGET" -j "$JOBS" --ignore-style USE_SYSTEMC=0)
        log_ok "gem5 build complete: $GEM5_SRC/$TARGET"
        ;;
esac
