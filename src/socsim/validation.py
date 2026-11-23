"""Shared strict contract validators and canonical JSON helpers."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any, TypeVar

from .domain import ContractError

T = TypeVar("T")
K = TypeVar("K")

SHA256_RE = re.compile(r"[0-9a-f]{64}")
VLNV_RE = re.compile(r"[A-Za-z0-9_.-]+(:[A-Za-z0-9_.-]+){3}")
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SLUG_RE = re.compile(r"[a-z][a-z0-9_.-]{0,63}")


def require_sha256(value: Any, label: str = "SHA-256 digest") -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ContractError(f"invalid {label}")
    return value


def require_vlnv(value: Any, label: str = "VLNV") -> str:
    if not isinstance(value, str) or not VLNV_RE.fullmatch(value):
        raise ContractError(f"invalid {label}")
    return value


def require_identifier(value: Any, label: str = "identifier") -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise ContractError(f"invalid {label}")
    return value


def require_slug(value: Any, label: str = "identifier") -> str:
    if not isinstance(value, str) or not SLUG_RE.fullmatch(value):
        raise ContractError(f"invalid {label}")
    return value


def require_bounded_int(value: Any, low: int, high: int, label: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ContractError(f"invalid {label}: expected integer in [{low}, {high}]")
    return value


def require_finite_number(
    value: Any, label: str, *, low: float | None = None, high: float | None = None
) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ContractError(f"invalid {label}: expected finite number")
    number = float(value)
    if low is not None and number < low:
        raise ContractError(f"invalid {label}: must be >= {low}")
    if high is not None and number > high:
        raise ContractError(f"invalid {label}: must be <= {high}")
    return number


def require_unique(items: Iterable[T], key: Callable[[T], K], label: str) -> dict[K, T]:
    result: dict[K, T] = {}
    for item in items:
        item_key = key(item)
        if item_key in result:
            raise ContractError(f"duplicate {label}: {item_key}")
        result[item_key] = item
    return result


def read_bounded_json(path: Path, *, max_bytes: int = 8 * 1024**2, label: str = "JSON") -> Any:
    if path.stat().st_size > max_bytes:
        raise ContractError(f"{label} exceeds {max_bytes} byte limit")
    return json.loads(path.read_text())


def canonical_json_dict(obj: Any) -> dict[str, Any]:
    return json.loads(json.dumps(asdict(obj)))
