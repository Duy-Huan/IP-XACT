# Scalar registers and executable contracts

The 2022 importer accepts addressBlock/register declarations with explicit names,
byte addressOffset, size, volatile and fields. Register sizes are 8, 16, 32 or
64 bits, naturally aligned and contained within a usage=register block. Each
register must contain fields with explicit bitOffset/bitWidth. Names, overlapping
registers/fields, duplicate names and out-of-range fields are rejected. Numeric
expressions use the same bounded component parameter interpreter as window sizes.
There are at most 4096 registers per block.

Every register requires one unconditional accessPolicies/accessPolicy/access;
every field requires one fieldAccessPolicies/fieldAccessPolicy/access. Only
read-only, write-only and read-write are accepted. Fields may narrow register
permissions, never widen them. volatile defaults to false and is retained in IR
for both levels; it is separate from read/write permissions.

Window.registers holds immutable sorted Register/RegisterField records. The
ir-v2.json schema describes their serialized shape. This is a deliberate additive
IR revision: consumers must accept the new registers member, including empty
arrays. No new release tag has been issued for the development package.

Metadata does not implement device behavior. During custom-profile resolution,
declared registers require a known executable model contract. The selected
Chipyard GCD profile accepts exactly four 32-bit registers:

| Register | Offset | Access | Fields |
|---|---|---|---|
| status | 0 | RO, volatile | output_valid bit 0, input_ready bit 1; RO, volatile |
| x | 4 | WO | value bits 31:0 |
| y | 8 | WO | value bits 31:0 |
| result | 12 | RO, volatile | value bits 31:0, RO, volatile |

The model descriptor, exact VLNV and original shim/RTL remain authoritative for
actual state transitions. A valid XML map that differs from this contract fails
with E_MODEL_REGISTERS. Other custom models currently reject declared registers;
the legacy virt backend rejects them too. Existing projects without declarations
continue using their pinned model contracts. Generic import/validation checks
metadata consistency; executable model checking occurs during resolution/generation.

Generation emits soc_registers.h and its application copy with absolute addresses,
relative offsets and field masks. The real rv64-ips Zephyr guest consumes the GCD
operand/result offsets and input-ready mask, executes four result vectors and
checks PLIC interrupts alongside actual OpenTitan GPIO. Run the commands in
examples/rv64-ips.md, then the full workspace acceptance gate.

Register files, arrays, mode-dependent policies, resets, read/write side effects,
W1C metadata, enumerations and arbitrary behavioral generation remain unsupported.
The actual GPIO source has W1C behavior, but this compiler does not yet elaborate
that behavior from XML. Unsupported standard elements/attributes fail intake;
they are not silently discarded. Generic bus transactions, cache coherency and
security behavior are separate contracts.
