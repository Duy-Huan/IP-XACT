import pytest

from socsim.domain import ContractError
from socsim.rocket_build import address_map_contract, ram_contract


@pytest.mark.parametrize("base,size", [(0, 4096), (0x80000000, 128 * 1024**2), (0, 2**32)])
def test_rocket_ram_boundaries_are_explicit(base, size):
    assert ram_contract(base, size) == {"base": base, "size": size}


@pytest.mark.parametrize(
    "base,size",
    [
        (True, 4096),
        (0, True),
        (-1, 4096),
        (1, 4096),
        (0, 4095),
        (0, 12288),
        (2**32, 4096),
        (0, 2**32 + 4096),
    ],
)
def test_unsupported_ram_cannot_be_silently_truncated(base, size):
    with pytest.raises(ContractError):
        ram_contract(base, size)


def test_pma_map_preserves_regions_and_leaves_unmapped_holes():
    ram = ram_contract(0x80000000, 128 * 1024**2)
    regions = [
        {"base": 0x11000000, "size": 4096, "kind": "mmio"},
        {"base": 0x1000, "size": 4096, "kind": "rom"},
    ]
    result = address_map_contract(ram, regions)
    assert [x["base"] for x in result] == [0x1000, 0x11000000]
    assert all(x["base"] > 0 for x in result)
    assert result[0]["kind"] == "rom"


@pytest.mark.parametrize(
    "regions",
    [
        [],
        [{"base": 0, "size": 4096, "kind": "unknown"}],
        [{"base": 0, "size": 4096, "kind": "rom", "ignored": True}],
        [{"base": 0, "size": 0, "kind": "rom"}],
        [{"base": 2**32 - 4096, "size": 8192, "kind": "rom"}],
        [{"base": 0, "size": 8192, "kind": "rom"}, {"base": 4096, "size": 8192, "kind": "mmio"}],
        [{"base": 0x80000000, "size": 4096, "kind": "mmio"}],
    ],
)
def test_invalid_or_overlapping_pma_is_rejected(regions):
    with pytest.raises(ContractError):
        address_map_contract(ram_contract(0x80000000, 128 * 1024**2), regions)
