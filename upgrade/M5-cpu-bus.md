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

## Promoted v0.3.0-alpha.5 checkpoint (`6a1492c`)

Pinned RocketTile/AXI32 builder, native IR v6 source-CPU binding (12/11-instance
AI_SOC/AIZ_SOC YAML and bus-XML flows) and a separate source-bound Rocket
OpenSBI/Sv39/Linux profile (32 MiB software RAM on 128 MiB physical RAM) were
promoted in `v0.3.0-alpha.5` (826 local passes, zero skips/failures). See
[rocket-cpu.md](../docs/rocket-cpu.md) and [native-rtl-cpu.md](../docs/native-rtl-cpu.md)
for exact identities and limits. BOOM, general AXI/TileLink, full Earlgrey top
integration, multi-hart RTL CPUs and native RTL Linux BSPs remain open; none of
the broad required exits above is closed.
