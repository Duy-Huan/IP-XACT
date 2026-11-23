#!/usr/bin/env bash
# Shared logging, prerequisite checking, and pinned-source verification helpers
# for socsim-platform setup scripts.
# shellcheck shell=bash

if [[ -n "${_SOCSIM_COMMON_SH_LOADED:-}" ]]; then
    return 0
fi
_SOCSIM_COMMON_SH_LOADED=1

log_info() {
    printf '[INFO] %s\n' "$*"
}

log_ok() {
    printf '[ OK ] %s\n' "$*"
}

log_warn() {
    printf '[WARN] %s\n' "$*" >&2
}

log_err() {
    printf '[ERR ] %s\n' "$*" >&2
}

die() {
    log_err "$*"
    exit 1
}

repo_root() {
    local script_dir
    script_dir=$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)
    if [[ -f "$script_dir/pyproject.toml" ]]; then
        printf '%s\n' "$script_dir"
    elif [[ -f "$script_dir/../pyproject.toml" ]]; then
        (cd "$script_dir/.." && pwd)
    elif [[ -f "$script_dir/../../pyproject.toml" ]]; then
        (cd "$script_dir/../.." && pwd)
    else
        die "Unable to locate repository root (pyproject.toml not found)"
    fi
}

default_jobs() {
    if [[ -n "${USP_JOBS:-}" ]]; then
        printf '%s\n' "$USP_JOBS"
    elif command -v nproc >/dev/null 2>&1; then
        nproc
    else
        printf '2\n'
    fi
}

check_cmd() {
    local cmd=$1
    local desc=${2:-$1}
    if command -v "$cmd" >/dev/null 2>&1; then
        log_ok "Tool available: $desc ($(command -v "$cmd"))"
        return 0
    else
        log_warn "Tool missing:   $desc ($cmd)"
        return 1
    fi
}

check_pkg_config() {
    local mod=$1
    if command -v pkg-config >/dev/null 2>&1 && pkg-config --exists "$mod" 2>/dev/null; then
        local ver
        ver=$(pkg-config --modversion "$mod" 2>/dev/null || printf 'unknown')
        log_ok "Library found:  $mod ($ver)"
        return 0
    else
        log_warn "Library missing: $mod (pkg-config)"
        return 1
    fi
}

check_python_min() {
    local py_bin=$1
    local min_major=${2:-3}
    local min_minor=${3:-11}
    if ! command -v "$py_bin" >/dev/null 2>&1 && [[ ! -x "$py_bin" ]]; then
        log_warn "Python binary not found: $py_bin"
        return 1
    fi
    local py_ver
    py_ver=$("$py_bin" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")' 2>/dev/null || true)
    if [[ -z "$py_ver" ]]; then
        log_warn "Failed to query Python version from $py_bin"
        return 1
    fi
    if "$py_bin" -c "import sys; raise SystemExit(0 if sys.version_info >= (${min_major}, ${min_minor}) else 1)"; then
        log_ok "Python version: $py_ver (>= ${min_major}.${min_minor}) at $py_bin"
        return 0
    else
        log_warn "Python version too old: $py_ver (requires >= ${min_major}.${min_minor})"
        return 1
    fi
}

clone_or_verify_pinned_repo() {
    local name=$1
    local url=$2
    local tag=$3
    local expected_sha=$4
    local dest=$5

    mkdir -p "$(dirname "$dest")"
    if [[ ! -e "$dest" ]]; then
        log_info "Cloning pinned $name ($tag @ ${expected_sha:0:12}) -> $dest"
        git clone --depth 1 --branch "$tag" "$url" "$dest"
    fi

    local actual_sha
    actual_sha=$(git -C "$dest" rev-parse HEAD)
    if [[ "$actual_sha" != "$expected_sha" ]]; then
        die "Revision mismatch for $name: expected $expected_sha, found $actual_sha"
    fi
    if [[ -n "$(git -C "$dest" status --porcelain)" ]]; then
        die "Refusing to modify dirty upstream checkout: $dest"
    fi
    log_ok "Verified pinned $name checkout ($tag @ ${actual_sha:0:12})"
}

verify_pinned_repo_readonly() {
    local name=$1
    local expected_sha=$2
    local dest=$3

    if [[ ! -d "$dest/.git" ]]; then
        log_warn "Upstream $name not fetched at $dest (expected SHA ${expected_sha:0:12})"
        return 1
    fi
    local actual_sha
    actual_sha=$(git -C "$dest" rev-parse HEAD 2>/dev/null || printf 'invalid')
    if [[ "$actual_sha" != "$expected_sha" ]]; then
        log_warn "Upstream $name revision mismatch at $dest: expected ${expected_sha:0:12}, found ${actual_sha:0:12}"
        return 1
    fi
    if [[ -n "$(git -C "$dest" status --porcelain 2>/dev/null)" ]]; then
        log_warn "Upstream $name checkout has local uncommitted edits: $dest"
        return 1
    fi
    log_ok "Upstream $name pinned & clean: $dest (${actual_sha:0:12})"
    return 0
}
