# Continuous Rocket/native source-CPU batch

This development batch advances M3–M5 while preserving every prior qualification
gate. It is not completion of M1–M9 or arbitrary SoC support. No version tag is
created until the complete frozen candidate passes its actual aggregate gate.

- Source-build the Chipyard-pinned RocketTile/L1/PTW/TLBroadcast/TLToAXI4 and
  verify real instruction ownership, backpressure, downstream errors, PMP/Sv39
  M/S/U and eligible source-built kernels on both backing-memory engines.
- Bind that CPU explicitly in native IR v6 without an ISS replacement; compile
  arbitrary instance names, maps and IRQs. The owned AI_SOC/AIZ_SOC fixtures have
  12/11 instances, distinct from the full 46/37 inventories.
- Persist a native source-CPU software flow using YAML and standard-bus XML.
  Run bare-metal and all four eligible M-mode kernels on both engines; capture
  actual retirement/AXI/IP/IRQ evidence and verify zero gem5 ISS instructions.
- Source-bind the separate Rocket OpenSBI/Linux trampoline, DTB, kernel and
  userspace. Require all three legacy maps on both engines. The explicit 32 MiB
  software RAM limit does not reduce the physical RAM inventory or imply full
  RAM acceptance. Native RTL Linux needs a separate generated BSP and is rejected.
- Preserve exact binary source-cache entries during real gem5 linking and retain
  failures/interrupted transfers. Serialize cold XSD initialization and check it
  with a fresh four-thread process. Include the native RTL resource in wheels.

Actual focused probes and failures are retained under the ignored
`results/continuous-upgrade-20261119/`. They do not replace workflow lint, clean
minimal Debian, the full mandatory native regression and checked main promotion.
Hosted CI, release assets, rootless SDK round-trip, BOOM/full Earlgrey, generic
DMA/coherence/time, Yocto and complete hardware inventories remain separate exits.
