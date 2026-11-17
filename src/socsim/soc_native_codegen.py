"""Native topology lowering; CPU identity, address cells and boot are design data."""

import json

from .soc_spec import devices


def machine_topology(template, spec):
    harts = spec["cpu"]["harts"]
    return (
        template.replace("machine->smp.cpus != 1", f"machine->smp.cpus != {harts}")
        .replace('"num-harts", 1,', f'"num-harts", {harts},')
        .replace("$clint_base, 0, 1, false", f"$clint_base, 0, {harts}, false")
        .replace("0x8000, 0, 1,", f"0x8000, 0, {harts},")
        .replace('"MS", 1, 0, 31, 7', f'"{",".join(["MS"] * harts)}", {harts}, 0, 32, 7')
        .replace("mc->max_cpus = 1;", f"mc->max_cpus = {harts};")
        .replace("one hart", "one hart" if harts == 1 else f"{harts} harts")
    )


def emit_dts(spec):
    def cells(value):
        return f"{hex(value >> 32)} {hex(value & 0xFFFFFFFF)}"

    harts = spec["cpu"]["harts"]
    console = next(d for d in spec["devices"] if d["name"] == spec["boot"]["console"])
    finish = devices(spec, "finisher")[0]["name"]
    dts = [
        "/dts-v1/;",
        "/ { #address-cells = <2>; #size-cells = <2>;",
        f'model = "{spec["name"]}"; compatible = "socsim,native-v1";',
        f"chosen {{ stdout-path = &{console['name']}; "
        f'bootargs = "console=ttyS0,115200 earlycon=uart8250,mmio,{hex(console["base"])} '
        'rdinit=/init"; };',
        "cpus { #address-cells = <1>; #size-cells = <0>;",
        f"timebase-frequency = <{spec['cpu']['timebase_hz']}>;",
    ]
    for hart in range(harts):
        dts += [
            f'cpu@{hart:x} {{ device_type = "cpu"; reg = <{hart}>; status = "okay"; '
            'compatible = "riscv";',
            'riscv,isa = "rv64imac_zicsr_zifencei"; mmu-type = "riscv,sv39";',
            'riscv,isa-base = "rv64i"; '
            'riscv,isa-extensions = "i", "m", "a", "c", "zicsr", "zifencei";',
            f"__usp_hlic{hart}: interrupt-controller {{ #interrupt-cells = <1>; "
            '#address-cells = <0>; interrupt-controller; compatible = "riscv,cpu-intc"; }; };',
        ]
    dts.append("};")
    compatibles = {
        "ram": '"mmio-sram"',
        "rom": '"mtd-rom"',
        "uart": '"ns16550a"',
        "clint": '"riscv,clint0"',
        "plic": '"sifive,plic-1.0.0", "riscv,plic0"',
        "finisher": '"sifive,test1", "syscon"',
        "chipyard.gcd": '"socsim,cygcd"',
        "opentitan.gpio": '"socsim,otgpio"',
    }
    compatibles.update({k: json.dumps(d["compatible"]) for k, d in spec["external_models"].items()})
    plic = devices(spec, "plic")[0]["name"]
    for d in spec["devices"]:
        kind = d["model"]
        boot_ram = d["name"] == spec["boot"]["ram"]
        node = (
            f"{d['name']}: {'memory' if boot_ram else 'device'}@{d['base']:x} {{ "
            f"compatible = {compatibles[kind]}; "
            f"reg = <{cells(d['base'])} {cells(d['size'])}>;"
        )
        if boot_ram:
            node += 'device_type = "memory";'
        if "irq" in d:
            node += f"interrupt-parent = <&{plic}>; interrupts = <{d['irq']}>;"
        if kind == "plic":
            node += (
                "#interrupt-cells = <1>; #address-cells = <0>; interrupt-controller; "
                "riscv,ndev = <31>; riscv,max-priority = <7>; interrupts-extended = <"
                + " ".join(f"&__usp_hlic{h} 11 &__usp_hlic{h} 9" for h in range(harts))
                + ">;"
            )
        if kind == "clint":
            node += (
                "interrupts-extended = <"
                + " ".join(f"&__usp_hlic{h} 3 &__usp_hlic{h} 7" for h in range(harts))
                + ">;"
            )
        if kind == "uart":
            node += "clock-frequency = <3686400>; current-speed = <115200>; reg-shift = <0>;"
        dts.append(node + "};")
    dts += [
        'poweroff { compatible = "syscon-poweroff"; '
        f"regmap = <&{finish}>; offset = <0>; value = <0x5555>; }};",
        "};",
        "",
    ]
    return "\n".join(dts)
