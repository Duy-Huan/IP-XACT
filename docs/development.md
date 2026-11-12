# Development, regression and promotion

The current supported-profile acceptance matrix is workspace.yaml. Run
`usp workspace test workspace.yaml --out results/<new-id>` and verify the retained
bundle using `usp workspace verify`. Promotion additionally sets
USP_REQUIRE_WORKSPACE=1; the real ten-target integration test must pass.
The milestone-checklist.md ledger explicitly retains uncompleted master items.

Use the existing checkout and branch `dev`; do not create a worktree unless the
owner requests one. Run `bash scripts/setup.sh`, then activate `.venv`. The setup
script installs the frozen Python lock and validates the example, without host
package changes. Read `skills/setup/SKILL.md` for simulator/toolchain prerequisites.

`python -m pytest` permits explicit skips when optional runtime tools are missing.
`bash scripts/regression.sh` is the promotion gate: tools are required, static and
documentation checks run, and real QEMU boot, negative guest exit and timeout tests
must pass. Output includes JUnit under ignored `build/`. An expected guest failure
is a passing negative test, not a claim that all guest workloads succeed.

Commit changes on dev, then run `bash scripts/promote.sh` for local promotion. It
requires a clean tree and runs regression before touching main. With no initial
main branch, it creates main at the checked dev commit; subsequent promotions are
non-fast-forward merges. It returns to dev. It never pushes or creates tags.

## GitHub configuration

The workflow `.github/workflows/regression.yml` runs checks on dev/main pushes and
pull requests. Only a same-repository dev→main PR is accepted by branch-policy.
The regression job runs inside Debian trixie with QEMU 10.0.13 and a RISC-V compiler.
It tests original public-safe fixtures only. CI artifacts contain JUnit, not private
input snapshots or simulator source trees. Publishing the YAML does not mean the
hosted workflow has run or branch protection is enabled.

An administrator should configure a ruleset for main: require a PR, required
`branch-policy` and `regression` checks, branches up to date, restrict deletions and
force pushes, and disable admin bypass. Configure dev protection against force
push/deletion as appropriate. Repository credentials/settings permissions are
outside local Git; no protection rule is silently claimed installed.

For updates, use `skills/update/SKILL.md`. Change dependencies and tool profiles on
dev, run regression, document incompatibilities, and promote reviewed candidates.
For release, synchronize version/changelog and create an immutable annotated tag
from verified main. No automatic deployment or package upload is configured.

## Current acceptance gates

The regression script now also runs mypy. The custom-zephyr hosted job rebuilds
the generated QEMU/Zephyr profile and the Linux and hybrid examples from pinned
public sources. It runs synthetic fixtures only and uploads JUnit, not arbitrary
run bundles. Include that check in main protection in addition to the quick jobs.
Hosted completion must be observed separately from local execution.

The same native job also builds the selected Chipyard GCD and OpenTitan GPIO
profile, including Java 21 and content-verified Scala/SBT/CIRCT tooling. Source IP
host conformance and guest/replay acceptance have a separate source-ips JUnit.
Local promotion requires USP_IP_MODELS_DIR and USP_IP_GENERATED_DIR and sets
USP_REQUIRE_IPS=1. Full Chipyard/Rocket and Earlgrey are distinct future targets.

For local promotion export USP_GENERATED_DIR to the built rv64-minimal directory;
promote.sh requires the custom boot/IRQ test rather than allowing it to skip.
Promotion also requires USP_MODEL_DIR, USP_HYBRID_GENERATED_DIR and
USP_GEM5_BUILD_DIR. Linux, model and gem5 gates are mandatory for promotion;
individual developer suites may still skip optional source builds. The source paths
and native model build prerequisites are in the example/reproducibility guides.
Missing required artifacts must fail the selected gate.

FreeRTOS acceptance is also mandatory for promotion. Build it with
scripts/freertos_bringup.sh and export USP_FREERTOS_SOURCE to the exact clean kernel
checkout; regression compiles real assertion-failure and tick-disabled guests.
The custom-zephyr hosted job now builds and runs this additional BSP with the
pinned kernel, preserving separate JUnit results.

The expensive gem5 source-build job is available through workflow_dispatch; it is
not silently reported as executed by the quick push/PR jobs. Local promotion still
requires its actual shared-workload integration test with a verified build.

AI_SOC/AIZ_SOC bring-up adds USP_REQUIRE_DUAL_SOC, USP_DUAL_BUILD_ROOT and
USP_NOC_BUILD_DIR. Local promotion also requires USP_ASIC_BUILD_DIR and sets
USP_REQUIRE_ASIC=1, checking the actual reference GCD GDS/timing/artifact evidence.
The separate physical-reference workflow is manually dispatched on a host runner
with Docker and uses an original public RTL counter, not company source. Hosted
completion remains unobserved. See docs/openroad.md for the immutable tool setup.

Both functional RTL test chips are also mandatory in local promotion. Export
USP_RTL_CHIP_ROOT, USP_CHIP_PHYSICAL_ROOT and USP_GATE_CHIP_ROOT to directories
containing AI_SOC and AIZ_SOC builds; promotion sets USP_REQUIRE_TESTCHIP=1.
The test executes actual RTL and post-route binaries twice, compares serial
traces, rejects a short cycle limit and verifies physical/evidence identities.
See rtl-testchips.md and scripts/rtl_testchip_flow.sh for preparation. The manual
rtl-testchips workflow requires a provisioned soc-eda runner; its presence in Git
does not establish a hosted result.

Unified virtual-SoC promotion also requires USP_UNIFIED_GEM5_BUILD and sets
USP_REQUIRE_UNIFIED=1. Tests run the same Zephyr ELF and actual SystemC GCD/GPIO
models on QEMU and gem5 full-system for rv64-ips, AI_SOC and AIZ_SOC bring-up maps.
They compare real IP command/data sequences and reject a short gem5 simulation
limit. See unified-soc.md for the supported extension build and ownership contract.
