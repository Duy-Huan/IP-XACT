# M8: full designs

Target version: 1.0.0. Status: **partial** at baseline.
Dependencies: M4, M5, M6, M7.

## Observed baseline

16/12-instance native and 9-component RTL qualification fixtures exist; complete inventories remain open.

## Required exits

- [ ] Source/model/adapter/driver/acceptance for all 46 AI_SOC and 37 AIZ_SOC instances
- [ ] Matching synthesizable RTL where source implementations exist
- [ ] Actual mapped firmware execution and public-technology physical evaluation

## Evidence and execution

Attach exact candidate/source/tool identities, build commands, real positive and
negative run reports and JUnit to each completed exit. Keep failures and cache
reuse explicit. The aggregate CI gate runs only after the batch is frozen.
A missing behavior/protocol/model is blocked, never a successful placeholder.
