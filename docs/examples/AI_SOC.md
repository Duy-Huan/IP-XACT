# AI_SOC executable example

The runnable IP-XACT project is [examples/AI_SOC](../../examples/AI_SOC). Read its
README for validation, planning, actual shared-ELF QEMU/gem5/SystemC execution and
portable verification commands. It must match the canonical
[systems/AI_SOC/bringup](../../systems/AI_SOC/bringup); tests enforce file and IR
parity. Matching existing generated/native outputs can be reused only after
their normal identity checks pass.

This is the nine-component bring-up subset, not the larger requested heterogeneous
architecture. It contains real source-backed GCD/GPIO IPs but no functional AI
accelerator. The full architecture inventory, separate RTL test chip and public
reference layout have their own acceptance scopes. Read [dual-soc.md](../dual-soc.md),
[unified-soc.md](../unified-soc.md), [next-version.md](../next-version.md) and
[industrial-readiness.md](../industrial-readiness.md) for the boundaries.
