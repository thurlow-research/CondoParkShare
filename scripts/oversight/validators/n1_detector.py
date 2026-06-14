#!/usr/bin/env python3
"""
n1_detector.py — Django N+1 query heuristic via AST pattern matching.

Detects ORM queryset calls inside for/while loop bodies. These are the most
common form of N+1 query in Django: accessing a related object per iteration
without prefetch_related or select_related.

This is a heuristic — it catches common patterns but will miss sophisticated
cases and may flag some false positives (e.g. intentional per-item queries).
Findings should be treated as "investigate" not "definitely broken".

Usage: python n1_detector.py file.py [file2.py ...]
"""

from __future__ import annotations
import ast
import json
import sys
from pathlib import Path

# self-bootstrap: ensure this file's dir (with schema.py) is importable
# regardless of caller cwd/PYTHONPATH (run_validators, run_panel, direct).
import sys as _hos_sys
import pathlib as _hos_pl
_hos_sys.path.insert(0, str(_hos_pl.Path(__file__).resolve().parent))
from schema import make_result, make_finding, normalize, WEIGHTS  # noqa: E402

# ORM method names that indicate a queryset operation (database hit)
_ORM_METHODS = frozenset(
    {
        "all",
        "filter",
        "exclude",
        "get",
        "first",
        "last",
        "count",
        "exists",
        "values",
        "values_list",
        "annotate",
        "aggregate",
        "select_related",
        "prefetch_related",
        "order_by",
        "distinct",
        "create",
        "update",
        "delete",
        "bulk_create",
        "bulk_update",
        "get_or_create",
        "update_or_create",
    }
)

# Attributes that suggest accessing a related manager (common N+1 pattern)
_RELATED_MANAGER_PATTERNS = frozenset(
    {
        "objects",
        "all",
        "filter",
        "set",
        "add",
        "remove",
    }
)


class _N1Visitor(ast.NodeVisitor):
    def __init__(self, filename: str):
        self.filename = filename
        self.findings: list[dict] = []
        self._loop_depth = 0

    def _in_loop(self) -> bool:
        return self._loop_depth > 0

    def _enter_loop(self, node: ast.For | ast.While, body: list) -> None:
        self._loop_depth += 1
        for stmt in body:
            self.visit(stmt)
        self._loop_depth -= 1

    def visit_For(self, node: ast.For) -> None:
        self._enter_loop(node, node.body)
        if node.orelse:
            for stmt in node.orelse:
                self.visit(stmt)

    def visit_While(self, node: ast.While) -> None:
        self._enter_loop(node, node.body)

    def visit_Call(self, node: ast.Call) -> None:
        if self._in_loop():
            self._check_orm_call(node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self._in_loop() and node.attr in _ORM_METHODS:
            # Heuristic: attribute access of an ORM method name inside a loop
            # Check if the parent is a Call (actual method call, not just reference)
            self._record_candidate(node)
        self.generic_visit(node)

    def _check_orm_call(self, node: ast.Call) -> None:
        # Pattern: obj.related_manager.all() or obj.objects.filter(...)
        if isinstance(node.func, ast.Attribute):
            method = node.func.attr
            if method in _ORM_METHODS:
                self._record_candidate(node.func)

    def _record_candidate(self, node: ast.AST) -> None:
        lineno = getattr(node, "lineno", 0)
        # Avoid duplicate recording for the same line
        if any(f["line"] == lineno for f in self.findings):
            return
        self.findings.append(
            {
                "file": self.filename,
                "line": lineno,
                "loop_depth": self._loop_depth,
                "attr": getattr(node, "attr", "?"),
            }
        )


def analyse_files(file_paths: list[str]) -> dict:
    all_findings: list[dict] = []

    for path in file_paths:
        try:
            source = Path(path).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=path)
            v = _N1Visitor(path)
            v.visit(tree)
            all_findings.extend(v.findings)
        except Exception:
            pass

    count = len(all_findings)
    score = normalize(count, 0, 8)

    evidence = [
        make_finding(
            f["file"],
            f["line"],
            f"potential N+1: .{f['attr']}() inside loop (depth={f['loop_depth']})",
            severity="medium",
        )
        for f in all_findings[:10]
    ]

    checklist = []
    if all_findings:
        checklist.append(
            "N+1 candidates found — verify each ORM call inside a loop uses "
            "select_related() or prefetch_related() on the queryset that feeds the loop."
        )
        for f in all_findings[:3]:
            checklist.append(
                f"  {Path(f['file']).name}:{f['line']} — .{f['attr']}() inside loop: "
                "is this queryset prefetched upstream?"
            )

    return make_result(
        dimension="n1_queries",
        score=score,
        raw_value={"candidate_count": count, "locations": all_findings[:20]},
        weight=WEIGHTS["n1_queries"],
        evidence=evidence,
        checklist_items=checklist,
    )


def main() -> None:
    files = [f for f in sys.argv[1:] if f.endswith(".py") and Path(f).exists()]
    if not files:
        print(
            json.dumps(
                make_result(
                    "n1_queries",
                    0.0,
                    {"error": "no input"},
                    weight=WEIGHTS["n1_queries"],
                    error="no input files",
                ),
                indent=2,
            )
        )
        return
    print(json.dumps(analyse_files(files), indent=2))


if __name__ == "__main__":
    main()
