"""Pinned public IP readiness catalog for AI_SOC (46) and AIZ_SOC (37) inventories (M8).

Distinguishes already-qualified models, pinned upstream public RTL awaiting an
explicit bus/driver adapter, and missing external/controller/PHY implementations.
Metadata here never substitutes for an executable model or driver.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .domain import ContractError
from .hybrid import OPENTITAN_SHA
from .ipblocks import CHIPYARD_SHA

READINESS_STATES = frozenset(
    {
        "qualified-native-or-rtl",
        "pinned-source-needs-adapter",
        "missing-source-implementation",
    }
)


@dataclass(frozen=True)
class IpCatalogEntry:
    provider: str
    model: str
    protocol: str
    readiness: str
    upstream_pin: str | None
    upstream_path: str | None
    notes: str


IP_CATALOG: tuple[IpCatalogEntry, ...] = (
    # Qualified public models (executed in native, unified, Rocket, or RTL test-chip flows)
    IpCatalogEntry(
        "chipyard",
        "BootROM",
        "tilelink",
        "qualified-native-or-rtl",
        CHIPYARD_SHA,
        "generators/rocket-chip/src/main/scala/devices/tilelink/BootROM.scala",
        "Qualified boot ROM contract in native/Rocket/test-chip flows.",
    ),
    IpCatalogEntry(
        "chipyard",
        "RocketConfig",
        "axi4-32-pa32",
        "qualified-native-or-rtl",
        CHIPYARD_SHA,
        "generators/rocket-chip",
        "Qualified single-tile Rocket AXI32 in v0.3.0-alpha.5; multi-core SMP RTL remains open.",
    ),
    IpCatalogEntry(
        "chipyard",
        "TLRAM",
        "tilelink",
        "qualified-native-or-rtl",
        CHIPYARD_SHA,
        "generators/rocket-chip/src/main/scala/tilelink/SRAM.scala",
        "Qualified memory region contract in native/Rocket/test-chip flows.",
    ),
    IpCatalogEntry(
        "opentitan",
        "gpio",
        "tilelink-ul-32",
        "qualified-native-or-rtl",
        OPENTITAN_SHA,
        "hw/ip/gpio",
        "Qualified Verilated TL-UL peripheral with guest IRQ acceptance on QEMU & gem5.",
    ),
    IpCatalogEntry(
        "opentitan",
        "rv_core_ibex",
        "obi-rvfi32-v1",
        "qualified-native-or-rtl",
        OPENTITAN_SHA,
        "hw/vendor/lowrisc_ibex",
        "Qualified RV32IMC RTL CPU master and Nangate45 test-chip top.",
    ),
    IpCatalogEntry(
        "opentitan",
        "rv_plic",
        "tilelink-ul-32",
        "qualified-native-or-rtl",
        OPENTITAN_SHA,
        "hw/top_earlgrey/ip_autogen/rv_plic",
        "Qualified PLIC routing contract in native/unified/test-chip flows.",
    ),
    IpCatalogEntry(
        "opentitan",
        "rv_timer",
        "tilelink-ul-32",
        "qualified-native-or-rtl",
        OPENTITAN_SHA,
        "hw/ip/rv_timer",
        "Qualified OpenTitan timer RTL in both 9-component RTL test chips.",
    ),
    IpCatalogEntry(
        "opentitan",
        "uart",
        "tilelink-ul-32",
        "qualified-native-or-rtl",
        OPENTITAN_SHA,
        "hw/ip/uart",
        "Qualified OpenTitan UART RTL in test chips and NS16550 native console contract.",
    ),
    # Pinned public upstream sources awaiting explicit SystemC/TLM/RTL adapter + driver acceptance
    IpCatalogEntry(
        "chipyard",
        "DebugModule",
        "tilelink",
        "pinned-source-needs-adapter",
        CHIPYARD_SHA,
        "generators/rocket-chip/src/main/scala/devices/debug",
        "Upstream Rocket debug module pinned; JTAG/DMI adapter and acceptance test remain open.",
    ),
    IpCatalogEntry(
        "chipyard",
        "inclusive-cache",
        "tilelink",
        "pinned-source-needs-adapter",
        CHIPYARD_SHA,
        "generators/rocket-chip-inclusive-cache",
        "Pinned L2 inclusive cache RTL; coherent multi-core RTL bus integration remains open.",
    ),
    IpCatalogEntry(
        "chipyard",
        "gemmini-candidate",
        "tilelink",
        "pinned-source-needs-adapter",
        CHIPYARD_SHA,
        "generators/gemmini",
        "Pinned Chipyard Gemmini submodule; elaboration, RoCC/DMA adapter and driver remain open.",
    ),
    IpCatalogEntry(
        "chipyard",
        "gemmini-or-vector-candidate",
        "tilelink",
        "pinned-source-needs-adapter",
        CHIPYARD_SHA,
        "generators/gemmini",
        "Pinned accelerator candidate; explicit RoCC/vector selection and driver remain open.",
    ),
    IpCatalogEntry(
        "chipyard",
        "icenet-candidate",
        "tilelink",
        "pinned-source-needs-adapter",
        CHIPYARD_SHA,
        "generators/icenet",
        "Pinned Chipyard IceNet NIC candidate; MAC/DMA adapter and packet tests remain open.",
    ),
    IpCatalogEntry(
        "chipyard",
        "vector-core-candidate",
        "tilelink",
        "pinned-source-needs-adapter",
        CHIPYARD_SHA,
        "generators/saturn",
        "Pinned vector core candidate; RVV elaboration, adapter and workload tests remain open.",
    ),
    IpCatalogEntry(
        "opentitan",
        "aes",
        "tilelink-ul-32",
        "pinned-source-needs-adapter",
        OPENTITAN_SHA,
        "hw/ip/aes",
        "Pinned OpenTitan AES RTL; TL-UL SystemC wrapper and KAT firmware acceptance remain open.",
    ),
    IpCatalogEntry(
        "opentitan",
        "aon_timer",
        "tilelink-ul-32",
        "pinned-source-needs-adapter",
        OPENTITAN_SHA,
        "hw/ip/aon_timer",
        "Pinned OpenTitan AON timer RTL; watchdog/wakeup adapter and tests remain open.",
    ),
    IpCatalogEntry(
        "opentitan",
        "clkmgr",
        "tilelink-ul-32",
        "pinned-source-needs-adapter",
        OPENTITAN_SHA,
        "hw/top_earlgrey/ip_autogen/clkmgr",
        "Pinned OpenTitan clock manager RTL; clock-domain bridge integration remains open.",
    ),
    IpCatalogEntry(
        "opentitan",
        "csrng",
        "tilelink-ul-32",
        "pinned-source-needs-adapter",
        OPENTITAN_SHA,
        "hw/ip/csrng",
        "Pinned OpenTitan CSRNG RTL; entropy-chain adapter and tests remain open.",
    ),
    IpCatalogEntry(
        "opentitan",
        "entropy_src",
        "tilelink-ul-32",
        "pinned-source-needs-adapter",
        OPENTITAN_SHA,
        "hw/ip/entropy_src",
        "Pinned OpenTitan entropy source RTL; deterministic seed harness remains open.",
    ),
    IpCatalogEntry(
        "opentitan",
        "hmac",
        "tilelink-ul-32",
        "pinned-source-needs-adapter",
        OPENTITAN_SHA,
        "hw/ip/hmac",
        "Pinned OpenTitan HMAC/SHA-256 RTL; TL-UL wrapper and digest test remain open.",
    ),
    IpCatalogEntry(
        "opentitan",
        "i2c",
        "tilelink-ul-32",
        "pinned-source-needs-adapter",
        OPENTITAN_SHA,
        "hw/ip/i2c",
        "Pinned OpenTitan I2C RTL; loopback bus-device harness and driver test remain open.",
    ),
    IpCatalogEntry(
        "opentitan",
        "otbn",
        "tilelink-ul-32",
        "pinned-source-needs-adapter",
        OPENTITAN_SHA,
        "hw/ip/otbn",
        "Pinned OpenTitan OTBN bignum accelerator RTL; IMEM/DMEM wrapper remains open.",
    ),
    IpCatalogEntry(
        "opentitan",
        "pwrmgr",
        "tilelink-ul-32",
        "pinned-source-needs-adapter",
        OPENTITAN_SHA,
        "hw/top_earlgrey/ip_autogen/pwrmgr",
        "Pinned OpenTitan power manager RTL; reset/sleep orchestration remains open.",
    ),
    IpCatalogEntry(
        "opentitan",
        "rstmgr",
        "tilelink-ul-32",
        "pinned-source-needs-adapter",
        OPENTITAN_SHA,
        "hw/top_earlgrey/ip_autogen/rstmgr",
        "Pinned OpenTitan reset manager RTL; reset-domain lowering remains open.",
    ),
    IpCatalogEntry(
        "opentitan",
        "spi_host",
        "tilelink-ul-32",
        "pinned-source-needs-adapter",
        OPENTITAN_SHA,
        "hw/ip/spi_host",
        "Pinned OpenTitan SPI host RTL; flash/loopback model and driver test remain open.",
    ),
    IpCatalogEntry(
        "opentitan",
        "usbdev",
        "tilelink-ul-32",
        "pinned-source-needs-adapter",
        OPENTITAN_SHA,
        "hw/ip/usbdev",
        "Pinned OpenTitan USB device RTL; USB PHY/packet harness remains open.",
    ),
    # Missing external / custom controllers, PHYs, and DMA adapters
    IpCatalogEntry(
        "chipyard",
        "memory-controller-required",
        "axi4",
        "missing-source-implementation",
        None,
        None,
        "Requires explicit DDR/LPDDR controller RTL or calibrated timing memory model.",
    ),
    IpCatalogEntry(
        "opentitan",
        "dma-adapter-required",
        "tilelink-ul-32",
        "missing-source-implementation",
        None,
        None,
        "Requires explicit DMA engine RTL/model and bus-master coherence adapter.",
    ),
    IpCatalogEntry(
        "external",
        "algorithm-and-rtl-required",
        "custom-mmio",
        "missing-source-implementation",
        None,
        None,
        "Requires original/public accelerator RTL, behavioral model and driver.",
    ),
    IpCatalogEntry(
        "external",
        "controller-required",
        "custom-mmio",
        "missing-source-implementation",
        None,
        None,
        "Requires explicit controller RTL/model and driver.",
    ),
    IpCatalogEntry(
        "external",
        "controller-and-phy-required",
        "external-phy",
        "missing-source-implementation",
        None,
        None,
        "Requires controller + PHY model/RTL and external interface testbench.",
    ),
    IpCatalogEntry(
        "external",
        "fabric-and-bitstream-required",
        "custom-fabric",
        "missing-source-implementation",
        None,
        None,
        "Requires explicit programmable fabric RTL/model and bitstream loader contract.",
    ),
    IpCatalogEntry(
        "external",
        "phy-and-controller-required",
        "external-phy",
        "missing-source-implementation",
        None,
        None,
        "Requires PHY + controller RTL/model and end-to-end link acceptance.",
    ),
)

_BY_KEY: dict[tuple[str, str], IpCatalogEntry] = {
    (entry.provider, entry.model): entry for entry in IP_CATALOG
}


def lookup_ip(provider: str, model: str) -> IpCatalogEntry:
    """Return the exact catalog entry for a (provider, model) pair."""
    entry = _BY_KEY.get((provider, model))
    if entry is None:
        raise ContractError(f"E_IP_CATALOG unknown model entry: {provider}/{model}")
    return entry


def catalog_summary() -> dict[str, Any]:
    """Return machine-readable counts across all cataloged IP models."""
    counts = {state: 0 for state in sorted(READINESS_STATES)}
    for entry in IP_CATALOG:
        counts[entry.readiness] += 1
    return {
        "total_catalog_models": len(IP_CATALOG),
        "by_readiness": counts,
    }
