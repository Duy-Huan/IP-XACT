#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p build/firmware
riscv64-unknown-elf-gcc -march=rv64imac -mabi=lp64 -nostdlib -nostartfiles \
    -Wl,--build-id=none -T examples/riscv-virt/firmware/link.ld \
    examples/riscv-virt/firmware/boot.S -o build/firmware/boot.elf
riscv64-unknown-elf-readelf -h build/firmware/boot.elf
