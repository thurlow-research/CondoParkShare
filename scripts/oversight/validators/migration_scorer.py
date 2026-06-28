#!/usr/bin/env python3
"""
migration_scorer.py — Django migration risk classification.

Classifies migration operations by risk level based on operation type:

  CRITICAL : RunPython data migrations, DeleteModel, RemoveField on live data
  HIGH     : AlterField (type/nullability change), RenameField, RenameModel,
             AlterUniqueTogether (removing constraints), AddField non-nullable
  MEDIUM   : AddField nullable/default, AlterIndexTogether, RunSQL (read-only)
  LOW      : AddIndex, CreateModel (new table), AlterModelOptions

Usage: python migration_scorer.py migrations/0003_auth.py [...]
"""

from __future__ import annotations

import ast
import json
import pathlib as _hos_pl

# self-bootstrap: ensure this file's dir (with schema.py) is importable
# regardless of caller cwd/PYTHONPATH (run_validators, run_panel, direct).
import sys
import sys as _hos_sys
from pathlib import Path

_hos_sys.path.insert(0, str(_hos_pl.Path(__file__).resolve().parent))
from schema import WEIGHTS, make_finding, make_result  # noqa: E402

_OP_RISK: dict[str, tuple[str, str]] = {
    # (risk_level, reason)
    "RunPython": ("CRITICAL", "data migration — Python code runs on production data"),
    "RunSQL": ("HIGH", "raw SQL — verify it's idempotent and reversible"),
    "DeleteModel": ("CRITICAL", "destroys table — irreversible data loss"),
    "RemoveField": ("HIGH", "drops column — data loss if not already migrated"),
    "RenameModel": ("HIGH", "renames table — breaks any raw SQL or external references"),
    "RenameField": ("HIGH", "renames column — breaks queries not going through ORM"),
    "AlterField": ("HIGH", "changes column definition — may truncate data or break constraints"),
    "AlterUniqueTogether": ("HIGH", "modifying uniqueness constraints — data integrity risk"),
    "AlterIndexTogether": ("MEDIUM", "index change — performance impact, verify on large tables"),
    "AddField": ("MEDIUM", "new column — risk depends on nullable/default (checked below)"),
    "AddIndex": ("LOW", "new index — safe, but CONCURRENT not used by default in Django"),
    "CreateModel": ("LOW", "new table — generally safe"),
    "AlterModelOptions": ("LOW", "metadata only — no schema change"),
    "SeparateDatabaseAndState": ("MEDIUM", "state-only migration — verify DB is already in sync"),
}

_TIER_SCORE = {"CRITICAL": 1.0, "HIGH": 0.75, "MEDIUM": 0.45, "LOW": 0.15}


class _MigrationVisitor(ast.NodeVisitor):
    """Extract migration operation class names from a Django migrations file."""

    def __init__(self):
        self.operations: list[tuple[str, int]] = []  # (op_name, lineno)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # migrations.AddField(...)  or  migrations.RunPython(...)
        if isinstance(node.value, ast.Name) and node.value.id == "migrations":
            self.operations.append((node.attr, node.lineno))
        self.generic_visit(node)


def _check_add_field_nullable(source: str, op_name_line: int) -> bool:
    """
    Heuristic: if an AddField call nearby does NOT set null=True or
    provide a default, it's a non-nullable add on an existing table = HIGH risk.
    This is a rough approximation.
    """
    lines = source.splitlines()
    context = "\n".join(lines[max(0, op_name_line - 1) : op_name_line + 10])
    return "null=True" not in context and "default=" not in context


def analyse_files(file_paths: list[str]) -> dict:
    """Only analyse files that look like Django migration files."""
    migration_files = [p for p in file_paths if "migration" in p.lower() and p.endswith(".py")]

    if not migration_files:
        return make_result(
            "migration_risk",
            0.0,
            {"note": "no migration files in changeset"},
            weight=WEIGHTS["migration_risk"],
        )

    all_ops: list[dict] = []
    parse_errors: list[dict] = []
    evidence: list[dict] = []
    checklist: list[str] = []

    for path in migration_files:
        try:
            source = Path(path).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=path)
            v = _MigrationVisitor()
            v.visit(tree)

            for op, lineno in v.operations:
                risk, reason = _OP_RISK.get(op, ("MEDIUM", "unknown operation"))
                # Upgrade AddField to HIGH if non-nullable without default
                if op == "AddField" and _check_add_field_nullable(source, lineno):
                    risk = "HIGH"
                    reason = (
                        "AddField without null=True or default — "
                        "Django will prompt for a default; risky on populated tables"
                    )

                all_ops.append({"file": path, "op": op, "risk": risk, "reason": reason})
                evidence.append(
                    make_finding(
                        path,
                        0,
                        f"{op}: {reason}",
                        severity=risk.lower() if risk != "CRITICAL" else "high",
                    )
                )
                if risk in ("CRITICAL", "HIGH"):
                    checklist.append(f"{op} ({Path(path).name}): {reason}")
                    checklist.append(
                        "  → Is there a reverse migration? "
                        "Has this been tested on a copy of prod data?"
                    )

        except Exception as e:
            # Track parse failures separately — do NOT mix them into all_ops,
            # where a missing "risk" key would default to LOW (0.15) and let an
            # unparseable migration score as clean. (#917)
            parse_errors.append({"file": path, "error": str(e)})

    # Every migration file failed to parse → no signal at all. Exclude the
    # dimension via error= rather than scoring a LOW-clean 0.15. (#917)
    if parse_errors and not all_ops:
        detail = "; ".join(f"{Path(p['file']).name}: {p['error']}" for p in parse_errors)
        return make_result(
            "migration_risk",
            0.0,
            {"parse_errors": parse_errors, "files": migration_files},
            weight=WEIGHTS["migration_risk"],
            error=f"all migration files unparseable — cannot assess risk: {detail}",
        )

    if not all_ops:
        return make_result(
            "migration_risk", 0.0, {"files": migration_files}, weight=WEIGHTS["migration_risk"]
        )

    # Some files parsed but others did not: keep the real signal, but flag the
    # unparseable files so a reviewer confirms they hide no high-risk operations.
    for pe in parse_errors:
        checklist.insert(
            0,
            f"⚠ {Path(pe['file']).name} could not be parsed ({pe['error']}) — "
            "manually verify it contains no high-risk migration operations",
        )

    max_score = max(_TIER_SCORE.get(op["risk"], 0.15) for op in all_ops)
    critical_ops = [op for op in all_ops if op.get("risk") == "CRITICAL"]
    high_ops = [op for op in all_ops if op.get("risk") == "HIGH"]

    if critical_ops:
        checklist.insert(
            0, "⚠ CRITICAL migration operations present — requires human review before merge"
        )

    return make_result(
        dimension="migration_risk",
        score=max_score,
        raw_value={
            "operations": all_ops,
            "critical_count": len(critical_ops),
            "high_count": len(high_ops),
            "parse_errors": parse_errors,
            "files": migration_files,
        },
        weight=WEIGHTS["migration_risk"],
        evidence=evidence,
        checklist_items=checklist,
    )


def main() -> None:
    files = [f for f in sys.argv[1:] if Path(f).exists()]
    if not files:
        print(
            json.dumps(
                make_result(
                    "migration_risk",
                    0.0,
                    {"error": "no input"},
                    weight=WEIGHTS["migration_risk"],
                    error="no input files",
                ),
                indent=2,
            )
        )
        return
    print(json.dumps(analyse_files(files), indent=2))


if __name__ == "__main__":
    main()
