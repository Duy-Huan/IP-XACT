"""Scalar register metadata checks and exact executable-model conformance."""

import re

from .domain import ContractError, Register, RegisterField

N = "{http://www.accellera.org/XMLSchema/IPXACT/1685-2022}"
PERMISSIONS = {"read-only": {"read"}, "write-only": {"write"}, "read-write": {"read", "write"}}


def name(node):
    value = node.findtext(N + "name", "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ContractError("E_REGISTER_NAME unsupported identifier")
    return value


def access(node, field=False):
    container, item = (
        ("fieldAccessPolicies", "fieldAccessPolicy")
        if field
        else ("accessPolicies", "accessPolicy")
    )
    policies = node.findall(f"{N}{container}/{N}{item}")
    if len(policies) != 1:
        raise ContractError("E_REGISTER_ACCESS requires one explicit unconditional policy")
    result = policies[0].findtext(N + "access", "").strip()
    if result not in PERMISSIONS:
        raise ContractError("E_REGISTER_ACCESS unsupported access policy")
    return result


def volatile(node):
    return node.findtext(N + "volatile", "false").strip() in {"true", "1"}


def parse_registers(block, number, block_size):
    result = []
    names = set()
    for node in block.findall(N + "register"):
        reg_name = name(node)
        if reg_name in names:
            raise ContractError("E_REGISTER_NAME duplicate register")
        names.add(reg_name)
        offset = number(node.findtext(N + "addressOffset", ""), reg_name)
        size = number(node.findtext(N + "size", ""), reg_name)
        if size not in {8, 16, 32, 64} or offset % (size // 8) or offset + size // 8 > block_size:
            raise ContractError(f"E_REGISTER_RANGE {reg_name}: invalid size/alignment/window")
        reg_access = access(node)
        fields = []
        mask = 0
        field_names = set()
        for field in node.findall(N + "field"):
            field_name = name(field)
            lsb = number(field.findtext(N + "bitOffset", ""), reg_name + "." + field_name)
            width = number(field.findtext(N + "bitWidth", ""), reg_name + "." + field_name)
            if field_name in field_names or width == 0 or lsb + width > size:
                raise ContractError(f"E_FIELD_RANGE {reg_name}.{field_name}")
            field_names.add(field_name)
            bits = ((1 << width) - 1) << lsb
            if mask & bits:
                raise ContractError(f"E_FIELD_OVERLAP {reg_name}.{field_name}")
            mask |= bits
            field_access = access(field, True)
            if not PERMISSIONS[field_access].issubset(PERMISSIONS[reg_access]):
                raise ContractError(
                    f"E_FIELD_ACCESS {reg_name}.{field_name} widens register permissions"
                )
            fields.append(RegisterField(field_name, lsb, width, field_access, volatile(field)))
        if not fields:
            raise ContractError(f"E_REGISTER_FIELDS {reg_name}: missing fields")
        result.append(
            Register(
                reg_name,
                offset,
                size,
                reg_access,
                volatile(node),
                tuple(sorted(fields, key=lambda f: f.lsb)),
            )
        )
        if len(result) > 4096:
            raise ContractError("E_REGISTER_LIMIT exceeds 4096 registers per block")
    result.sort(key=lambda r: r.offset)
    end = 0
    for reg in result:
        if reg.offset < end:
            raise ContractError(f"E_REGISTER_OVERLAP {reg.name}")
        end = reg.offset + reg.size // 8
    return tuple(result)


def verify_model_registers(instance):
    registers = [r for window in instance.windows for r in window.registers]
    if not registers:
        return
    if instance.name != "cygcd":
        raise ContractError(f"UNSUPPORTED declared register contract for model {instance.name}")
    expected = [
        Register(
            "status",
            0,
            32,
            "read-only",
            True,
            (
                RegisterField("output_valid", 0, 1, "read-only", True),
                RegisterField("input_ready", 1, 1, "read-only", True),
            ),
        ),
        Register(
            "x", 4, 32, "write-only", False, (RegisterField("value", 0, 32, "write-only", False),)
        ),
        Register(
            "y", 8, 32, "write-only", False, (RegisterField("value", 0, 32, "write-only", False),)
        ),
        Register(
            "result", 12, 32, "read-only", True, (RegisterField("value", 0, 32, "read-only", True),)
        ),
    ]
    if registers != expected:
        raise ContractError(
            "E_MODEL_REGISTERS declared GCD registers differ from the executable shim/RTL contract"
        )
