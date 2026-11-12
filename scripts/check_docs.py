"""Require real documentation and an execution guide for every example."""

from pathlib import Path

root = Path(__file__).resolve().parents[1]
required = [
    "docs/milestone-checklist.md",
    "docs/verification-status.md",
    "docs/build-reproducibility.md",
    "docs/freertos.md",
    "agent.md",
    "AGENTS.md",
    "README.md",
    "CHANGELOG.md",
    "docs/architecture.md",
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
