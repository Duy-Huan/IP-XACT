"""Backend-independent, immutable, byte-addressed hardware IR."""

from dataclasses import asdict, dataclass


class ContractError(ValueError):
    """Invalid input or unsupported hardware/model contract."""


@dataclass(frozen=True)
class Window:
    name: str
    base: int
    size: int
    width: int


@dataclass(frozen=True)
class Instance:
    name: str
    vlnv: str
    windows: tuple[Window, ...]


@dataclass(frozen=True)
class SoCIR:
    schema_version: str
    vlnv: str
    instances: tuple[Instance, ...]
    sources: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict:
        return asdict(self)
