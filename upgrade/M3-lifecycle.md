# M3: lifecycle

Target version: 0.3.0. Status: **partial** at baseline.
Dependencies: M1, M2.

## Observed baseline

Native CLI has separate commands; common persisted execution lifecycle remains open.

## Required exits

- [ ] One generated plan/build/software/run/verify lifecycle
- [ ] Explicit CPU/RAM/peripheral/time ownership and resource preflight
- [ ] Previously unseen supported designs run without compiler edits or ISS substitution

## Evidence and execution

Attach exact candidate/source/tool identities, build commands, real positive and
negative run reports and JUnit to each completed exit. Keep failures and cache
reuse explicit. The aggregate CI gate runs only after the batch is frozen.
A missing behavior/protocol/model is blocked, never a successful placeholder.
