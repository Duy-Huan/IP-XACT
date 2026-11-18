# M2: model packages

Target version: 0.3.0. Status: **partial** at baseline.
Dependencies: M1.

## Observed baseline

External executable/hash/license v1 exists; source-built package lifecycle and richer transport remain open.

## Required exits

- [ ] Exact VLNV and immutable source/license/build contracts
- [ ] Driver/register/reset/error and transaction capability contracts
- [ ] An independently authored package executes real MMIO/IRQ/error tests on both engines

## Evidence and execution

Attach exact candidate/source/tool identities, build commands, real positive and
negative run reports and JUnit to each completed exit. Keep failures and cache
reuse explicit. The aggregate CI gate runs only after the batch is frozen.
A missing behavior/protocol/model is blocked, never a successful placeholder.
