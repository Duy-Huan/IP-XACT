# M6: network time

Target version: 0.4.0. Status: **partial** at baseline.
Dependencies: M3, M5.

## Observed baseline

Live Garnet and coherent DMA are separate qualified profiles; generic lowering/global timing remain open.

## Required exits

- [ ] IR-generated network topology on the actual transaction path
- [ ] Qualified DMA/coherence/backpressure/reset/error contracts
- [ ] Explicit synchronization/ownership and no undisclosed PIO bypass

## Evidence and execution

Attach exact candidate/source/tool identities, build commands, real positive and
negative run reports and JUnit to each completed exit. Keep failures and cache
reuse explicit. The aggregate CI gate runs only after the batch is frozen.
A missing behavior/protocol/model is blocked, never a successful placeholder.
