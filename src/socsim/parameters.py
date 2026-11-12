"""Bounded unsigned-integer expression subset; never executes input code."""

import ast
import re

from .domain import ContractError

NS = "{http://www.accellera.org/XMLSchema/IPXACT/1685-2022}"
LIMIT = 2**64
TOKEN = re.compile(r"0[xX][0-9a-fA-F]+|[0-9]+|[A-Za-z_][A-Za-z0-9_]*|<<|>>|[()+*/%&|^\-]")


def expression(value, lookup, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ContractError(f"{label}: E_EXPR_SIZE invalid expression")
    tokens = []
    remaining = value.strip()
    while remaining:
        match = TOKEN.match(remaining)
        if not match:
            raise ContractError(f"{label}: E_EXPR_SYNTAX unsupported expression token")
        token = match.group()
        tokens.append(str(int(token)) if token.isdecimal() else token)
        remaining = remaining[match.end() :].lstrip()
    try:
        tree = ast.parse(" ".join(tokens), mode="eval")
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise ContractError(f"{label}: E_EXPR_SYNTAX invalid expression") from exc
    if sum(1 for _ in ast.walk(tree)) > 128:
        raise ContractError(f"{label}: E_EXPR_SIZE expression exceeds 128 nodes")

    def evaluate(node, depth=0):
        if depth > 32:
            raise ContractError(f"{label}: E_EXPR_SIZE expression nesting exceeds 32")
        if isinstance(node, ast.Constant) and type(node.value) is int:
            result = node.value
        elif isinstance(node, ast.Name):
            result = lookup(node.id)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
            result = evaluate(node.operand, depth + 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in {
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.Mod,
            ast.LShift,
            ast.RShift,
            ast.BitAnd,
            ast.BitOr,
            ast.BitXor,
        }:
            left = evaluate(node.left, depth + 1)
            right = evaluate(node.right, depth + 1)
            if isinstance(node.op, (ast.Div, ast.Mod)) and right == 0:
                raise ContractError(f"{label}: E_EXPR_DIV_ZERO division by zero")
            if isinstance(node.op, (ast.LShift, ast.RShift)) and right >= 64:
                raise ContractError(f"{label}: E_EXPR_SHIFT shift outside 0..63")
            operations = {
                ast.Add: lambda: left + right,
                ast.Sub: lambda: left - right,
                ast.Mult: lambda: left * right,
                ast.Div: lambda: left // right,
                ast.Mod: lambda: left % right,
                ast.LShift: lambda: left << right,
                ast.RShift: lambda: left >> right,
                ast.BitAnd: lambda: left & right,
                ast.BitOr: lambda: left | right,
                ast.BitXor: lambda: left ^ right,
            }
            result = operations[type(node.op)]()
        else:
            raise ContractError(f"{label}: E_EXPR_UNSUPPORTED unsupported expression operation")
        if not 0 <= result < LIMIT:
            raise ContractError(f"{label}: E_EXPR_RANGE outside unsigned 64-bit range")
        return result

    return evaluate(tree.body)


def resolve_parameters(component, reference=None):
    definitions = {}
    names = set()
    for node in component.findall(f"{NS}parameters/{NS}parameter"):
        identifier = node.get("parameterId", "")
        name = node.findtext(NS + "name")
        label = f"parameter {identifier} line {node.sourceline}"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier) or identifier in definitions:
            raise ContractError(f"{label}: E_PARAMETER_ID missing/duplicate/unsupported identifier")
        if name in names:
            raise ContractError(f"{label}: E_PARAMETER_ID duplicate name")
        names.add(name)
        if node.get("type") != "longint" or node.get("sign") != "unsigned":
            raise ContractError(f"{label}: E_PARAMETER_TYPE requires explicit unsigned longint")
        if node.get("resolve", "immediate") not in {"immediate", "user"}:
            raise ContractError(f"{label}: E_PARAMETER_RESOLVE generated values unsupported")
        if node.get("resolve", "immediate") != "user" and any(
            k in node.attrib for k in ("minimum", "maximum")
        ):
            raise ContractError(f"{label}: E_PARAMETER_BOUND bounds require resolve=user")
        definitions[identifier] = node
    if len(definitions) > 128:
        raise ContractError("E_PARAMETER_LIMIT exceeds 128 parameters")
    overrides = {}
    if reference is not None:
        for node in reference.findall(
            f"{NS}configurableElementValues/{NS}configurableElementValue"
        ):
            key = node.get("referenceId")
            if key not in definitions or key in overrides:
                raise ContractError(f"E_PARAMETER_OVERRIDE unknown/duplicate reference {key}")
            if definitions[key].get("resolve", "immediate") != "user":
                raise ContractError(f"E_PARAMETER_OVERRIDE {key} is immutable")
            overrides[key] = node.text or ""
    values: dict[str, int] = {}
    stack: list[str] = []

    def resolve(identifier):
        if identifier in values:
            return values[identifier]
        if identifier not in definitions:
            raise ContractError(f"E_PARAMETER_UNRESOLVED unknown reference {identifier}")
        if identifier in stack:
            raise ContractError("E_PARAMETER_CYCLE " + " -> ".join([*stack, identifier]))
        if len(stack) >= 32:
            raise ContractError("E_PARAMETER_LIMIT dependency depth exceeds 32")
        stack.append(identifier)
        node = definitions[identifier]
        label = f"parameter {identifier} line {node.sourceline}"
        result = expression(
            overrides.get(identifier, node.findtext(NS + "value", "")), resolve, label
        )

        # Bounds are literals in this supported subset, avoiding circular constraint semantics.
        def bound_lookup(key):
            raise ContractError(f"{label}: E_PARAMETER_BOUND bounds must be literals")

        for key in ("minimum", "maximum"):
            if key in node.attrib and not re.fullmatch(
                r"(?:0[xX][0-9a-fA-F]+|[0-9]+)", node.get(key, "")
            ):
                raise ContractError(f"{label}: E_PARAMETER_BOUND bounds must be literals")

        minimum = expression(node.get("minimum", "0"), bound_lookup, label)
        maximum = expression(node.get("maximum", str(LIMIT - 1)), bound_lookup, label)
        if minimum > maximum or not minimum <= result <= maximum:
            raise ContractError(f"{label}: E_PARAMETER_BOUND value outside minimum/maximum")
        stack.pop()
        values[identifier] = result
        return result

    for key in sorted(definitions):
        resolve(key)
    return values
