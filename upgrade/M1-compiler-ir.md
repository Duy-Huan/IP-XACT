# M1: compiler ir

Target version: 0.3.0. Status: **partial** at baseline.
Dependencies: none.

## Observed baseline

Native IR v3; structural 2022 import; flat homogeneous RV64 contracts accepted in v0.3.0-alpha.1.

## Required exits

- [ ] Canonical typed IR and equivalent YAML/IP-XACT intake
- [ ] Explicit CPU, memory, IRQ, clock/reset and hierarchy contracts
- [ ] General interface/address-space/translation representation and capability-checked lowering

## Evidence and execution

The 0.3.0a3 candidate implements typed static decoder routing in native IR v4,
preserving v3 compatibility. See address-decoder-batch.md and
docs/native-address-decoder.md. General IP-XACT bus XML, initiator-specific views,
timed protocols and asynchronous domains remain open; this subset does not close M1.

Attach exact candidate/source/tool identities, build commands, real positive and
negative run reports and JUnit to each completed exit. Keep failures and cache
reuse explicit. The aggregate CI gate runs only after the batch is frozen.
A missing behavior/protocol/model is blocked, never a successful placeholder.

The 0.3.0a4 scoped batch adds standard native bus XML/IR v5 and workflow Git-note
observations. See docs/native-bus-xml.md, docs/hosted-ci-receipts.md and
upgrade/bus-xml-batch.md. Full milestone completion remains separately gated;
implementation references alone do not constitute release qualification.
