# Address-decoder batch: candidate 0.3.0a3

Continue M1 before broad CPU/network lowering. Preserve explicit local address
spaces and bridge intervals; support only static decoder trees with checked
physical lowering and exact source/model/CPU ownership. Reject unsupported
protocols/aliases/cycles/width overflow/clipping/shadowing before output.

Implemented source: native_fabric.py, native IR v4, fabric native/flow intake and
two original translated fixtures. Required acceptance: frontend negatives,
canonical graph/projection consistency, actual source-IP read/write/IRQ and
bare-metal/FreeRTOS/Zephyr/µC/OS-II/III on both engines, followed by the complete
mandatory aggregate gate. Focused probes do not replace final qualification.

Keep broad M1-M9 status partial. General IP-XACT bus XML, per-initiator views,
timed transaction adapters, aliases, asynchronous clocks/resets, DMA/coherence,
RTL CPU/MMU, Yocto and full-design/release exits remain open. Commit work on dev;
merge/push/tag only after the exact candidate passes required local checks.
