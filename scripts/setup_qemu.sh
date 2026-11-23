#!/usr/bin/env bash
# Pinned QEMU v10.0.0 source acquisition, prerequisite audit, and optional RISC-V build.
# Revision pin matches scripts/fetch_upstreams.sh and docs/build-reproducibility.md.
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/common.sh
source "$script_dir/lib/common.sh"

REPO=$(repo_root)
QEMU_URL="https://github.com/qemu/qemu.git"
QEMU_TAG="v10.0.0"
QEMU_SHA="7c949c53e936aa3a658d84ab53bae5cadaa5d59c"

UPSTREAM_ROOT="${USP_UPSTREAM_ROOT:-$REPO/.tools/upstream}"
BUILD_DIR="${USP_QEMU_BUILD_DIR:-$REPO/.tools/build/qemu}"
INSTALL_PREFIX="${USP_QEMU_PREFIX:-}"
JOBS=$(default_jobs)
MODE="check"

usage() {
    cat <<'EOF'
Usage: scripts/setup_qemu.sh [OPTIONS]

Audit, fetch, or build pinned QEMU v10.0.0 (RISC-V 64/32 softmmu).

Options:
  --check              Audit host QEMU binaries, build prerequisites, and pinned source (default)
  --fetch-only         Clone and SHA-verify QEMU v10.0.0 without compiling
  --build              Fetch (if absent) and compile riscv64-softmmu,riscv32-softmmu
  --upstream-dir DIR   Upstream source root (default: <repo>/.tools/upstream)
  --build-dir DIR      Out-of-tree build directory (default: <repo>/.tools/build/qemu)
  --prefix DIR         Optional install prefix (runs `ninja install` when set)
  --jobs N             Parallel build jobs (default: nproc or $USP_JOBS)
  -h, --help           Show this help message

Pinned Source:
  Repo: https://github.com/qemu/qemu.git
  Tag:  v10.0.0
  SHA:  7c949c53e936aa3a658d84ab53bae5cadaa5d59c
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check) MODE="check"; shift ;;
        --fetch-only) MODE="fetch"; shift ;;
        --build) MODE="build"; shift ;;
        --upstream-dir) UPSTREAM_ROOT=${2:?Missing value for --upstream-dir}; shift 2 ;;
        --build-dir) BUILD_DIR=${2:?Missing value for --build-dir}; shift 2 ;;
        --prefix) INSTALL_PREFIX=${2:?Missing value for --prefix}; shift 2 ;;
        --jobs) JOBS=${2:?Missing value for --jobs}; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1 (use --help)" ;;
    esac
done

QEMU_SRC="$UPSTREAM_ROOT/qemu"

run_check() {
    local gaps=0
    log_info "Auditing QEMU environment (pinned $QEMU_TAG @ ${QEMU_SHA:0:12})"
    if command -v qemu-system-riscv64 >/dev/null 2>&1; then
        log_ok "Host QEMU binary: $(qemu-system-riscv64 --version | head -n 1)"
    else
        log_warn "Host qemu-system-riscv64 not on PATH"
        gaps=$((gaps + 1))
    fi

    for tool in gcc g++ make pkg-config ninja meson dtc; do
        check_cmd "$tool" || gaps=$((gaps + 1))
    done
    for lib in glib-2.0 pixman-1; do
        check_pkg_config "$lib" || gaps=$((gaps + 1))
    done

    verify_pinned_repo_readonly "qemu" "$QEMU_SHA" "$QEMU_SRC" || gaps=$((gaps + 1))

    if [[ -x "$BUILD_DIR/qemu-system-riscv64" ]]; then
        log_ok "Built pinned QEMU binary: $BUILD_DIR/qemu-system-riscv64"
    else
        log_info "Custom pinned QEMU binary not yet compiled at $BUILD_DIR/qemu-system-riscv64"
    fi

    if (( gaps > 0 )); then
        log_warn "QEMU audit found $gaps missing prerequisite(s)/source(s)."
        return 1
    fi
    log_ok "QEMU environment audit PASSED."
    return 0
}

case "$MODE" in
    check)
        run_check
        ;;
    fetch)
        clone_or_verify_pinned_repo "qemu" "$QEMU_URL" "$QEMU_TAG" "$QEMU_SHA" "$QEMU_SRC"
        ;;
    build)
        clone_or_verify_pinned_repo "qemu" "$QEMU_URL" "$QEMU_TAG" "$QEMU_SHA" "$QEMU_SRC"
        for tool in gcc g++ make ninja pkg-config; do
            command -v "$tool" >/dev/null 2>&1 || die "Required QEMU build tool missing: $tool"
        done
        mkdir -p "$BUILD_DIR"
        cfg_args=(
            "--target-list=riscv64-softmmu,riscv32-softmmu"
            --disable-werror
            --disable-docs
            --disable-gtk
            --disable-sdl
        )
        if [[ -n "$INSTALL_PREFIX" ]]; then
            cfg_args+=("--prefix=$INSTALL_PREFIX")
        fi
        log_info "Configuring QEMU $QEMU_TAG in $BUILD_DIR"
        (cd "$BUILD_DIR" && "$QEMU_SRC/configure" "${cfg_args[@]}")
        log_info "Building QEMU $QEMU_TAG with $JOBS job(s)"
        ninja -C "$BUILD_DIR" -j "$JOBS" qemu-system-riscv64 qemu-system-riscv32
        if [[ -n "$INSTALL_PREFIX" ]]; then
            ninja -C "$BUILD_DIR" install
        fi
        log_ok "QEMU build complete: $BUILD_DIR/qemu-system-riscv64"
        ;;
esac
