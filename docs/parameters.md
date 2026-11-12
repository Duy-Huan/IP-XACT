# Component integer parameters

The importer now supports a bounded unsigned-integer subset of IEEE 1685-2022
component parameters. Official XSD validation precedes semantic elaboration.
This is not a complete SystemVerilog expression implementation.

Declarations use parameters/parameter with name, value and explicit parameterId,
type="longint", sign="unsigned". Resolve defaults to immediate, which configuration
cannot change. Resolve=user permits per-instance configuration under
componentRef/configurableElementValues/configurableElementValue, with referenceId
pointing to the parameterId. Generated parameters, implicit/string/signed types,
vectors, arrays and other configuration metadata remain unsupported.

Expressions refer to component-local parameter IDs. Forward references are allowed;
unknown identifiers and cycles fail with E_PARAMETER diagnostics. Each instance
gets a separate graph, so overrides never mutate another instance or the original
component. User-resolved parameters can declare minimum/maximum as unsigned
decimal/hex literals. These bounds apply to the final overridden value. Every
declared parameter of an instantiated component is resolved, including unused ones.

Supported operators are parentheses, unary +, binary + - * / %, shifts << >> and
bitwise & | ^. Division truncates toward zero for unsigned operands. Every operand
and intermediate must fit 0..2^64-1. Underflow, overflow, zero divisors and shifts
outside 0..63 fail. Decimal and 0x hexadecimal literals are accepted. Other literal
syntaxes, comparisons, conditionals, calls, attribute/index access, powers and unary
complement are rejected. No eval or input code execution is used. Limits are 1024
characters and 128 AST nodes per expression, 32 expression/dependency levels and
128 parameters per component.

Expressions are accepted in addressBlock baseAddress/range/width and memoryMap
addressUnitBits. The existing model-window, overlap, overflow and byte-addressing
checks still apply. Supplemental manifest placement remains a literal offset.
Design parameters, cross-component references and general hierarchy/translations
are not added by this extension.

The immutable IR emits Instance.parameters as sorted identifier/value pairs, empty
for nonparameterized components. The IR-v2 export schema accepts this additive
field. Exact declarations and overrides remain covered by input source hashes,
and generation manifests additionally hash parameters.py. Existing artifacts need
explicit regeneration/rebuild after this compiler change.

The rv64-minimal UART demonstrates actual model binding: UNIT=128, SPAN defaults
to 1 with bounds 1..8, BITS=8; range is SPAN*UNIT. Its design instance overrides
SPAN to 2, yielding the required 256-byte model window. The unconfigured 128-byte
component default is valid metadata but incompatible with this selected backend.
Regression boots Zephyr, FreeRTOS and Linux on this parameterized design.

```bash
usp validate examples/rv64-minimal
usp elaborate examples/rv64-minimal --out build/parameter-ir.json
```

Tests cover operator safety, bounds, forward references, cycles, unknown references,
per-instance isolation, immutable/unknown overrides and unsupported types. Native
regression additionally requires actual OS/IRQ/model and workspace acceptance.
