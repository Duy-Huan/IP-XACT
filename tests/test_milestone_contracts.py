"""Positive and negative contract tests for refactored M1-M9 modules."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from socsim.domain import ContractError
from socsim.lifecycle_preflight import preflight_design
from socsim.native_bsp_gen import synthesize_bsp_bundle
from socsim.native_domains import (
    ClockDomain,
    DomainBinding,
    DomainCrossing,
    DomainGraph,
    ResetDomain,
    check_lowering_capability,
    derive_domains_from_ir,
    parse_address_views,
    resolve_initiator_views,
    validate_domain_graph,
)
from socsim.native_ipxact import import_design
from socsim.native_package import generate_package_header, package_capabilities, read_package
from socsim.readiness_audit import audit_milestone_readiness, audit_system_inventory
from socsim.v04_contracts import (
    check_cpu_bus_adapter,
    derive_noc_topology,
    evaluate_calibration_report,
)
from socsim.validation import (
    require_bounded_int,
    require_finite_number,
    require_identifier,
    require_sha256,
    require_slug,
    require_unique,
    require_vlnv,
)

ROOT = Path(__file__).resolve().parents[1]


def _sample_native_ir():
    spec_dir = ROOT / "specs/bus/AI_SOC/native.yaml"
    return import_design(spec_dir)[1]


def _sample_rocket_ir():
    spec_dir = ROOT / "specs/rtl/AIZ_SOC-rocket-xml/native.yaml"
    return import_design(spec_dir)[1]


def test_shared_validation_primitives():
    assert require_sha256("a" * 64) == "a" * 64
    assert require_vlnv("vendor:lib:name:1.0") == "vendor:lib:name:1.0"
    assert require_identifier("uart_0") == "uart_0"
    assert require_slug("pkg-1.0") == "pkg-1.0"
    assert require_bounded_int(4, 1, 8, "harts") == 4
    assert require_finite_number(1.5, "cpi", low=0.1, high=10.0) == 1.5
    assert require_unique(["a", "b"], lambda x: x, "item") == {"a": "a", "b": "b"}

    with pytest.raises(ContractError):
        require_sha256("short")
    with pytest.raises(ContractError):
        require_vlnv("bad:vlnv")
    with pytest.raises(ContractError):
        require_bounded_int(True, 0, 10, "bool-not-int")
    with pytest.raises(ContractError):
        require_finite_number(float("nan"), "nan")
    with pytest.raises(ContractError):
        require_unique(["a", "a"], lambda x: x, "item")


def test_m1_domain_graph_validation_and_negative_crossings():
    ir = _sample_native_ir()
    derived = derive_domains_from_ir(ir)
    assert any(c.name == "cpu_clock" for c in derived.clocks)
    assert derived.to_dict()["bindings"]

    # Explicit synchronous-ratio CDC succeeds
    graph = DomainGraph(
        clocks=(
            ClockDomain("fast_clk", 200_000_000),
            ClockDomain("slow_clk", 100_000_000, parent="fast_clk"),
        ),
        resets=(ResetDomain("sys_rst_n", "synchronous", "active-low", "fast_clk", 0),),
        bindings=(
            DomainBinding("cpu0", "fast_clk", "sys_rst_n", ("top", "core")),
            DomainBinding("uart0", "slow_clk", "sys_rst_n", ("top", "periph")),
        ),
        crossings=(DomainCrossing("cpu0", "uart0", "synchronous-ratio"),),
    )
    validated = validate_domain_graph(graph, {"cpu0", "uart0"}, (("cpu0", "uart0"),))
    assert len(validated.crossings) == 1

    # Undeclared crossing fails
    with pytest.raises(ContractError, match="E_DOMAIN_CROSSING"):
        validate_domain_graph(
            DomainGraph(graph.clocks, graph.resets, graph.bindings, ()),
            {"cpu0", "uart0"},
            (("cpu0", "uart0"),),
        )

    # Non-integer synchronous ratio fails
    bad_clocks = (ClockDomain("c1", 100_000_000), ClockDomain("c2", 33_000_000))
    with pytest.raises(ContractError, match="integer clock ratio"):
        validate_domain_graph(
            DomainGraph(
                bad_clocks,
                (ResetDomain("rst", "synchronous", "active-low", "c1"),),
                (DomainBinding("a", "c1", "rst"), DomainBinding("b", "c2", "rst")),
                (DomainCrossing("a", "b", "synchronous-ratio"),),
            )
        )

    # Clock cycle fails
    with pytest.raises(ContractError, match="cycle"):
        validate_domain_graph(
            DomainGraph(
                (ClockDomain("a", 10, parent="b"), ClockDomain("b", 10, parent="a")),
                (ResetDomain("rst", "synchronous", "active-low", "a"),),
                (DomainBinding("x", "a", "rst"),),
            )
        )


def test_m1_initiator_views_and_lowering_capability():
    views = parse_address_views(
        {
            "views": [
                {
                    "initiator": "cpu0",
                    "address_bits": 32,
                    "apertures": [
                        {
                            "name": "ram_ap",
                            "local_base": 0x80000000,
                            "physical_base": 0x80000000,
                            "size": 0x100000,
                            "target": "ram0",
                        }
                    ],
                },
                {
                    "initiator": "dma0",
                    "address_bits": 32,
                    "apertures": [
                        {
                            "name": "dma_ram",
                            "local_base": 0x00000000,
                            "physical_base": 0x80000000,
                            "size": 0x100000,
                            "target": "ram0",
                            "protocol": "axi4-lite-32",
                        }
                    ],
                },
            ]
        }
    )
    resolve_initiator_views(views, {"ram0": 0x200000})
    cap = check_lowering_capability(views, backend="native-flat")
    assert cap["status"] == "UNSUPPORTED"
    assert any("axi4-lite-32" in reason for reason in cap["blocking_reasons"])

    # Overlapping apertures rejected
    with pytest.raises(ContractError, match="E_VIEW_OVERLAP"):
        parse_address_views(
            {
                "views": [
                    {
                        "initiator": "cpu0",
                        "address_bits": 32,
                        "apertures": [
                            {
                                "name": "a",
                                "local_base": 0x1000,
                                "physical_base": 0x1000,
                                "size": 0x2000,
                                "target": "t1",
                            },
                            {
                                "name": "b",
                                "local_base": 0x2000,
                                "physical_base": 0x2000,
                                "size": 0x1000,
                                "target": "t2",
                            },
                        ],
                    }
                ]
            }
        )


def test_m2_package_capabilities_and_deterministic_header():
    pkg_path = next(ROOT.glob("specs/**/package.yaml"))
    package, _ = read_package(pkg_path)
    caps = package_capabilities(package)
    assert caps["supports_reset"] is True
    assert caps["supports_bus_error"] is True
    assert caps["supports_irq"] is True
    assert caps["register_count"] == len(package["registers"])
    header = generate_package_header(package)
    assert header == generate_package_header(package)
    assert "#include <stdint.h>" in header
    assert "_OFFSET" in header and "_RESET" in header


def test_m3_lifecycle_preflight_forbids_unsupported_combinations():
    iss_ir = _sample_native_ir()
    ready = preflight_design(iss_ir, engine="both", workloads=["baremetal", "freertos", "linux"])
    assert ready["status"] == "READY"
    assert ready["is_rtl_cpu"] is False

    rtl_ir = _sample_rocket_ir()
    rtl_ok = preflight_design(rtl_ir, engine="both", workloads=["baremetal", "zephyr"])
    assert rtl_ok["status"] == "READY"
    assert rtl_ok["is_rtl_cpu"] is True

    rtl_linux = preflight_design(rtl_ir, engine="qemu", workloads=["linux"])
    assert rtl_linux["status"] == "BLOCKED"
    assert any("RTL-Linux" in issue for issue in rtl_linux["blocking_issues"])


def test_m4_ir_derived_bsp_synthesis(tmp_path):
    ir = _sample_native_ir()
    manifest = synthesize_bsp_bundle(ir, tmp_path / "bsp")
    assert manifest["contract"] == "native-bsp-bundle-1"
    assert (tmp_path / "bsp/soc_map.h").is_file()
    assert (tmp_path / "bsp/memory.ld").is_file()
    assert (tmp_path / "bsp/soc.dts").is_file()
    assert manifest["software_eligibility"]["baremetal"] == "ELIGIBLE"
    assert manifest["software_eligibility"]["yocto_image"] == "BLOCKED_UPSTREAM_FETCH_POLICY"
    assert manifest == synthesize_bsp_bundle(ir, tmp_path / "bsp2")


def test_m5_m6_m7_v04_contracts():
    # M5: qualified adapter succeeds; planned BOOM & Earlgrey fail explicitly
    ok = check_cpu_bus_adapter("chipyard", "chipyard.rocket_axi32", "axi4-32-pa32", "gem5")
    assert ok["status"] == "QUALIFIED_ADAPTER"
    with pytest.raises(ContractError, match="ISS substitution is forbidden"):
        check_cpu_bus_adapter("chipyard", "chipyard.boom", "axi4-64", "gem5")
    with pytest.raises(ContractError, match="ISS substitution is forbidden"):
        check_cpu_bus_adapter("opentitan", "opentitan.earlgrey_full", "tilelink-ul-32", "qemu")

    # M6: IR-derived NoC topology rejects undisclosed PIO bypass
    ir = _sample_native_ir()
    topo = derive_noc_topology(ir, rows=2, cols=2, pio_bypass_disclosed=True)
    assert topo["routers"] == 4
    assert topo["synthesizable_rtl_counterpart"] is False
    with pytest.raises(ContractError, match="E_NOC_BYPASS"):
        derive_noc_topology(ir, rows=2, cols=2, pio_bypass_disclosed=False)

    # M7: ROI calibration enforces native reference and error budget
    report = evaluate_calibration_report(
        roi_name="gcd_kernel",
        reference_source="native-gem5",
        model_source="qemu-analytical-estimate",
        reference_cycles=10_000,
        estimated_cycles=10_800,
        max_relative_error=0.10,
    )
    assert report["within_budget"] is True
    assert report["relative_error"] == 0.08
    assert report["silicon_signoff"] is False
    with pytest.raises(ContractError, match="never wall time"):
        evaluate_calibration_report(
            roi_name="bad_ref",
            reference_source="wall-clock",
            model_source="qemu-analytical-estimate",
            reference_cycles=100,
            estimated_cycles=100,
        )


def test_m8_m9_readiness_audit_and_cli():
    ai = audit_system_inventory(ROOT, "AI_SOC")
    aiz = audit_system_inventory(ROOT, "AIZ_SOC")
    assert ai["total_instances"] == 46
    assert aiz["total_instances"] == 37
    assert (
        ai["qualified_model_matches"]
        + ai["pinned_source_needs_adapter"]
        + ai["missing_source_implementation"]
        == 46
    )
    assert (
        aiz["qualified_model_matches"]
        + aiz["pinned_source_needs_adapter"]
        + aiz["missing_source_implementation"]
        == 37
    )
    assert ai["full_inventory_closed"] is False
    assert aiz["full_inventory_closed"] is False

    audit = audit_milestone_readiness(ROOT)
    assert audit["ready_for_final_v0_3_0"] is False
    assert audit["ready_for_final_v1_0_0"] is False
    assert audit["ip_catalog"]["total_catalog_models"] >= 30

    run = subprocess.run(
        [sys.executable, "-m", "socsim.upgrade", "--root", str(ROOT), "--audit"],
        capture_output=True,
        text=True,
        check=True,
    )
    parsed = json.loads(run.stdout)
    assert parsed["full_design_inventories"]["AI_SOC"]["total_instances"] == 46
    assert parsed["full_design_inventories"]["AIZ_SOC"]["total_instances"] == 37


def test_cli_end_to_end_domains_contracts_package_and_flow(tmp_path):
    from socsim.cli import main as cli_main

    ir = _sample_native_ir()
    ir_path = tmp_path / "native-ir.json"
    ir_path.write_text(json.dumps(ir.to_dict(), indent=2, sort_keys=True) + "\n")

    # usp domains derive & views
    assert cli_main(["domains", "derive", str(ir_path)]) == 0
    views_yaml = tmp_path / "views.yaml"
    views_yaml.write_text(
        "views:\n"
        "  - initiator: cpu0\n"
        "    address_bits: 32\n"
        "    apertures:\n"
        "      - name: ram\n"
        "        local_base: 0x80000000\n"
        "        physical_base: 0x80000000\n"
        "        size: 0x100000\n"
        "        target: ram0\n"
    )
    assert cli_main(["domains", "views", str(views_yaml)]) == 0

    # usp contracts adapters, noc, calibrate, ip-catalog
    assert cli_main(["contracts", "adapters"]) == 0
    assert (
        cli_main(
            [
                "contracts",
                "adapters",
                "--provider",
                "chipyard",
                "--model",
                "chipyard.rocket_axi32",
                "--protocol",
                "axi4-32-pa32",
                "--engine",
                "gem5",
            ]
        )
        == 0
    )
    assert (
        cli_main(
            [
                "contracts",
                "adapters",
                "--provider",
                "chipyard",
                "--model",
                "chipyard.boom",
                "--protocol",
                "axi4-64",
                "--engine",
                "gem5",
            ]
        )
        == 2
    )
    assert cli_main(["contracts", "noc", str(ir_path), "--rows", "2", "--cols", "2"]) == 2
    assert (
        cli_main(
            [
                "contracts",
                "noc",
                str(ir_path),
                "--rows",
                "2",
                "--cols",
                "2",
                "--pio-bypass-disclosed",
            ]
        )
        == 0
    )
    assert (
        cli_main(
            [
                "contracts",
                "calibrate",
                "--roi",
                "gcd",
                "--ref-source",
                "native-gem5",
                "--model-source",
                "qemu-analytical-estimate",
                "--ref-cycles",
                "1000",
                "--est-cycles",
                "1050",
            ]
        )
        == 0
    )
    assert cli_main(["contracts", "ip-catalog"]) == 0

    # usp package inspect & header
    pkg_path = next(ROOT.glob("specs/**/package.yaml"))
    header_out = tmp_path / "pkg_regs.h"
    assert cli_main(["package", "inspect", str(pkg_path)]) == 0
    assert cli_main(["package", "header", str(pkg_path), "--out", str(header_out)]) == 0
    assert header_out.is_file() and "#define USP_" in header_out.read_text()

    # usp flow preflight across real XML, fabric, Rocket RTL, and package flows
    for rel in (
        "specs/bus/AI_SOC-flow.yaml",
        "specs/fabric/AI_SOC-flow.yaml",
        "specs/rtl/AIZ_SOC-rocket-flow.yaml",
        "specs/flows/AI_SOC-package.yaml",
    ):
        assert cli_main(["flow", "preflight", str(ROOT / rel)]) == 0

    # usp flow bsp
    bsp_out = tmp_path / "bsp-out"
    assert cli_main(["flow", "bsp", str(ir_path), "--out", str(bsp_out)]) == 0
    assert (bsp_out / "soc_map.h").is_file()
    assert (bsp_out / "memory.ld").is_file()
    assert (bsp_out / "soc.dts").is_file()
    assert (bsp_out / "bsp-manifest.json").is_file()
