# Generated RV64 machine and Zephyr acceptance

The original synthetic IP-XACT 2022 design declares seven instances: RV64 hart,
RAM, boot ROM, NS16550 UART, CLINT, PLIC and SiFive test finisher. The version-2
simulation manifest references a strict hardware overlay with CPU/boot/IRQ/address
space contracts. Unsupported CPU modes, ambiguous bindings and invalid graphs fail.

| Target | Address | Size / contract |
|---|---|---|
| ROM / CPU reset | 0x1000 | 4 KiB, generated reset trampoline |
| RAM / firmware entry | 0x80000000 | 128 MiB |
| UART | 0x11000000 | 256 bytes; byte stride; PLIC source 10 |
| CLINT | 0x02000000 | 64 KiB; MSIP 3, MTIP 7; 10 MHz timebase |
| PLIC | 0x0c000000 | 64 MiB; M/S contexts; external lines 11/9 |
| Finisher | 0x00100000 | Success 0x5555; failure 0x13333 |

UART is deliberately relocated from upstream virt. The generated QEMU C machine
instantiates actual upstream models, maps RAM/ROM, connects UART to PLIC and creates
CLINT software/timer interrupts. There are no implicit virt PCI/virtio devices.
The selected CPU model supplies the required RV64IMAC/Zicsr/Zifencei instructions,
M/S/U and MMU capability; this is a required subset, not an exhaustive enabled-ISA
or microarchitecture equivalence claim. One hart and fixed model window sizes are
supported. Topology role names are currently constrained by the adapter.

## Reproduce

Install the source-build prerequisites in docs/build-reproducibility.md. Activate
`.venv` and any user-owned tool environment before these commands.

```bash
uv sync --frozen --extra dev --extra bringup
bash scripts/fetch_upstreams.sh
export CROSS_COMPILE=riscv64-unknown-elf-
bash scripts/bringup.sh
```

The helper uses clean, pinned public QEMU/Zephyr checkouts and generates a derived
QEMU tree under build/ without modifying the original checkout. `west` uses the
pinned Zephyr source with an external generated board, the upstream compatible
RISC-V SoC support, and the explicitly selected cross compiler. No Zephyr SDK or
all-project west update is needed for this tested app.

Equivalent individual commands:

```bash
usp ipxact validate examples/rv64-minimal
usp generate examples/rv64-minimal --out build/rv64-minimal
usp models explain examples/rv64-minimal --instance uart
usp build examples/rv64-minimal --generated build/rv64-minimal --source-qemu .tools/upstream/qemu
usp software build examples/rv64-minimal --os zephyr --generated build/rv64-minimal \
  --source-zephyr .tools/upstream/zephyr --cross-compile riscv64-unknown-elf-
usp test examples/rv64-minimal --generated build/rv64-minimal --out results/zephyr-001
usp report results/zephyr-001 --format md
```

Expected actual serial markers: USP_ZEPHYR_BOOT_OK, USP_TIMER_IRQ_OK,
USP_UART_PLIC_IRQ_OK, USP_TEST_PASS. The timer callback must run in ISR context;
the UART driver enables TX-empty interrupts, receives the callback through PLIC,
and disables the interrupt. Success also requires QEMU exit zero via the finisher.
Host-only output cannot meet these guest assertions. Fixed marker checks apply to
this conformance workload; this is not a general OS-test language.

Generated files and compiled artifacts carry independent checksums. Changed inputs,
templates, registry entries or modified outputs must be regenerated/rebuilt. Use a
fresh output directory rather than overwriting earlier evidence. Simulator runtime
is bounded and failed/timeout outcomes remain failures. Reports record functional
fidelity; no simulated cycle or cross-backend equivalence claim is made.
