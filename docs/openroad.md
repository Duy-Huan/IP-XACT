# OpenROAD reference RTL-to-layout flow

The platform runs OpenROAD-flow-scripts locally through Docker. The implemented
path is RTL + SDC → Yosys technology mapping → floorplan/PDN → placement → CTS →
global/detailed routing → extraction/final timing → KLayout GDS merge. It supports
an explicitly supplied elaborated Verilog/SystemVerilog top file and constraints,
or the verified Chipyard GCD leaf from the existing source-model bundle.

The reference technology is public Nangate45. This validates the tool flow; it is
not a production PDK, manufactured chip or foundry signoff. The complete AI_SOC and
AIZ_SOC RTL tops and synthesizable interconnects remain open requirements. Their
Garnet experiment is a simulation model and cannot be synthesized into a NoC.

## Pinned tools and local execution

The toolchain is pinned to the upstream public openroad/orfs OCI index digest
688272c2cca337c25e98437ae0e2dc919f1bb659d8e488427c52d97ae903e507. The setup
script downloads crane v0.20.2 from its official release and verifies a fixed
archive SHA256. It exports the pinned OCI filesystem and imports one flat Docker
layer. This avoids excessive intermediate-layer duplication with the managed
daemon's VFS driver. Original OCI provenance and the resulting immutable local
image ID are recorded separately; flattening changes image metadata/identity.

No storage driver, daemon configuration, host networking or privilege settings
are changed. The build container runs as the task user with networking disabled
and mounts only its job directory. Tracked checkouts stay unchanged. Flow/platform
assets and their notices are copied from the pinned image to the ignored build
workspace. SDC is Tcl source: use trusted constraint files, not commands extracted
from untrusted documents.

The selected image reports OpenROAD's version banner as unknown and Yosys as
0.68+post with an unknown source revision. Its exact contents are pinned by OCI
digest/local image identity; no unobserved source commit or release is claimed.
The independent generic synthesis/equivalence profile still pins native Yosys 0.52.

## Build and verify

Activate the Python environment and confirm that the managed local Docker socket
is healthy. Setup is explicit; regular verification does not download tools.

```bash
source .venv/bin/activate
bash scripts/setup_openroad.sh

# Actual Chipyard source-model bundle must already be verified/built.
usp asic build --toolchain build/openroad-tools \
  --models build/source-ip-models --out build/asic-gcd-run-001
usp asic verify build/asic-gcd-run-001

# An explicit, self-contained RTL file and its constraints:
usp asic build --toolchain build/openroad-tools \
  --rtl /path/to/top.sv --top top --sdc /path/to/top.sdc \
  --out build/asic-top-run-001
```

Output directories must be new. The current recipe uses Nangate45, automatic
floorplanning at 40% core utilization, two tool threads and the reference GCD PDN
strategy. Designs requiring macros, other PDNs/IO/technology, multiple files or
additional package/include dependencies need an extended validated recipe. A
missing or incompatible input fails; it is not replaced by a blackbox success.

The result includes the original input/constraints, make configuration, raw flow
log, final netlist, DEF/ODB, SPEF/SDC and GDS, plus per-stage raw metrics and JSON/
Markdown reports. Verification checks declared artifact hashes, reference timing
and detailed-router DRC acceptance. It rejects modified GDS, negative setup/hold
slack, nonzero TNS/router DRC, and false full-system/foundry-signoff claims.
Hashes detect changes; they are not a signed attestation.

## Observed reference result, 2026-11-12

The actual GCDMMIOChiselModule generated from pinned Chipyard classes completed
the entire reference flow and produced 6_final.gds. The preserved successful run
is build/asic-gcd-004; earlier failed setup experiments remain available locally.
It reports detailedroute__route__drc_errors=0, setup TNS=0, hold TNS=0,
setup worst slack=9.1003 ns and hold worst slack=0.111771 ns against the 10 ns
reference clock. Standard-cell area is 1496.25 square micrometres and die area is
3960.81 square micrometres. These are reference-library tool outputs for one leaf,
not calibrated whole-SoC power/performance/area figures.

GDS SHA256: 6b9ab6f205179fe10fb8df63632f5e357de73b87c9cdb77195e0b2ddb66ba4a6.
Detailed-router DRC is not a foundry DRC/LVS/antenna or manufacturing signoff deck.
Post-mapping/physical equivalence, complete power/clock constraints, IO/macro plans,
full-chip DRC/LVS and release acceptance remain additional exit criteria. The
separate native Yosys equivalence gate verifies the source GCD and generic netlist.

## System integration and regression

```bash
usp system test systems/AI_SOC --generated build/AI_SOC \
  --models build/source-ip-models --noc-build build/garnet \
  --asic-build build/asic-gcd-004 --scope bringup --out results/ai-flow-001
usp system verify results/ai-flow-001
```

The optional asic-reference stage checks that the physical result matches the
current verified GCD model/RTL, snapshots its declared artifacts and original log,
and adds them to the system evidence inventory. AIZ_SOC uses the same verified
leaf layout and its own actual firmware/map/NoC tests; this is not a second full
chip physical implementation. Full-system scope still returns BLOCKED while
heterogeneous CPU/IP/NoC/RTL/ASIC criteria remain open.

The separate generic RTL/SDC path was also exercised locally with the original
128-bit physical_counter fixture, producing a GDS and passing the same reference
timing/router-DRC checks. The manual physical-reference workflow uses this public
fixture and uploads only its JSON/Markdown summary; hosted execution is unobserved.

Local promotion requires USP_ASIC_BUILD_DIR and sets USP_REQUIRE_ASIC=1. The native
test checks the actual GDS/timing and rejects evidence tampering. Hosted physical
execution must be observed independently; no hosted success is claimed here.

Incomplete native runs can be continued explicitly with `usp asic build` using
`--resume --timeout 7200` and the same RTL/top/SDC/frontend/memory budget and
immutable toolchain. The runner requires identical copied RTL, SDC, configuration,
working directory and image identity, rejects completed outputs and confirms the
previous container stopped. A per-output lock prevents concurrent writers. It
retains prior command/log evidence, restores flow scripts from the pinned image
and lets upstream Make reuse intermediate outputs. Timing/DRC/artifact acceptance
is unchanged; a timeout or intermediate route is never a pass. Timeout values
are bounded to 60–14400 seconds; the default is 3600. The full RTL test-chip
script uses a 7200-second budget and accepts explicit USP_RESUME_ASIC=1 for
matching incomplete outputs.
