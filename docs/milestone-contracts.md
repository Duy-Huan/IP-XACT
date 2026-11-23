# Integrated M1–M9 compiler, package, lifecycle and audit contracts

This document covers the CLI-integrated contract modules added on `dev` to
advance the sequential M1–M9 upgrade programme without weakening any existing
hardware qualification boundary. Contract checks and generated headers never
replace actual simulator, RTL, Yocto, Podman or OpenROAD acceptance.

## M1: Clock/reset domains and per-initiator address views (`usp domains`)

`src/socsim/native_domains.py` validates typed clock/reset domain graphs
(`ClockDomain`, `ResetDomain`, `DomainBinding`, `DomainCrossing`) and
per-initiator address translation views (`InitiatorView`,
`TranslationAperture`, `AddressViewGraph`):

```sh
usp domains derive path/to/native-ir.json
usp domains views path/to/views.yaml --backend native-flat
```

- Rejects clock parent cycles, unknown domains, unbound instances, undeclared
  clock/reset crossings (`E_DOMAIN_CROSSING`) and non-integer synchronous clock
  ratios.
- Rejects overlapping apertures (`E_VIEW_OVERLAP`), width overflow
  (`E_VIEW_OVERFLOW`), target clipping (`E_VIEW_CLIPPING`) and unsupported
  protocols, returning explicit `UNSUPPORTED` blocking reasons before lowering.

## M2: Package capabilities and C register headers (`usp package`)

`src/socsim/native_package.py` decomposes package verification and adds
deterministic capability inspection and register-header synthesis:

```sh
usp package inspect specs/flows/packages/counter/package.yaml
usp package header specs/flows/packages/counter/package.yaml --out build/pkg_regs.h
```

`native_flow.package_driver()` automatically writes `package_regs.h` alongside
`driver_bindings.h` when compiling external package selftest firmware.

## M3 & M4: Flow preflight and IR-derived BSP synthesis (`usp flow`)

`src/socsim/lifecycle_preflight.py` and `src/socsim/native_bsp_gen.py` are wired
into the native flow CLI:

```sh
usp flow preflight specs/bus/AI_SOC-flow.yaml
usp flow bsp path/to/native-ir.json --out build/bsp-bundle
```

- `usp flow preflight` checks CPU/RAM/time ownership, clock/reset domains and
  workload eligibility across YAML, fabric, standard-bus XML, Rocket RTL and
  package flows, forbidding silent ISS substitution.
- `usp flow bsp` deterministically synthesizes `soc_map.h`, `memory.ld`,
  `soc.dts` (1..8 harts), per-IP register headers (`regs_<instance>.h`) and
  `bsp-manifest.json` with explicit eligibility labels (retaining
  `BLOCKED_UPSTREAM_FETCH_POLICY` for Yocto until upstream fetch is enabled).

## M5–M7: CPU/bus adapters, NoC topology and ROI calibration (`usp contracts`)

`src/socsim/v04_contracts.py` exposes explicit v0.4.0 contract checks:

```sh
usp contracts adapters
usp contracts adapters --provider chipyard --model chipyard.rocket_axi32 \
  --protocol axi4-32-pa32 --engine gem5
usp contracts noc path/to/native-ir.json --rows 2 --cols 2 --pio-bypass-disclosed
usp contracts calibrate --roi gcd_kernel --ref-source native-gem5 \
  --model-source qemu-analytical-estimate --ref-cycles 10000 --est-cycles 10800
usp contracts ip-catalog
```

- Planned targets (`chipyard.boom`, `opentitan.earlgrey_full`) fail with
  `E_CPU_ADAPTER` rather than substituting an ISS.
- `usp contracts noc` requires `--pio-bypass-disclosed` (`E_NOC_BYPASS`) and
  marks `synthesizable_rtl_counterpart: false`.
- `usp contracts calibrate` rejects host wall-clock references and labels
  `silicon_signoff: false`.

## M8–M9: Full-inventory and milestone readiness audit (`usp upgrade --audit`)

`src/socsim/ip_catalog.py` and `src/socsim/readiness_audit.py` categorize every
instance in `systems/AI_SOC/system.yaml` (46 instances) and
`systems/AIZ_SOC/system.yaml` (37 instances) into:

1. `qualified-native-or-rtl`
2. `pinned-source-needs-adapter`
3. `missing-source-implementation`

Run `usp upgrade --root . --audit` for a complete machine-readable readiness
snapshot. Final `v0.3.0`, `v0.4.0` and `v1.0.0` release tags remain gated on
actual end-to-end hardware, software, physical and cold/Podman qualification.
