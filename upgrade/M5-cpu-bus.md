# M5: cpu bus

Target version: 0.4.0. Status: **partial** at baseline.
Dependencies: M1, M2, M3, M4.

## Observed baseline

Ibex/custom OBI-Wishbone RV32 masters accepted; Rocket/BOOM/AXI/TileLink/full Earlgrey remain open.

## Required exits

- [ ] Real CPU-owned RTL adapters with protocol/reset/IRQ/error tests
- [ ] Qualified AXI/TileLink and Rocket then BOOM CPU integrations
- [ ] Explicit complete Earlgrey/subsystem ownership and eligible MMU/OS acceptance

## Evidence and execution

Attach exact candidate/source/tool identities, build commands, real positive and
negative run reports and JUnit to each completed exit. Keep failures and cache
reuse explicit. The aggregate CI gate runs only after the batch is frozen.
A missing behavior/protocol/model is blocked, never a successful placeholder.
