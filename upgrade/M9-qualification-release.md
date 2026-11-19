# M9: qualification release

Target version: 1.0.0. Status: **partial** at baseline.
Dependencies: M8.

## Observed baseline

v0.3.0-alpha.1 has 534 local mandatory passes; hosted CI remains unobserved; old SDK is a separate immutable release.

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
