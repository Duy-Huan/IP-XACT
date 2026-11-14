#!/bin/sh
set -eu
umask 022
mkdir -p "$HOME" "$XDG_CACHE_HOME" "$UV_CACHE_DIR"
exec "$@"
