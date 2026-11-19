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

## Development checkpoint, 2026-11-19

Pinned RocketTile/AXI32 builder and a real SystemC physical-master target now
exist on dev. All three low-PA maps passed actual bare-metal and PMP/Sv39 M/S/U
conformance on both memory engines with 8-beat bursts and request backpressure.
Initial rv64-ips FreeRTOS scheduler/IRQ tests also passed on both engines. See
[rocket-cpu.md](../docs/rocket-cpu.md) for identities and explicit limitations.
These are engineering executions; mandatory final candidate CI is still pending.
Generic native instance binding, BOOM, broader AXI/TileLink, full Earlgrey and
Linux RTL boot remain open; none of the broad required exits above is closed.
