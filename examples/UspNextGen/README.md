# UspNextGen: Next-Generation USP Multi-Platform SoC Showcase

| Metadata Field | Value |
|---|---|
| **Document ID** | `SOCSIM-EX-NEXTGEN-001` |
| **Current Version** | `0.3.0a5` |
| **Status** | Active / Official Showcase Example |
| **Normative Authority** | `examples/UspNextGen/usp_nextgen_soc.yaml`, `upgrade/usp-v2-core-upgrades.md`, `tests/test_usp_v2_upgrades.py` |

---

## Document Revision History

| Version | Release Date | Milestone / Gate | Author / Scope | Summary of Changes |
|---|---|---|---|---|
| `0.3.0a5` | `2026-11-24` | USP Core Upgrade Batch (3 Upgrades) | UspNextGen Example | Created official `UspNextGen` example exercising real QEMU CPU Master, `usp-bus-v2` Reverse DMA + 64-bit Burst + Quantum Sync alongside `usp-mmio32-v1`, and automatic pin-level Chipyard AXI4 Full / AMBA AXI4-Lite / OpenTitan TL-UL Verilator RTL adapters. |

---

## Overview

`examples/UspNextGen/` demonstrates all three USP v2 core upgrades running live together in a single heterogeneous 12-IP, 2-clock-domain (`core_clk 800 MHz` ↔ `periph_clk 250 MHz`), 3-crossbar (`tilelink_xbar`, `axi4_full_xbar`, `tlul_periph_xbar`) SoC:

1. **Real QEMU (`qemu-system-riscv64` TCG/KVM) Direct CPU Master:** Executes a >2.5M-instruction bare-metal RV64 workload (`sw/usp_nextgen_sw.c`) directly in zero-copy shared `mmap` RAM while trapping SoC MMIO and periodic CLINT quantum time-sync barriers over VirtIO-MMIO v2 `usp-bus-v2` into SystemC `MultiFabricInterconnect`.
2. **`usp-bus-v2` Live Plugin + `usp-mmio32-v1` Backward Compatibility:**
   - `dma_burst_v2_0` (`packages/dma_burst_v2/package.yaml`) executes 64-bit bursts, receives `USP_OP_TIME_SYNC` quantum barriers, and autonomously initiates reverse bus-master DMA (`USP_OP_DMA_READ`/`WRITE`) reading and writing QEMU's shared RAM through `MultiFabricInterconnect` with Verilator `l2_cache_rtl` snoop tracking.
   - `polyphase_v1_0` (`../BSoC/packages/polyphase_12t/package.yaml`) runs completely unmodified side-by-side over `usp-mmio32-v1`.
3. **Automatic Pin-Level Verilator RTL Adapters (Zero Manual MMIO32 Shims):**
   - `chipyard_gcd_0` (`rtl/chipyard_gcd_axi4_rtl.sv`): Chipyard/`firtool`-flattened AXI4 Full 64-bit Euclidean GCD FSM + 32×64-bit INCR burst SRAM (`VerilatorAxi4FullAdapter`).
   - `axi4_lite_crc32_0` (`rtl/axi4_lite_crc32_rtl.sv`): Standard AMBA AXI4-Lite 32-bit IEEE 802.3 CRC-32 (`"123456789"` → `0xCBF43926`, `VerilatorAxi4LiteAdapter`).
   - `opentitan_gpio_0` (`rtl/opentitan_gpio_tlul_rtl.sv`): OpenTitan TL-UL 32-bit GPIO & edge-IRQ controller verified with 1-cycle `d_ready = 0` backpressure (`VerilatorTlulAdapter`).

## Reproducible Commands

```bash
# 1. Audit SoC bindings, auto-detected RTL protocols, and plugin wire protocols
usp compose audit examples/UspNextGen/usp_nextgen_soc.yaml

# 2. Build & qualify the usp-bus-v2 plugin standalone (v1 compat + burst64 + quantum + reverse DMA)
usp package build examples/UspNextGen/packages/dma_burst_v2/package.yaml --out /tmp/dma-v2-pkg

# 3. Run UspNextGen live with QEMU CPU Master + Verilator pin-level RTL + v2/v1 plugins
usp compose run examples/UspNextGen/usp_nextgen_soc.yaml --out /tmp/usp-nextgen-run

# 4. Run unmodified BSoC (22 devices) directly with QEMU as CPU Master
usp compose run examples/BSoC/bsoc_composed.yaml --cpu-master qemu --out /tmp/bsoc-qemu-run

# 5. Inspect verbatim measured reports
ls -la examples/UspNextGen/reports/
```

See [`docs/examples/UspNextGen.md`](../../docs/examples/UspNextGen.md) for full measured results and walkthrough.
