# Parallel DSE & RTL↔TLM Calibration Report (ASoC)

> **Provenance:** Captured verbatim from GitHub Actions `Podman SDK and OCI release` run `45151702324` (`dev` @ `2fa29af`).

- **Contract:** `usp-dse-cluster-1`
- **Status:** **PASSED**
- **Cluster Backend:** `process` (`workers=2`)
- **Execution Mode:** `live-parallel-simulation`
- **Timestamp (UTC):** `2026-11-23T10:06:06.771723+00:00`

| Point | Split | Backend | Hop/Link | Status | Sim Time (ps) | NoC Hops | Replayed |
|---|---|---|---|---|---|---|---|
| `noc_fast_tlm` | `train` | `systemc-tlm` | 5/2 | **PASSED** | `323194000` | `18996` | `False` |
| `noc_nom_tlm` | `train` | `systemc-tlm` | 10/5 | **PASSED** | `396260000` | `19731` | `False` |
| `noc_nom_rtl` | `train` | `verilator-rtl` | 10/5 | **PASSED** | `396369000` | `19736` | `False` |
| `noc_slow_tlm` | `holdout` | `systemc-tlm` | 20/8 | **PASSED** | `495504000` | `19971` | `False` |

## RTL vs. TLM-2.0 Timing Calibration (Nominal 10/5 ns NoC)

- Relative error: `0.000275` (`0.0275%`, budget `<= 0.1`, within budget: **True**)
- Latency monotonicity verified across train & holdout splits: `323.194 us < 396.260 us < 495.504 us`
