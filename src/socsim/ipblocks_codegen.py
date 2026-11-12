"""Hardware/guest lowering for the explicit Chipyard GCD + OpenTitan GPIO profile."""

from pathlib import Path
from string import Template

RESOURCES = Path(__file__).parent / "resources/ipblocks"


def templates():
    return {"ipblocks/" + name: RESOURCES / name for name in ("qemu-bridge.c.in", "guest.c.in")}


def lower(project, out, values):
    gcd = next(i for i in project.ir.instances if i.name == "cygcd")
    # A missing optional declaration retains the pinned, explicit model contract.
    offsets = {"x": 4, "y": 8, "result": 12}
    values["gcd_input_ready_mask"] = "0x2"
    for reg in gcd.windows[0].registers:
        offsets[reg.name] = "USP_CYGCD_" + reg.name.upper() + "_OFFSET"
        if reg.name == "status":
            field = next(f for f in reg.fields if f.name == "input_ready")
            values["gcd_input_ready_mask"] = "USP_CYGCD_STATUS_" + field.name.upper() + "_MASK"
    values.update({"gcd_" + name + "_offset": offset for name, offset in offsets.items()})
    for name in ("cygcd", "otgpio"):
        values[name + "_irq"] = next(r.line for r in project.ir.interrupts if r.source == name)
        binding = out / "zephyr/dts/bindings/misc"
        binding.mkdir(parents=True, exist_ok=True)
        (binding / f"socsim,{name}.yaml").write_text(
            f'description: USP explicit {name} RTL bridge\ncompatible: "socsim,{name}"\n'
            "include: base.yaml\nproperties:\n  reg:\n    required: true\n"
            "  interrupts:\n    required: true\n"
        )
    values.update(
        bridge_code=Template((RESOURCES / "qemu-bridge.c.in").read_text()).substitute(values),
        bridge_init="usp_ip_init(memory, plic);",
        bridge_dts="\n".join(
            f'{name}: {name}@{values[name + "_unit"]} {{ compatible = "socsim,{name}"; '
            f"reg = <{values[name + '_base']} {values[name + '_size']}>; "
            f"interrupt-parent = <&plic>; interrupts = <{values[name + '_irq']} 1>; }};"
            for name in ("cygcd", "otgpio")
        ),
    )
    return Template((RESOURCES / "guest.c.in").read_text()).substitute(values)
