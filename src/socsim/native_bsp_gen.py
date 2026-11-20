"""Unified IR-derived BSP, linker, DTS and register-header synthesis (M4)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .domain import ContractError
from .ipxact import digest
from .native_ir import NativeIR
from .runner import write_json


def _c_ident(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")


def _cells64(value: int) -> str:
    return f"{value >> 32:#x} {value & 0xFFFFFFFF:#x}"


def _reg64(base: int, size: int) -> str:
    return f"<{_cells64(base)} {_cells64(size)}>"


def render_soc_map_header(ir: NativeIR) -> str:
    """Render deterministic C memory/IRQ/clock header directly from NativeIR."""
    cpu_clk = next(c.frequency_hz for c in ir.clocks if c.name == ir.cpus[0].clock)
    lines = [
        "#ifndef USP_IR_SOC_MAP_H",
        "#define USP_IR_SOC_MAP_H",
        "#include <stdint.h>",
        f"/* Synthesized from NativeIR v{ir.schema_version}: {ir.name} ({ir.vlnv}) */",
        f"#define USP_HART_COUNT {len(ir.cpus)}U",
        f"#define USP_CPU_CLOCK_HZ {cpu_clk}ULL",
        f"#define USP_TIMEBASE_HZ {ir.cpus[0].contract.timebase_hz}ULL",
        f"#define USP_BOOT_ENTRY 0x{ir.boot.entry:016X}ULL",
    ]
    for region in sorted(ir.regions, key=lambda r: (r.instance.windows[0].base, r.instance.name)):
        ident = _c_ident(region.instance.name)
        win = region.instance.windows[0]
        lines.append(f"#define USP_{ident}_BASE 0x{win.base:016X}ULL")
        lines.append(f"#define USP_{ident}_SIZE 0x{win.size:016X}ULL")
        if region.irq is not None:
            lines.append(f"#define USP_{ident}_IRQ {region.irq}U")
    lines.append("#endif /* USP_IR_SOC_MAP_H */")
    return "\n".join(lines) + "\n"


def render_linker_script(ir: NativeIR) -> str:
    """Render deterministic RAM/ROM linker memory map from NativeIR boot contract."""
    ram = next((r for r in ir.regions if r.instance.name == ir.boot.ram), None)
    rom = next((r for r in ir.regions if r.instance.name == ir.boot.rom), None)
    if ram is None or rom is None:
        raise ContractError("E_BSP_GEN boot RAM or ROM region not found in IR")
    ram_win = ram.instance.windows[0]
    rom_win = rom.instance.windows[0]
    if not ram_win.base <= ir.boot.entry < ram_win.base + ram_win.size:
        raise ContractError("E_BSP_GEN boot entry lies outside selected boot RAM")
    ram_len = ram_win.base + ram_win.size - ir.boot.entry
    return (
        f"/* Synthesized from NativeIR v{ir.schema_version}: {ir.name} */\n"
        "MEMORY\n{\n"
        f"    ROM (rx)  : ORIGIN = 0x{rom_win.base:016X}, LENGTH = 0x{rom_win.size:016X}\n"
        f"    RAM (rwx) : ORIGIN = 0x{ir.boot.entry:016X}, LENGTH = 0x{ram_len:016X}\n"
        "}\n"
    )


def render_dts(ir: NativeIR) -> str:
    """Render deterministic Device Tree Source supporting 1..8 homogeneous harts."""
    ram = next(r for r in ir.regions if r.instance.name == ir.boot.ram)
    uart = next(r for r in ir.regions if r.instance.name == ir.boot.console)
    clint = next(r for r in ir.regions if r.model == "clint")
    plic = next(r for r in ir.regions if r.model == "plic")
    ram_w = ram.instance.windows[0]
    uart_w = uart.instance.windows[0]
    clint_w = clint.instance.windows[0]
    plic_w = plic.instance.windows[0]

    cpu_nodes: list[str] = []
    clint_irqs: list[str] = []
    plic_irqs: list[str] = []
    for cpu in ir.cpus:
        h = cpu.hart_id
        cpu_nodes.append(
            f"        cpu@{h} {{\n"
            f'            device_type = "cpu"; reg = <{h}>; status = "okay";\n'
            f'            compatible = "riscv"; riscv,isa = "{cpu.contract.isa}";\n'
            f'            mmu-type = "riscv,{cpu.contract.mmu}";\n'
            f"            intc{h}: interrupt-controller {{\n"
            f'                compatible = "riscv,cpu-intc";\n'
            f"                #address-cells = <0>; #interrupt-cells = <1>; interrupt-controller;\n"
            f"            }};\n"
            f"        }};"
        )
        clint_irqs.append(f"&intc{h} 3 &intc{h} 7")
        plic_irqs.append(f"&intc{h} 11 &intc{h} 9")

    return (
        "/dts-v1/;\n"
        "/ {\n"
        "    #address-cells = <2>; #size-cells = <2>;\n"
        f'    model = "{ir.name}"; compatible = "socsim,native-ir-v{ir.schema_version}";\n'
        "    chosen { stdout-path = &uart0; };\n"
        "    cpus {\n"
        "        #address-cells = <1>; #size-cells = <0>;\n"
        f"        timebase-frequency = <{ir.cpus[0].contract.timebase_hz}>;\n"
        + "\n".join(cpu_nodes)
        + "\n    };\n"
        f"    memory@{ram_w.base:x} {{\n"
        f'        device_type = "memory"; reg = {_reg64(ram_w.base, ram_w.size)};\n'
        "    };\n"
        f"    uart0: serial@{uart_w.base:x} {{\n"
        '        compatible = "ns16550a";\n'
        f"        reg = {_reg64(uart_w.base, uart_w.size)}; clock-frequency = <3686400>;\n"
        f'        interrupt-parent = <&plic>; interrupts = <{uart.irq or 1}>; status = "okay";\n'
        "    };\n"
        f"    clint@{clint_w.base:x} {{\n"
        '        compatible = "riscv,clint0";\n'
        f"        reg = {_reg64(clint_w.base, clint_w.size)};\n"
        f"        interrupts-extended = <{' '.join(clint_irqs)}>;\n"
        "    };\n"
        f"    plic: interrupt-controller@{plic_w.base:x} {{\n"
        '        compatible = "sifive,plic-1.0.0";\n'
        f"        reg = {_reg64(plic_w.base, plic_w.size)};\n"
        "        #address-cells = <0>; #interrupt-cells = <1>; interrupt-controller;\n"
        f"        riscv,ndev = <63>; interrupts-extended = <{' '.join(plic_irqs)}>;\n"
        "    };\n"
        "};\n"
    )


def render_register_headers(ir: NativeIR) -> dict[str, str]:
    """Generate per-instance C register/field headers for every IR region with registers."""
    headers: dict[str, str] = {}
    for region in sorted(ir.regions, key=lambda r: r.instance.name):
        regs = region.instance.windows[0].registers
        if not regs:
            continue
        ident = _c_ident(region.instance.name)
        lines = [
            f"#ifndef USP_REGS_{ident}_H",
            f"#define USP_REGS_{ident}_H",
            "#include <stdint.h>",
            f"/* IR-derived registers for {region.instance.name} ({region.instance.vlnv}) */",
        ]
        for reg in sorted(regs, key=lambda r: r.offset):
            r_ident = _c_ident(reg.name)
            lines.append(
                f"#define USP_{ident}_{r_ident}_OFFSET "
                f"0x{reg.offset:03X}U /* {reg.access}, {reg.size}b */"
            )
            for field in sorted(reg.fields, key=lambda f: f.lsb):
                f_ident = _c_ident(field.name)
                mask = ((1 << field.width) - 1) << field.lsb
                lines.append(f"#define USP_{ident}_{r_ident}_{f_ident}_SHIFT {field.lsb}U")
                lines.append(f"#define USP_{ident}_{r_ident}_{f_ident}_MASK 0x{mask:08X}U")
        lines.append(f"#endif /* USP_REGS_{ident}_H */")
        headers[f"regs_{region.instance.name}.h"] = "\n".join(lines) + "\n"
    return headers


def synthesize_bsp_bundle(ir: NativeIR, out: Path) -> dict[str, Any]:
    """Write deterministic IR-derived BSP artifacts and explicit workload eligibility."""
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {
        "soc_map.h": render_soc_map_header(ir),
        "memory.ld": render_linker_script(ir),
        "soc.dts": render_dts(ir),
        **render_register_headers(ir),
    }
    hashes: dict[str, str] = {}
    for filename, content in sorted(files.items()):
        target = out / filename
        target.write_text(content)
        hashes[filename] = digest(target)

    is_rtl = ir.cpu_binding is not None
    single_hart = len(ir.cpus) == 1
    eligibility = {
        "baremetal": "ELIGIBLE",
        "freertos": "ELIGIBLE" if single_hart else "UNSUPPORTED_SMP_RTOS",
        "zephyr": "ELIGIBLE" if single_hart else "UNSUPPORTED_SMP_RTOS",
        "ucos2": "ELIGIBLE" if single_hart else "UNSUPPORTED_SMP_RTOS",
        "ucos3": "ELIGIBLE" if single_hart else "UNSUPPORTED_SMP_RTOS",
        "opensbi_linux": "UNSUPPORTED_NATIVE_RTL_LINUX" if is_rtl else "ELIGIBLE",
        "yocto_image": "BLOCKED_UPSTREAM_FETCH_POLICY",
    }
    manifest = {
        "contract": "native-bsp-bundle-1",
        "design": ir.name,
        "vlnv": ir.vlnv,
        "ir_schema_version": ir.schema_version,
        "harts": len(ir.cpus),
        "files": hashes,
        "software_eligibility": eligibility,
    }
    write_json(out / "bsp-manifest.json", manifest)
    return manifest
