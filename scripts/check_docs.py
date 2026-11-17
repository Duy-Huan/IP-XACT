"""Require real documentation and an execution guide for every example."""

from pathlib import Path

root = Path(__file__).resolve().parents[1]
required = [
    "docs/ci-local.md",
    "docs/native-soc-generator.md",
    "specs/README.md",
    "ref/notes/chia-2606.27350.md",
    "docs/industrial-readiness.md",
    "docs/podman.md",
    "docs/next-version.md",
    "docs/coherent-dma.md",
    "docs/rtl-software-platform.md",
    "docs/rtl-zephyr.md",
    "docs/live-garnet.md",
    "docs/rtl-os.md",
    "docs/rtl-cpu-adapters.md",
    "docs/software-layers-performance.md",
    "docs/cpu-platforms.md",
    "docs/research-direction.md",
    "ref/README.md",
    "docs/unified-soc.md",
    "docs/rtl-testchips.md",
    "docs/openroad.md",
    "docs/dual-soc.md",
    "docs/full-soc-integration.md",
    "docs/registers.md",
    "docs/parameters.md",
    "docs/milestone-checklist.md",
    "docs/verification-status.md",
    "docs/build-reproducibility.md",
    "docs/freertos.md",
    "agent.md",
    "AGENTS.md",
    "README.md",
    "CHANGELOG.md",
    "docs/architecture.md",
    "docs/native-design-capabilities.md",
    "docs/native-external-models.md",
    "docs/creating-soc.md",
    "docs/firmware-and-os.md",
    "docs/roadmap.md",
    "docs/development.md",
    "skills/setup/SKILL.md",
    "skills/update/SKILL.md",
]
required += [f"docs/examples/{p.name}.md" for p in (root / "examples").iterdir() if p.is_dir()]
for name in required:
    path = root / name
    if not path.is_file() or len(path.read_text().strip()) < 100:
        raise SystemExit(f"Missing/empty documentation: {name}")
print(f"Documentation gate: {len(required)} files checked")
