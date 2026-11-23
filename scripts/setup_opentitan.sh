#!/usr/bin/env bash
# Pinned OpenTitan (earlgrey_silver_release_v5) source acquisition, Verilator/SystemC
# prerequisite audit, and RTL source tree verification.
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/common.sh
source "$script_dir/lib/common.sh"

REPO=$(repo_root)
OPENTITAN_URL="https://github.com/lowRISC/opentitan.git"
OPENTITAN_TAG="earlgrey_silver_release_v5"
OPENTITAN_SHA="ed044fc9760bdf9fc075d0015ba1db07fa075355"

UPSTREAM_ROOT="${USP_UPSTREAM_ROOT:-$REPO/.tools/upstream}"
MODE="check"
VERIFY_RTL=0

usage() {
    cat <<'EOF'
Usage: scripts/setup_opentitan.sh [OPTIONS]

Audit, fetch, and verify pinned OpenTitan (earlgrey_silver_release_v5) RTL sources
and SystemC/Verilator co-simulation prerequisites.

Options:
  --check              Audit Verilator, SystemC 3.0.1 headers, Yosys, and pinned checkout (default)
  --fetch-only         Clone and SHA-verify OpenTitan earlgrey_silver_release_v5
  --verify-rtl         Verify required OpenTitan GPIO / TL-UL / Prim FIFO RTL files exist
  --upstream-dir DIR   Upstream source root (default: <repo>/.tools/upstream)
  -h, --help           Show this help message

Pinned Source:
  Repo: https://github.com/lowRISC/opentitan.git
  Tag:  earlgrey_silver_release_v5
  SHA:  ed044fc9760bdf9fc075d0015ba1db07fa075355
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check) MODE="check"; shift ;;
        --fetch-only) MODE="fetch"; shift ;;
        --verify-rtl) VERIFY_RTL=1; shift ;;
        --upstream-dir) UPSTREAM_ROOT=${2:?Missing value for --upstream-dir}; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1 (use --help)" ;;
    esac
done

OT_SRC="$UPSTREAM_ROOT/opentitan"

check_systemc_header() {
    local candidate
    for candidate in \
        "${SYSTEMC_HOME:-}/include/systemc.h" \
        "${USP_HOST_PREFIX:-}/usr/include/systemc.h" \
        /usr/include/systemc.h \
        /usr/local/include/systemc.h; do
        if [[ -n "$candidate" && -f "$candidate" ]]; then
            log_ok "SystemC header found: $candidate"
            return 0
        fi
    done
    log_warn "SystemC header (systemc.h) not found (install libsystemc-dev or use Podman SDK)"
    return 1
}

verify_rtl_files() {
    local missing=0
    local rel
    local required_rtl=(
        "hw/ip/gpio/rtl/gpio.sv"
        "hw/ip/tlul/rtl/tlul_pkg.sv"
        "hw/ip/prim/rtl/prim_fifo_sync.sv"
    )
    for rel in "${required_rtl[@]}"; do
        if [[ -f "$OT_SRC/$rel" ]]; then
            log_ok "OpenTitan RTL present: $rel"
        else
            log_warn "Expected OpenTitan RTL missing: $OT_SRC/$rel"
            missing=$((missing + 1))
        fi
    done
    return "$missing"
}

run_check() {
    local gaps=0
    log_info "Auditing OpenTitan RTL & SystemC environment (pinned $OPENTITAN_TAG @ ${OPENTITAN_SHA:0:12})"
    for tool in verilator g++ python3 yosys; do
        check_cmd "$tool" || gaps=$((gaps + 1))
    done
    check_systemc_header || gaps=$((gaps + 1))
    verify_pinned_repo_readonly "opentitan" "$OPENTITAN_SHA" "$OT_SRC" || gaps=$((gaps + 1))

    if (( VERIFY_RTL == 1 )) && [[ -d "$OT_SRC" ]]; then
        verify_rtl_files || gaps=$((gaps + 1))
    fi

    if (( gaps > 0 )); then
        log_warn "OpenTitan audit found $gaps missing prerequisite(s)/source(s)."
        return 1
    fi
    log_ok "OpenTitan environment audit PASSED."
    return 0
}

case "$MODE" in
    check)
        run_check
        ;;
    fetch)
        clone_or_verify_pinned_repo "opentitan" "$OPENTITAN_URL" "$OPENTITAN_TAG" "$OPENTITAN_SHA" "$OT_SRC"
        if (( VERIFY_RTL == 1 )); then
            verify_rtl_files || die "OpenTitan RTL source verification failed"
        fi
        ;;
esac
