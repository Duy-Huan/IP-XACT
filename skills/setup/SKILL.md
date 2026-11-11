---
name: setup
description: Set up and verify the supported IP-XACT and QEMU development workflow.
---

# Setup workflow

Implements agent.md §§2–5. Read it and docs/roadmap.md first. Work on `dev` in the
existing isolated checkout; do not create a worktree unless requested. Inspect
Git changes, Python/uv availability, disk and actual engine versions before install.
Do not overwrite user files or download confidential reference material.

Run `bash scripts/setup.sh`, then `source .venv/bin/activate`. The script uses
`uv sync --frozen --extra dev` and performs offline schema/example validation.
It does not install QEMU, compile firmware or modify host packages. Pin uv in CI;
on a normal host install it through a verified official package route.

Required external tools for the full gate: QEMU 10.0.13, a bare-metal RISC-V GCC
and binutils. On Debian trixie use `qemu-system-riscv=1:10.0.13+ds-0+deb13u1`,
`qemu-system-data=1:10.0.13+ds-0+deb13u1`, `gcc-riscv64-unknown-elf` and
`binutils-riscv64-unknown-elf`. Follow environment package policy; never use
unsigned packages, disable TLS, or assume escalation grants root.

When root installation is unavailable, use `scripts/setup_user_tools.sh` on a
Debian trixie amd64 host. It downloads through signed Debian package indexes and
extracts into an external user-owned tools directory. Source the generated env.sh.
This helper is platform-specific and does not replace a future source-build lock.

Then run `socsim doctor`, `socsim plan examples/riscv-virt`,
`bash scripts/build_firmware.sh`, a real `socsim run` with a new results directory,
and `bash scripts/regression.sh`. Inspect raw serial, exit status and report files.
Missing tools are blocked for promotion. Do not replace real integration with
host-printed markers. Keep run artifacts ignored; leave tracked files unchanged
during setup. Do not build planned engines or OS trees without a selected target.

After restore, activate `.venv` and source tools env.sh if needed. No persistent
service is required. Report exact tests run and any external capability still absent.
