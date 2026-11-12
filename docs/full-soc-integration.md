# Full Rocket and Earlgrey: integration boundary

The current rv64-ips profile executes selected Chipyard GCD and OpenTitan GPIO
RTL with QEMU owning CPU/RAM. Full SoCs require distinct profiles in which the RTL
simulator owns CPU, memories, reset, clocks and interrupts. Two CPU/RAM owners
cannot be combined by adding another peripheral binding.

## Chipyard 1.13.0

The source pin is 69eba860a352343e4ac6b6df0f3638a79a86ec78. Inspection on
2026-11-12 found the full Git submodule graph uninitialized. Important gitlinks
include rocket-chip 72690b07c0939a58670418b97e5442835b2049b5,
diplomacy 6b7dc988a771ff21237b102fd02a1f2d2c2e7b72 and
testchipip c94c1e3fa9f7437a3e95f63181cf0f54b8650b3a. The full build must resolve
its required graph at those exact gitlinks, retain notices and record nested pins.
The current isolated GCD adapter does not initialize or validate that graph.

Upstream docs/Simulation/Software-RTL-Simulation.rst describes sims/verilator,
RocketConfig, the resulting simulator-chipyard.harness-RocketConfig and its
run-binary target. The documented elaboration estimate is about 6.5 GB RAM; the
current environment has a 32 GiB cgroup memory limit and 47 GiB free workspace
at inspection. These are readiness observations, not a successful build.

Upstream project/build.properties pins SBT 1.8.2. The selected GCD workaround
using isolated SBT 1.10.7 is not a validated full-build migration. Validate a
compatible Java/SBT combination or an explicit reviewed patchset before expanding
the profile. Native full-board boot, firmware ABI/loading, timer/UART/PLIC tests,
timeouts and source/build/ELF provenance are required before adding a workspace
target or checking the full Rocket criterion.

## OpenTitan silver-v5

The source pin is ed044fc9760bdf9fc075d0015ba1db07fa075355. This revision uses
the FuseSoC core lowrisc:systems:chip_earlgrey_verilator and Meson firmware flow,
documented in doc/ug/getting_started_verilator.md. Do not substitute current
OpenTitan Bazel instructions for this older pinned source. FuseSoC is not yet
installed in the prepared environment.

The full top includes Ibex and has separate ROM, flash and OTP image inputs.
Its documented simulation boots ROM then flash; UART logging is available via
UARTDPI_LOG_uart0. RV32 firmware and its software toolchain/ABI need their own
contract; the current RV64 Zephyr/Linux ELF cannot stand in for that acceptance.
OTP inputs must use public synthetic development fixtures, never company secrets.

Before acceptance, build the actual top and exact firmware/images, capture ROM
to flash boot, UART and GPIO/PLIC/timer tests, then test reset, bad/missing images,
timeouts and process cleanup. Version every image and source/tool identity. A
functional boot does not establish secure-boot, lifecycle or silicon signoff.

Neither full SoC is currently implemented or verified. Both remain open in the
milestone ledger; successful selected-IP traces do not close their criteria.
