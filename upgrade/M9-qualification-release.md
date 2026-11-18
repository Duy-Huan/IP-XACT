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
