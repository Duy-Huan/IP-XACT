# Chipyard-pinned Rocket CPU integration work

This is ongoing M5 implementation on dev. It does not close M5 or M8-M9.
The initial CPU wrapper instantiates the actual RocketTile from the Rocket Chip
commit selected by Chipyard 1.13.0, with its exact Diplomacy/CDE/HardFloat gitlinks.
It retains actual L1 caches, PTW and TLBroadcast, followed by upstream TLToAXI4,
AXI ID/buffer/user adapters and an explicit 64-bit-data, 32-bit-address AXI port.
It is a single tile profile, not the complete Chipyard board or a replacement ISS.

The qualified build direction uses SBT 1.8.2 with the published-checksum JDK
17.0.17+10, Chisel 6.5.0/Scala 2.13.12, a checked Maven inventory, firtool 1.62.0
and Verilator 5.032. JDK21/SBT1.8.2 failure evidence is preserved. Upstream trees
stay clean; no algorithm sources are rewritten. Berkeley/SiFive/Apache notices
are copied with build outputs.

`rocket_elaboration.py` preserves the full descriptive ParamsAnnotation and
SRAMAnnotation data in JSON and removes only those two descriptive classes from
the compiler input. All other annotations remain for strict firtool validation.
Split Verilog output retains blackbox implementation and active assertions.

`rocket_build.py` provides the source build/verify API. RAM and ROM/MMIO apertures
must be explicit and must fit the supported physical address width; they determine
actual PMA visibility. Other harts, bus widths/ISA profiles, external DMA coherence
and full SoC timing require their own adapters and tests. Errors do not fall back
to ISS execution. Source model behavior is never inferred from register metadata.

The AXI target adapter performs explicit byte-strobe handling and decomposes
64-bit beats into the existing aligned MMIO32 memory transport. QEMU qtest or
gem5 external atomic master owns backing RAM/peripherals; actual RTL owns CPU
instructions and L1 state. Native gem5 ISS counters must remain zero. Retirement
and cycles come from Rocket's actual commit trace and real simulated clock edges.

Exploratory runs have exercised real timer, Chipyard GCD and OpenTitan GPIO IRQs
on both engines. The initial final-response drain assertion was caught and fixed.
Deeper tests distinguish PMA access exceptions from downstream AXI errors; the
latter are not assumed to be precise CPU load exceptions. The current candidate
is not release qualified: production build, firmware/negative/IRQ/reset/burst/
backpressure acceptance, plan binding and aggregate mandatory CI remain required.
