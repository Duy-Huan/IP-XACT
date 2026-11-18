# M7: performance

Target version: 0.4.0. Status: **partial** at baseline.
Dependencies: M3, M6.

## Observed baseline

Per-hart gem5 counters and separate QEMU event cost estimates exist; unified ROI/calibration remain open.

## Required exits

- [ ] Per-hart/IP/network ROI and source-labelled native counters
- [ ] QEMU measured events with explicit configurable cost estimates
- [ ] Reference calibration, uncertainty and model-error reporting

## Evidence and execution

Attach exact candidate/source/tool identities, build commands, real positive and
negative run reports and JUnit to each completed exit. Keep failures and cache
reuse explicit. The aggregate CI gate runs only after the batch is frozen.
A missing behavior/protocol/model is blocked, never a successful placeholder.
