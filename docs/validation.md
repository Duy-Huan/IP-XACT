# Initial implementation validation

Validated on 2026-11-11 in the current Debian 13 amd64 cloud workspace. This is
local execution evidence, not a hosted GitHub Actions run or a fresh-task restore.

| Check | Observed outcome |
|---|---|
| Frozen Python setup | Passed with uv 0.13.5 / Python 3.12.14 |
| Official public 2022 XSD + strict semantic subset | Positive and negative tests passed |
| Compiler/model and process regression | 49 tests passed, zero failed/skipped |
| Real simulator | QEMU 10.0.13, Debian 1:10.0.13+ds-0+deb13u1 |
| Real firmware compiler | RISC-V unknown-elf GCC 14.2.0+19 |
| Guest smoke | UART SOCSIM_BOOT_OK; SiFive finisher exit zero |
| Guest failure control | Actual finisher failure exit 1, correctly classified failed |
| Guest hang control | Timeout, process group terminated, correctly classified failed |
| Static/docs | Ruff lint + formatting and documentation gate passed |
| Distribution | Built wheel, installed into separate environment, validated example |
| Rootless setup helper | Executed, then sourced generated environment and passed regression |
| Workflow YAML and shell scripts | Parsed structure / bash syntax; hosted execution not run |
| Private reference archive | Local copy removed, no extraction or internal Git history imported |

The local smoke bundle is under ignored `results/initial-smoke/`; it preserves
input snapshots from that run, before the subsequent XML pretty formatting. The
latest compiler/regression tests use the formatted source. JUnit is generated at
`build/junit.xml`. Run `bash scripts/regression.sh` to create current evidence.
The two expected guest failures are passing negative tests, not skipped failures.

Remaining scope: custom machine generation, richer IP-XACT semantics, gem5,
Chipyard, OpenTitan, SystemC, RTOS and Yocto integration are roadmap items. No
cross-engine equivalence, performance calibration, OS boot or security verification
has been demonstrated. Hosted CI, repository rulesets and cloud draft publication
are separate from the validated local development workflow.
