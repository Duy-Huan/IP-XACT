#!/usr/bin/env bash
# Pinned Chipyard 1.13.0 source acquisition, prerequisite audit, and SHA-256 verified
# Chisel/CIRCT generator tools (sbt-launch-1.10.7.jar + firtool 1.62.0).
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/common.sh
source "$script_dir/lib/common.sh"

REPO=$(repo_root)
CHIPYARD_URL="https://github.com/ucb-bar/chipyard.git"
CHIPYARD_TAG="1.13.0"
CHIPYARD_SHA="69eba860a352343e4ac6b6df0f3638a79a86ec78"

UPSTREAM_ROOT="${USP_UPSTREAM_ROOT:-$REPO/.tools/upstream}"
TOOLS_DIR="${USP_CHIPYARD_TOOLS:-$REPO/.tools/chipyard}"
MODE="check"
WITH_TOOLS=0

usage() {
    cat <<'EOF'
Usage: scripts/setup_chipyard.sh [OPTIONS]

Audit or set up pinned Chipyard 1.13.0 sources and SHA-256 verified Chisel/CIRCT tools.

Options:
  --check              Audit JDK 21+, Verilator, RISC-V GCC, pinned Chipyard, and sbt/firtool (default)
  --fetch-only         Clone and SHA-verify Chipyard 1.13.0 (add --tools to also fetch sbt/firtool)
  --tools              Also run scripts/setup_chipyard_tools.sh (sbt-launch-1.10.7.jar + firtool 1.62.0)
  --tools-only         Only fetch & SHA-verify sbt-launch and firtool without cloning Chipyard
  --upstream-dir DIR   Upstream source root (default: <repo>/.tools/upstream)
  --tools-dir DIR      Generator tools directory (default: <repo>/.tools/chipyard)
  -h, --help           Show this help message

Pinned Source & Generator Artifacts:
  Repo:    https://github.com/ucb-bar/chipyard.git
  Tag:     1.13.0
  SHA:     69eba860a352343e4ac6b6df0f3638a79a86ec78
  Tools:   sbt-launch-1.10.7.jar, llvm-firtool-1.62.0-linux-x64.jar (SHA-256 locked)
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check) MODE="check"; shift ;;
        --fetch-only) MODE="fetch"; shift ;;
        --tools) WITH_TOOLS=1; shift ;;
        --tools-only) MODE="tools"; shift ;;
        --upstream-dir) UPSTREAM_ROOT=${2:?Missing value for --upstream-dir}; shift 2 ;;
        --tools-dir) TOOLS_DIR=${2:?Missing value for --tools-dir}; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1 (use --help)" ;;
    esac
done

CHIPYARD_SRC="$UPSTREAM_ROOT/chipyard"

run_check() {
    local gaps=0
    log_info "Auditing Chipyard environment (pinned $CHIPYARD_TAG @ ${CHIPYARD_SHA:0:12})"
    for tool in java verilator riscv64-unknown-elf-gcc curl; do
        check_cmd "$tool" || gaps=$((gaps + 1))
    done

    verify_pinned_repo_readonly "chipyard" "$CHIPYARD_SHA" "$CHIPYARD_SRC" || gaps=$((gaps + 1))

    if [[ -f "$TOOLS_DIR/sbt-launch-1.10.7.jar" ]]; then
        log_ok "Chipyard sbt-launch present: $TOOLS_DIR/sbt-launch-1.10.7.jar"
    else
        log_warn "Missing sbt-launch-1.10.7.jar in $TOOLS_DIR (run with --tools)"
        gaps=$((gaps + 1))
    fi

    if [[ -x "$TOOLS_DIR/firtool" ]]; then
        log_ok "Chipyard firtool ready: $("$TOOLS_DIR/firtool" --version 2>&1 | head -n 1)"
    else
        log_warn "Missing firtool binary in $TOOLS_DIR (run with --tools)"
        gaps=$((gaps + 1))
    fi

    if (( gaps > 0 )); then
        log_warn "Chipyard audit found $gaps missing prerequisite(s)/artifact(s)."
        return 1
    fi
    log_ok "Chipyard environment audit PASSED."
    return 0
}

case "$MODE" in
    check)
        run_check
        ;;
    fetch)
        clone_or_verify_pinned_repo "chipyard" "$CHIPYARD_URL" "$CHIPYARD_TAG" "$CHIPYARD_SHA" "$CHIPYARD_SRC"
        if (( WITH_TOOLS == 1 )); then
            USP_CHIPYARD_TOOLS="$TOOLS_DIR" bash "$script_dir/setup_chipyard_tools.sh"
        fi
        ;;
    tools)
        USP_CHIPYARD_TOOLS="$TOOLS_DIR" bash "$script_dir/setup_chipyard_tools.sh"
        ;;
esac
