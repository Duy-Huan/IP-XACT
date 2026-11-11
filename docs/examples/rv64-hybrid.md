# QEMU + SystemC / OpenTitan FIFO reference

This profile extends rv64-minimal with one 32-bit FIFO register window at
0x12000000, connected to PLIC source 11. The CPU, RAM, UART, timer and PLIC remain
owned by QEMU. The FIFO has an explicit SystemC TLM model or a Verilated OpenTitan
`prim_fifo_sync` implementation selected by the run command. There is no fallback.

The source IP is pinned to public OpenTitan earlgrey_silver_release_v5 commit
ed044fc9760bdf9fc075d0015ba1db07fa075355. Only the standalone synchronous FIFO is
integrated. No OpenTitan lifecycle, OTP, cryptographic or root-of-trust claim is made.
The project owns the wrapper/adapter; the RTL is built directly from the clean
public upstream checkout, with its existing license and notices.

## Register/transaction contract

Four-byte, aligned, little-endian accesses only. Offset 0 writes push and reads pop;
offset 4 reads the nonempty status. Internal reset clears the FIFO through the
bridge. IRQ is level-high while nonempty and deasserts on the last pop. Depth is 4;
underflow, overflow and unknown addresses return transaction errors. The QEMU
adapter fails closed on remote errors, disconnects and two-second I/O timeouts.

Transport uses a local Unix socket with fixed-size little-endian messages and
monotonic sequence IDs. Every transaction goes through real TLM sockets. The
selected RTL model receives actual clock edges and is sampled after evaluation.
Each successful TLM transaction advances annotated SystemC time by 10 ns; the RTL
wrapper uses two edges per operation. This is deterministic transaction-boundary
synchronization, not CPU-cycle correlation. There is no DMA, atomics, multicore
coherency, arbitrary bus-width conversion or checkpoint serialization support.

## Build and run

Requires SystemC 3.0.1 and Verilator 5.032, plus the QEMU/Zephyr prerequisites.
Debian's SystemC 3.0.1 package reports 3.0.0 through pkg-config; the adapter also
checks the actual header and link ABI. The upstream RTL selects dummy SVA macros
under Verilator; the conformance proof uses independent executable assertions and
guest checks. We do not claim execution of the disabled upstream SVA properties.

```bash
usp generate examples/rv64-hybrid --out build/rv64-hybrid
usp build examples/rv64-hybrid --generated build/rv64-hybrid --source-qemu .tools/upstream/qemu
usp software build examples/rv64-hybrid --os zephyr --generated build/rv64-hybrid \
  --source-zephyr .tools/upstream/zephyr --cross-compile riscv64-unknown-elf-
usp models build --out build/fifo-models --native-source native/systemc \
  --source-opentitan .tools/upstream/opentitan
usp test examples/rv64-hybrid --generated build/rv64-hybrid --out results/hybrid-001 \
  --bridge-models build/fifo-models --bridge-mode rtl
```

Use `--bridge-mode tlm` for the behavioral TLM model. The helper starts the model,
waits for its local socket, runs the actual guest and cleans up only processes it
started. `USP_TLM_RTL_IRQ_OK` means the selected model passed guest MMIO/readback/
IRQ; consult report.bridge.mode to distinguish TLM and RTL. Both modes were run
in the current workspace, and repeated RTL runs produced identical raw traces.

`tests/test_models.py` covers FIFO ordering, full/empty errors, invalid offsets,
reset, guest IRQ completion, and deterministic replay in both modes. Enable the
required gate with USP_MODEL_DIR, USP_HYBRID_GENERATED_DIR and USP_REQUIRE_MODELS=1.
