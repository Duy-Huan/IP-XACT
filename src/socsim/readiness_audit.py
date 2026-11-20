"""Full-inventory (M8) and milestone/release readiness (M9) auditor."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .project import read_yaml
from .upgrade import validate_ledger

QUALIFIED_PUBLIC_MODELS: frozenset[tuple[str, str]] = frozenset(
    {
        ("chipyard", "BootROM"),
        ("chipyard", "RocketConfig"),
        ("chipyard", "TLRAM"),
        ("opentitan", "gpio"),
        ("opentitan", "rv_core_ibex"),
        ("opentitan", "rv_plic"),
        ("opentitan", "rv_timer"),
        ("opentitan", "uart"),
    }
)


def audit_system_inventory(root: Path, system_name: str) -> dict[str, Any]:
    """Audit real instance-level binding readiness for AI_SOC (46) or AIZ_SOC (37)."""
    system_yaml = root / "systems" / system_name / "system.yaml"
    data = read_yaml(system_yaml)
    blocks = data["blocks"]
    provider_counts = Counter(b["provider"] for b in blocks)
    role_counts = Counter(b["role"] for b in blocks)
    qualified_instances = [
        b["instance"] for b in blocks if (b["provider"], b["model"]) in QUALIFIED_PUBLIC_MODELS
    ]
    open_instances = [
        {
            "instance": b["instance"],
            "provider": b["provider"],
            "model": b["model"],
            "role": b["role"],
        }
        for b in blocks
        if (b["provider"], b["model"]) not in QUALIFIED_PUBLIC_MODELS
    ]
    return {
        "system": system_name,
        "total_instances": len(blocks),
        "providers": dict(sorted(provider_counts.items())),
        "roles": dict(sorted(role_counts.items())),
        "qualified_model_matches": len(qualified_instances),
        "open_instance_count": len(open_instances),
        "open_instances_sample": open_instances[:10],
        "full_inventory_closed": len(open_instances) == 0,
    }


def audit_milestone_readiness(root: Path) -> dict[str, Any]:
    """Combine milestone ledger consistency with M8 full-inventory coverage."""
    root = root.resolve()
    ledger = validate_ledger(root)
    raw = read_yaml(root / "upgrade/milestones.yaml")
    by_target: dict[str, dict[str, int]] = {}
    for milestone in raw["milestones"]:
        target = milestone["target_version"]
        bucket = by_target.setdefault(
            target, {"verified": 0, "implemented": 0, "open": 0, "blocked": 0}
        )
        for exit_item in milestone["required_exits"]:
            bucket[exit_item["status"]] += 1

    inventories = {
        name: audit_system_inventory(root, name)
        for name in ("AI_SOC", "AIZ_SOC")
        if (root / "systems" / name / "system.yaml").is_file()
    }
    return {
        "ledger": ledger,
        "exit_counts_by_target_version": by_target,
        "full_design_inventories": inventories,
        "ready_for_final_v0_3_0": by_target.get("0.3.0", {}).get("open", 1) == 0
        and by_target.get("0.3.0", {}).get("blocked", 1) == 0
        and by_target.get("0.3.0", {}).get("implemented", 1) == 0,
        "ready_for_final_v1_0_0": all(v == "verified" for v in ledger["milestones"].values()),
    }
