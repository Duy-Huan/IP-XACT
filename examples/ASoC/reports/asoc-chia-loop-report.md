# CHIA-Style Cyclic SoC Optimization Loop Report (ASoC)

> **Provenance:** Captured verbatim from GitHub Actions `Podman SDK and OCI release` run `45151702324` (`dev` @ `2fa29af`).

- **Contract:** `usp-chia-loop-1`
- **Status:** **PASSED**
- **Execution Mode:** `live-cyclic-optimization`
- **Iterations Executed:** `4` / `4`
- **Converged:** `True` | **Best Speedup:** `1.1551x`
- **Isolated Oracle SW SHA-256:** `01b6810fa3da66d405e9a93896c03da62d9b173e61a3499a0ead792be7507236`

| Iter | Candidate | Hop/Link | Oracle | Sim Time (ps) | NoC Hops | Speedup | Replayed |
|---|---|---|---|---|---|---|---|
| `1` | `baseline_distributed_2x2` | `10/5` | **PASS** | `396260000` | `19731` | `1.0x` | `False` |
| `2` | `co_locate_crypto_and_npu_near_hart0` | `10/5` | **PASS** | `395359000` | `19611` | `1.0023x` | `False` |
| `3` | `co_locate_accelerators_and_tune_noc_8ns` | `8/4` | **PASS** | `368270000` | `19294` | `1.076x` | `False` |
| `4` | `pareto_low_latency_6ns_noc_balanced` | `6/3` | **PASS** | `343053000` | `19076` | `1.1551x` | `False` |

## Optimization Summary

- Co-locating `mac_npu_0`, `lpu_engine_0`, `sha256_crypto_0`, `aes128_crypto_0`, and `dma_engine_0` near Cluster 0 (`[0,0]`) and tuning NoC hop/link latency from `10/5 ns` to `6/3 ns` reduced simulated runtime from **`396.260 us`** to **`343.053 us`** (**`1.1551x` speedup**) and eliminated **`655` NoC hops** (`19,731 -> 19,076`) while maintaining **100.0% (`30/30` bins) isolated oracle verification** on every iteration.
