# M9: qualification release

Target version: 1.0.0. Status: **partial** at baseline.
Dependencies: M8.

## Observed baseline

`v0.3.0-alpha.5` (`6a1492c`) has 826 local mandatory passes (775 initial plus 51
resumed after `tmpfs` ENOSPC repair, zero skips/failures); hosted CI failed on
container `safe.directory` ownership checks; old SDK (`container-v0.2.0-r1`) is
a separate immutable release.

## Required exits

- [ ] Independent unseen design and cold reproducible builds
- [ ] Rootless Podman SDK build/OCI roundtrip and complete evidence closure
- [ ] Mandatory local regression and observed hosted CI before final release

## Evidence and execution

Attach exact candidate/source/tool identities, build commands, real positive and
negative run reports and JUnit to each completed exit. Keep failures and cache
reuse explicit. The aggregate CI gate runs only after the batch is frozen.
A missing behavior/protocol/model is blocked, never a successful placeholder.

The 0.3.0a4 scoped batch adds standard native bus XML/IR v5 and workflow Git-note
observations. See docs/native-bus-xml.md, docs/hosted-ci-receipts.md and
upgrade/bus-xml-batch.md. Full milestone completion remains separately gated;
implementation references alone do not constitute release qualification.

## Observed hosted CI failure on `v0.3.0-alpha.5` (`6a1492c`), 2026-11-19

Direct GitHub Actions inspection recorded failures caused by Git dubious
ownership in Debian container jobs (`/__w/IP-XACT/IP-XACT` owned by host runner
UID while container steps run as UID 0, without persistent `safe.directory` in
`/github/home/.gitconfig`):

- `SoC regression` runs `44554888972` (`dev`) and `44554888959` (`main`):
  `branch-policy`, `workflow-lint`, `regression` and `custom-zephyr` passed
  (`gem5-se` skipped), while `hosted-receipt` failed immediately on
  `git rev-parse --show-toplevel` (exit 128). `workflow-lint` also hit exit 128
  on `git ls-files -z '*.sh'` inside a process substitution in `scripts/ci_lint.sh`.
- `Versioned application prerelease` run `44554889389` (`v0.3.0-alpha.5`):
  `source-release` failed at `Verify immutable tag and full local qualification`
  with `git rev-parse --show-toplevel` exit 128, cascading into `hosted-receipt`.

All container jobs in `.github/workflows/regression.yml` and
`.github/workflows/application-release.yml` now configure `safe.directory` for
`"${GITHUB_WORKSPACE:-$PWD}"` after checkout, and `scripts/ci_lint.sh` checks
`git ls-files` exit status explicitly. Observed hosted pass still requires a
fresh green Actions run and published Git note receipt.

## Current-machine rootless observation, 2026-11-19

An isolated Debian Podman 5.4.2 installation reports `rootless: true` with the
VFS driver. Its only UID/GID map is container 0 -> host 1000, size 1; the cloud
account has no subordinate ranges. Loading the original SHA256-verified SDK
OCI was actually attempted and failed while applying `/etc/gshadow` (GID 42):
`potentially insufficient UIDs or GIDs available in user namespace`.
No SDK load/smoke/round-trip success is claimed from `podman info`. Ownership was
not squashed to bypass this gate. The original OCI and all baseline verification
files still pass their recorded checksums. Exact diagnostics are preserved in
`results/continuous-upgrade-20261119/podman-*`. Rootless build/run acceptance and
new SDK publication remain open; the old SDK revision is separate from the app.
