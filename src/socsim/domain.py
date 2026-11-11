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
class CpuContract:
    isa: str
    privilege: str
    mmu: str
    reset_vector: int
    entry: int
    timebase_hz: int


@dataclass(frozen=True)
class InterruptRoute:
    source: str
    target: str
    line: int
    trigger: str


@dataclass(frozen=True)
class AddressSpace:
    name: str
    initiator: str
    targets: tuple[str, ...]


@dataclass(frozen=True)
class SoCIR:
    schema_version: str
    vlnv: str
    instances: tuple[Instance, ...]
    sources: tuple[tuple[str, str], ...]
    cpu: CpuContract | None = None
    interrupts: tuple[InterruptRoute, ...] = ()
    address_spaces: tuple[AddressSpace, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)
