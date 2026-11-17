# External native MMIO models

Project-local executables bind through the explicit usp-mmio32-v1 contract.
A new model ID/VLNV does not require a compiler branch. Its executable implements
behavior; a register map is not a model. The selected engine owns CPU/RAM; each
peripheral instance owns a process, socket, state, transaction trace and PLIC IRQ.

## Descriptor and instance

    external_models:
      vendor.counter:
        vlnv: vendor:peripherals:counter:1.0
        executable: models/counter-model
        sha256: <actual executable SHA256>
        license: Apache-2.0
        protocol: usp-mmio32-v1
        compatible: vendor,counter
    devices:
      # Include required RAM/ROM/CLINT/PLIC/UART/finisher instances too.
      - {name: counter0, model: vendor.counter, base: 0x190000000, size: 4096, irq: 20}

Executable paths are relative to the spec directory and start with models/.
Resolved paths must remain inside it. Selected programs must exist, be executable
and match their hashes before output creation. They are copied into the generated
bundle and checked by its manifest. IDs/VLNVs/component filenames cannot collide.
Binding records preserve model binary/license provenance.

Generation emits and XSD-validates real parameterized IEEE 1685-2022 XML for the
new VLNV and imports the typed IR. Both engines use the transport adapter.
External models require explicit user firmware; the compiler does not manufacture
peripheral conformance. Linux boot and user-ELF execution have separate suite
labels from generated built-in conformance.

## Wire contract

Launch uses an argument array, never a shell command:

    MODEL_EXECUTABLE SOCKET_PATH TRACE_JSONL

The program listens on the owned Unix stream socket and accepts one connection.
Requests/replies are five unsigned 32-bit little-endian words transferred in full.
Request: magic 0x31505355, sequence, operation, offset, data. Operations: 0=read,
1=write, 2=reset; reset offset/data are zero. Reply: same magic/sequence,
status (zero=success), read value, IRQ mask. Bit zero is the instance's level
interrupt; bit one is unconnected. Masks above three are rejected by the current
transport. The aperture is 4096 bytes;
accesses are aligned 32-bit transactions. DMA, implicit AXI/TileLink semantics,
live checkpointing and global cycle accuracy are unsupported. Socket I/O is
bounded; disconnects, status errors and malformed replies fail the engine.

JSONL rows record actual numeric op, offset, data, value, status and irq.
Acceptance requires real reads/writes and compares deterministic commands/results.
A nondeterministic polling model needs an appropriate conformance workload;
single-engine applications can use usp soc run with --firmware.

## Original executable example

models/peripherals/tlm-counter.cpp has a real SystemC initiator/target socket and
b_transport callback. Register 0 reads/writes a value; writing asserts IRQ.
Register 4 reports pending state; writing 1 clears it. Other offsets return a
bus error. SystemC time advances by annotated transaction delay, not hardware
cycle prediction.

counter-firmware.c uses generated addresses/IRQ: the guest writes 41, takes the
PLIC interrupt, reads the value, acknowledges the source and checks deassertion
before printing USP_EXTERNAL_IRQ_OK and exiting. The second of its two actual
harts waits rather than claiming an additional peripheral test.

    python scripts/native_external_bringup.py --out build/external-001 \
      --source-qemu .tools/upstream/qemu --gem5-build build/native-gem5 --jobs 2

Use frozen Python and provisioned SystemC/pkg-config/C++/RISC-V tools. The helper
records compile commands, source/binary hashes, compiler/SystemC identity, creates
the spec, builds the machine and firmware, then accepts both engines. It does not
download models. Output must be new; optional --reuse-qemu requires a matching
verified build. Company executables and generated bundles remain private ignored
outputs.
