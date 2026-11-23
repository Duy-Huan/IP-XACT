"""Full-inventory (M8) and milestone/release readiness (M9) auditor."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .ip_catalog import catalog_summary, lookup_ip
from .project import read_yaml
from .upgrade import validate_ledger


def audit_system_inventory(root: Path, system_name: str) -> dict[str, Any]:
    """Audit real instance-level binding readiness for AI_SOC (46) or AIZ_SOC (37)."""
    system_yaml = root / "systems" / system_name / "system.yaml"
    data = read_yaml(system_yaml)
    blocks = data["blocks"]
    provider_counts = Counter(b["provider"] for b in blocks)
    role_counts = Counter(b["role"] for b in blocks)
    readiness_counts: Counter[str] = Counter()
    open_instances: list[dict[str, Any]] = []

    for block in blocks:
        entry = lookup_ip(block["provider"], block["model"])
        readiness_counts[entry.readiness] += 1
        if entry.readiness != "qualified-native-or-rtl":
            open_instances.append(
                {
                    "instance": block["instance"],
                    "provider": block["provider"],
                    "model": block["model"],
                    "role": block["role"],
                    "readiness": entry.readiness,
                    "upstream_path": entry.upstream_path,
                }
            )

    return {
        "system": system_name,
        "total_instances": len(blocks),
        "providers": dict(sorted(provider_counts.items())),
        "roles": dict(sorted(role_counts.items())),
        "by_readiness": dict(sorted(readiness_counts.items())),
        "qualified_model_matches": readiness_counts["qualified-native-or-rtl"],
        "pinned_source_needs_adapter": readiness_counts["pinned-source-needs-adapter"],
        "missing_source_implementation": readiness_counts["missing-source-implementation"],
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
        "ip_catalog": catalog_summary(),
        "exit_counts_by_target_version": by_target,
        "full_design_inventories": inventories,
        "ready_for_final_v0_3_0": by_target.get("0.3.0", {}).get("open", 1) == 0
        and by_target.get("0.3.0", {}).get("blocked", 1) == 0
        and by_target.get("0.3.0", {}).get("implemented", 1) == 0,
        "ready_for_final_v1_0_0": all(v == "verified" for v in ledger["milestones"].values()),
    }
