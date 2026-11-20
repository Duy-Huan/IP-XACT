"""Validate the sequential milestone ledger without turning plans into acceptance."""

import argparse
import json
from pathlib import Path

from .domain import ContractError
from .project import inside, keys, read_yaml


def validate_ledger(root):
    root = root.resolve()
    directory = root / "upgrade"
    data = read_yaml(directory / "milestones.yaml")
    keys(data, {"schema_version", "baseline", "policy", "milestones"}, "upgrade ledger")
    if data["schema_version"] != "1" or data["policy"] != {
        "execution": "sequential",
        "aggregate_ci": "after_complete_batch",
        "completion": "all_required_exits_with_actual_evidence",
        "future_tags": "never_precreate",
    }:
        raise ContractError("upgrade requires sequential work and one final aggregate gate")
    if not isinstance(data["milestones"], list):
        raise ContractError("upgrade milestone inventory must be a list")
    by_id = {}
    for item in data["milestones"]:
        keys(
            item,
            {"id", "target_version", "document", "depends_on", "status", "required_exits"},
            "milestone",
        )
        if item["id"] in by_id or item["status"] not in {
            "planned",
            "partial",
            "blocked",
            "verified",
        }:
            raise ContractError("duplicate milestone or invalid observed status")
        if item["target_version"] not in {"0.3.0", "0.4.0", "1.0.0"}:
            raise ContractError("milestone version differs from approved programme")
        doc = inside(directory, item["document"])
        if not doc.is_file() or len(doc.read_text().strip()) < 100:
            raise ContractError("milestone requires a concrete execution/acceptance document")
        if not isinstance(item["depends_on"], list) or len(set(item["depends_on"])) != len(
            item["depends_on"]
        ):
            raise ContractError("invalid milestone dependencies")
        if not isinstance(item["required_exits"], list) or not item["required_exits"]:
            raise ContractError("milestone requires explicit acceptance exits")
        for exit in item["required_exits"]:
            keys(exit, {"requirement", "status", "evidence"}, "milestone exit")
            if (
                not isinstance(exit["requirement"], str)
                or exit["status"] not in {"open", "implemented", "blocked", "verified"}
                or not isinstance(exit["evidence"], list)
            ):
                raise ContractError("invalid milestone acceptance state")
            if exit["status"] == "verified" and (
                not exit["evidence"]
                or all(
                    isinstance(ev, dict) and ev.get("kind") == "implementation-reference"
                    for ev in exit["evidence"]
                )
            ):
                raise ContractError("verified exit requires actual qualification evidence")
        if item["status"] == "verified" and any(
            e["status"] != "verified" for e in item["required_exits"]
        ):
            raise ContractError("partial implementation cannot close a complete milestone")
        by_id[item["id"]] = item
    if set(by_id) != {f"M{i}" for i in range(1, 10)}:
        raise ContractError("upgrade ledger must retain the complete approved M1-M9 programme")
    ordered, visiting = [], set()

    def visit(name):
        if name not in by_id:
            raise ContractError("milestone dependency does not exist")
        if name in visiting:
            raise ContractError("milestone dependency cycle")
        if name in ordered:
            return
        visiting.add(name)
        for dependency in by_id[name]["depends_on"]:
            visit(dependency)
            if by_id[name]["status"] == "verified" and by_id[dependency]["status"] != "verified":
                raise ContractError("verified milestone has an unqualified prerequisite")
        visiting.remove(name)
        ordered.append(name)

    for name in sorted(by_id):
        visit(name)
    return {
        "validation": "PASSED",
        "scope": "ledger consistency; milestone completion remains separate",
        "execution_order": ordered,
        "milestones": {n: by_id[n]["status"] for n in ordered},
        "baseline": data["baseline"],
        "aggregate_ci": "after_complete_batch",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog="usp upgrade")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Include M1-M9 exit summary and AI_SOC/AIZ_SOC full-inventory readiness",
    )
    args = parser.parse_args(argv)
    try:
        if args.audit:
            from .readiness_audit import audit_milestone_readiness

            payload = audit_milestone_readiness(args.root)
        else:
            payload = validate_ledger(args.root)
        print(json.dumps(payload, indent=2))
        return 0
    except (ContractError, OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"validation": "ERROR", "message": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
