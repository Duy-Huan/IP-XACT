# Canonical native IR and direct IEEE 1685-2022 intake

Application 0.3.0a1 starts M1 of the approved upgrade programme. It adds
native-ir-v3 alongside the unchanged legacy SoCIR API. It is an executable native
contract, not the complete general bus/translation/heterogeneous compiler.

Both native YAML and direct structural IP-XACT inputs converge into the same
immutable NativeIR: per-hart instance/ISA/privilege/MMU/reset/entry and explicit
engine bindings, CPU/RTC clocks, cold CPU reset membership, ordered regions with
exact VLNV/width/register/parameters/hierarchy, boot references, address spaces,
PLIC/CLINT routes, RAM latency, external descriptors and source hashes.

The serialized schema is schemas/native-ir-v3.json. Decoding also reconstructs
and checks the entire qualified graph: unknown clock/reset references, conflicting
hart IDs/contracts, routes, ownership or model windows fail. It does not simply
drop fields to produce a valid old spec. Declared register maps are checked against
the actual supported executable model contract; other register behaviours fail.

After intake, generation and execution use this IR projection. spec.yaml remains
input/audit material; it is not re-read by the native backend. soc-ir.json is a
checked structural compatibility view used by existing reports. Manifests hash
both representations and compiler/schema inputs. Run bundles include the native
IR and compare its hash across both independent engines with the same ELF.

## Direct input

The checked-in original fixtures are specs/ipxact/AI_SOC/native.yaml,
specs/ipxact/AIZ_SOC/native.yaml and specs/ipxact/AI_SOC-hierarchy/native.yaml.
Each directory includes its actual 2022 XML. The last fixture puts two GCD
instances under a structural wrapper while retaining logical aliases and high
physical addresses. They qualify the same 16/12-instance SMP subsets as the YAML
inputs, not the complete 46/37-instance architecture targets.

An overlay has schema_version native-ipxact-1, design under ipxact/, name, CPU,
boot, explicit placements for every hierarchical leaf and ordered bindings.
Each binding has instance/model and an IRQ only where the model requires it.
Optional instance_aliases map hierarchical source paths to logical instance
names. Optional external_models uses the existing checked executable contract.
Runtime CPU bindings must name the declared contiguous homogeneous harts.

Sizes, widths, register declarations and resolved structural parameters come
from the XML, not duplicated window sizes in the overlay. Runtime choices absent
from the supported XML subset stay explicit. Every leaf needs one exact binding.
Unknown XML semantics are rejected by the existing fail-closed importer. General
busInterfaces/abstractions, translations and hierarchical parameter propagation
remain open; structural hierarchy does not imply protocol wiring.

```sh
usp soc validate specs/ipxact/AI_SOC/native.yaml --input-format ipxact
usp soc generate specs/ipxact/AI_SOC/native.yaml --input-format ipxact \
  --engine both --out build/direct-ai-001
usp soc build --generated build/direct-ai-001 --source-qemu .tools/upstream/qemu
usp soc linux-build --generated build/direct-ai-001 --source-linux .tools/upstream/linux \
  --source-opensbi .tools/upstream/opensbi
usp soc accept --generated build/direct-ai-001 --models build/source-ip-models \
  --gem5-build build/native-gem5 --software linux --timeout 180 --out results/direct-ai-001
```

Use a new output directory and provisioned pinned tools/models. The source-based
recipe scripts/native_soc_bringup.sh builds both frontends and the configured
hierarchy before running all four native test modules. Local promotion requires
USP_REQUIRE_NATIVE_IPXACT plus USP_NATIVE_AI_SOC_IPXACT_BUILD,
USP_NATIVE_AIZ_SOC_IPXACT_BUILD and USP_NATIVE_HIERARCHY_IPXACT_BUILD.

## Version and release provenance

The first version-module migration changes the legacy compiler fingerprint;
regenerate affected legacy outputs and rebuild their software. The explicit
legacy build --reuse-qemu option copies a previously compiled engine only after
checking its original manifest, pinned QEMU source, exact generated C and binary
hash. It records the original build identity in a new build record and leaves
original records unchanged. Subsequent version-only changes do not alter the
stable __init__ compiler code; functional changes still invalidate their inputs.

scripts/promote.sh writes a release receipt after the complete required gate and
main promotion. An application tag is annotated with that receipt, including
version, tested candidate/tree, promoted commit, required native flags and
compressed/checksummed actual JUnit. python -m socsim.release check-tag validates
these identities and extracts the JUnit. Default verification requires origin/main;
--main-ref main is for checking a local promoted tag before an atomic publication.
No future version tags or incomplete/skipped qualification are accepted.

The application-release workflow runs a separate hosted minimal regression,
builds and installs a wheel in an isolated environment, then publishes source
assets and distinct local/hosted-minimal evidence. Full hosted native, a cold
all-engine rebuild and new rootless Podman/OCI acceptance are not inferred.

## Package and software lifecycle binding

The 0.3.0a2 flow retains native IR v3 as the checked hardware authority. Its plan
records unresolved package compilation without inventing a binary digest. After
source compilation, package/build/driver/license locks bind the actual executable
identities into the generated native IR and flow-build.json. BSPs and driver
bindings use verify_generated/read_ir, not raw source YAML. See native-flow.md.
General interfaces/translations and heterogeneous execution remain open.
