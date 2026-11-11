"""Capability-gated lowering for the explicit upstream QEMU virt reference board."""

from .domain import ContractError
from .project import Project, keys

CAPABILITIES = {
    "qemu": {
        "status": "implemented",
        "profile": "qemu-virt-rv64-v1",
        "fidelity": "functional",
        "scope": "fixed virt board; bare-metal smoke",
    },
    "gem5": {"status": "planned", "reason": "No pinned, tested FS/SE adapter yet"},
    "chipyard": {
        "status": "planned",
        "reason": "RTL source/generator ecosystem, not a CPU emulator",
    },
    "opentitan": {"status": "planned", "reason": "IP/SoC sources require per-IP model contracts"},
    "systemc": {
        "status": "planned",
        "reason": "No verified TLM bridge or synchronization contract",
    },
}

# Implementations are identifiers in this adapter, never arbitrary import paths or commands.
MODELS = {"qemu.rv64", "qemu.virt.ram", "qemu.virt.ns16550", "qemu.virt.sifive-test"}


def qemu_plan(project: Project, backend: str) -> dict:
    if backend != "qemu":
        raise ContractError(f"UNSUPPORTED backend {backend}: no executable adapter")
    if project.manifest["profile"] != "qemu-virt-rv64-v1":
        raise ContractError("UNSUPPORTED profile; custom maps require a custom QEMU machine")
    registry = {}
    for binding in project.models["bindings"]:
        keys(binding, {"vlnv", "backend", "implementation"}, "model binding")
        if not all(isinstance(v, str) for v in binding.values()):
            raise ContractError("binding values must be strings")
        key = (binding["vlnv"], binding["backend"])
        if key in registry:
            raise ContractError(f"ambiguous binding {key}")
        registry[key] = binding["implementation"]
    resolved = []
    by_model = {}
    for instance in project.ir.instances:
        model = registry.get((instance.vlnv, backend))
        if model not in MODELS:
            raise ContractError(
                f"{instance.name}: UNBOUND/UNSUPPORTED {instance.vlnv} on {backend}"
            )
        if model in by_model:
            raise ContractError(f"profile allows one instance of {model}")
        by_model[model] = instance
        resolved.append({"instance": instance.name, "vlnv": instance.vlnv, "implementation": model})
    if set(by_model) != MODELS:
        raise ContractError("profile requires exactly CPU, RAM, UART and test-finisher")
    if by_model["qemu.rv64"].windows:
        raise ContractError("CPU cannot have MMIO windows in this profile")
    expected = {
        "qemu.virt.ram": (0x80000000, 128 * 1024 * 1024, 64),
        "qemu.virt.ns16550": (0x10000000, 0x100, 8),
        "qemu.virt.sifive-test": (0x100000, 0x1000, 32),
    }
    for model, (base, size, width) in expected.items():
        windows = by_model[model].windows
        if len(windows) != 1 or (windows[0].base, windows[0].size, windows[0].width) != (
            base,
            size,
            width,
        ):
            raise ContractError(f"{model}: map does not match upstream virt reference profile")
    return {
        "schema_version": "1",
        "backend": "qemu",
        "profile": project.manifest["profile"],
        "fidelity": "functional",
        "simulated_time_seconds": None,
        "limitations": [
            "Fixed upstream virt board; not custom SoC synthesis",
            "Implicit virt ROM/CLINT/PLIC/other board devices are not generated IP",
            "No cycle, cache, security, IRQ or OS validation claimed",
        ],
        "qemu_version": project.lock["qemu_version"],
        "bindings": resolved,
        "arguments": [
            "-machine",
            "virt",
            "-cpu",
            "rv64",
            "-smp",
            "1",
            "-m",
            "128M",
            "-display",
            "none",
            "-monitor",
            "none",
            "-serial",
            "stdio",
            "-bios",
            "none",
            "-no-reboot",
        ],
    }
