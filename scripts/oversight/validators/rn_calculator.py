#!/usr/bin/env python3
"""
rn_calculator.py — Dai et al. (2024) Risk Number for Python source files.

Risk Number improves on Cognitive Complexity by using empirically calibrated
nesting increments derived from regression analysis on historical bug data.
Reference: Dai, Liu & Xu, "Enhancing human-machine pair inspection with risk
number and code inspection diagram", Software Quality Journal 32:939-959, 2024.

Formula per statement:
  RN = nesting_increment(depth) + judgment_increment
where:
  nesting_increment: I_nesting(d) calibrated from bug probability at nesting level d
                     (default: Dai's case study coefficients; override via NESTING_COEFFS)
  judgment_increment: +1 for each flow-break node + +1 per logical operator in condition

Usage:
  python rn_calculator.py file.py [file2.py ...]
  python rn_calculator.py --files file1.py file2.py
"""

from __future__ import annotations
import ast
import json
import sys
from pathlib import Path
from typing import Any

from schema import make_result, make_finding, normalize, WEIGHTS

# Nesting increment table from Dai's case study regression.
# Key: nesting depth (0 = outermost flow-break in function).
# Override by setting environment variable OVERSIGHT_NESTING_COEFFS=w,b
# to use linear formula I_nesting(d) = w*d + b.
_NESTING_TABLE: dict[int, float] = {
    0: 0.0,   # outermost — no nesting penalty
    1: 1.0,
    2: 3.0,
    3: 4.8,
    4: 7.1,
}
_NESTING_W: float = 2.01
_NESTING_B: float = -1.05


def nesting_increment(depth: int) -> float:
    """Return calibrated nesting increment for the given nesting depth."""
    if depth in _NESTING_TABLE:
        return _NESTING_TABLE[depth]
    return round(max(0.0, _NESTING_W * depth + _NESTING_B), 1)


def _count_logical_ops(node: ast.AST) -> int:
    """Count and/or operators and ternary expressions in a node's condition."""
    condition: ast.AST | None = None
    if isinstance(node, (ast.If, ast.While)):
        condition = node.test
    elif isinstance(node, ast.Assert):
        condition = node.test

    if condition is None:
        return 0

    count = 0
    for child in ast.walk(condition):
        if isinstance(child, ast.BoolOp):
            # BoolOp values has N operands → N-1 operators
            count += len(child.values) - 1
        elif isinstance(child, ast.IfExp):
            count += 1
    return count


# Node types that are flow-break structures (contribute judgment + nesting increments)
_FLOW_BREAK = (
    ast.If,
    ast.For,
    ast.While,
    ast.ExceptHandler,
    ast.With,
    ast.AsyncWith,
    ast.AsyncFor,
)


class _FunctionRNVisitor(ast.NodeVisitor):
    """
    Walks a single function's body, tracking nesting depth and computing
    per-statement Risk Numbers. Does NOT recurse into nested function defs
    (those are analysed separately as top-level functions).
    """

    def __init__(self, filename: str, func_name: str, start_line: int):
        self.filename = filename
        self.func_name = func_name
        self.start_line = start_line
        self.depth = 0
        self.total_rn: float = 0.0
        self.statements: list[dict] = []

    def _record(self, node: ast.AST, rn: float) -> None:
        lineno = getattr(node, "lineno", 0)
        self.total_rn += rn
        self.statements.append({
            "line": lineno,
            "type": type(node).__name__,
            "rn": rn,
            "nesting_depth": self.depth,
        })

    def _visit_flow_break_body(self, node: ast.AST, body: list) -> None:
        """Compute RN for a flow-break node, then recurse into its body."""
        j_inc = 1 + _count_logical_ops(node)
        n_inc = nesting_increment(self.depth)
        self._record(node, n_inc + j_inc)
        self.depth += 1
        for stmt in body:
            self.visit(stmt)
        self.depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Nested function — skip; it is analysed as its own entry
        pass

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_If(self, node: ast.If) -> None:
        self._visit_flow_break_body(node, node.body)
        if node.orelse:
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                # elif: judgment +1, no additional nesting increment
                elif_node = node.orelse[0]
                j_inc = 1 + _count_logical_ops(elif_node)
                self._record(elif_node, j_inc)
                self.depth += 1
                for stmt in elif_node.body:
                    self.visit(stmt)
                # handle trailing else after elif
                if elif_node.orelse:
                    self._record_else(elif_node.orelse)
                self.depth -= 1
            else:
                self._record_else(node.orelse)

    def _record_else(self, orelse: list) -> None:
        # else: judgment +1 only, no nesting increment
        self.total_rn += 1
        self.depth += 1
        for stmt in orelse:
            self.visit(stmt)
        self.depth -= 1

    def visit_For(self, node: ast.For) -> None:
        self._visit_flow_break_body(node, node.body)
        if node.orelse:
            self._record_else(node.orelse)

    def visit_While(self, node: ast.While) -> None:
        self._visit_flow_break_body(node, node.body)
        if node.orelse:
            self._record_else(node.orelse)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self._visit_flow_break_body(node, node.body)

    def visit_With(self, node: ast.With) -> None:
        self._visit_flow_break_body(node, node.body)

    visit_AsyncWith = visit_With
    visit_AsyncFor = visit_For


def _collect_functions(tree: ast.Module, filename: str) -> list[dict]:
    """
    Walk the module AST and collect all function/method definitions at all levels,
    returning a flat list with their body nodes for RN computation.
    """
    results: list[dict] = []

    class _Collector(ast.NodeVisitor):
        def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            visitor = _FunctionRNVisitor(filename, node.name, node.lineno)
            for stmt in node.body:
                visitor.visit(stmt)
            results.append({
                "name": node.name,
                "file": filename,
                "start_line": node.lineno,
                "risk_number": round(visitor.total_rn, 1),
                "statements": visitor.statements,
            })
            # Use generic_visit so the NodeVisitor machinery handles recursion.
            # ast.walk would yield grandchildren before _visit_func returns,
            # causing depth-3+ nested functions to be appended twice.
            self.generic_visit(node)

        visit_FunctionDef = _visit_func
        visit_AsyncFunctionDef = _visit_func

    _Collector().visit(tree)
    return results


def _checklist_items(func: dict) -> list[str]:
    """Produce Dai CID-style checklist items for the highest-RN statements."""
    items = []
    top = sorted(func["statements"], key=lambda s: s["rn"], reverse=True)[:3]
    for s in top:
        if s["rn"] == 0:
            continue
        node_type = s["type"]
        line = s["line"]
        fn = func["name"]
        if node_type == "If":
            items.append(
                f"{fn}:{line} — can this condition be both true and false as expected?"
            )
            items.append(
                f"{fn}:{line} — are all branches (including implicit else) handled?"
            )
        elif node_type in ("For", "AsyncFor"):
            items.append(f"{fn}:{line} — is the loop bound correct and termination guaranteed?")
            items.append(f"{fn}:{line} — is anything mutated inside the loop that affects the iterator?")
        elif node_type == "While":
            items.append(f"{fn}:{line} — can the while condition become False? Is the loop variable updated?")
        elif node_type == "ExceptHandler":
            items.append(f"{fn}:{line} — is the exception caught at the right granularity (not bare `except`)?")
        elif node_type in ("With", "AsyncWith"):
            items.append(f"{fn}:{line} — does the context manager correctly release resources on exception?")
    return items


# Score thresholds: RN >= HIGH_THRESHOLD maps to score 1.0
_SCORE_HIGH_THRESHOLD = 20.0


def analyse_files(file_paths: list[str]) -> dict:
    all_functions: list[dict] = []
    parse_errors: list[str] = []

    for path in file_paths:
        try:
            source = Path(path).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=path)
            all_functions.extend(_collect_functions(tree, path))
        except SyntaxError as e:
            parse_errors.append(f"{path}: SyntaxError {e}")
        except Exception as e:
            parse_errors.append(f"{path}: {e}")

    if not all_functions:
        return make_result(
            "risk_number", 0.0, {"functions": [], "parse_errors": parse_errors},
            weight=WEIGHTS["risk_number"],
            error=("; ".join(parse_errors) if parse_errors else None),
        )

    max_rn = max(f["risk_number"] for f in all_functions)
    mean_rn = sum(f["risk_number"] for f in all_functions) / len(all_functions)
    high_risk = [f for f in all_functions if f["risk_number"] >= 8.0]

    score = normalize(max_rn, 0, _SCORE_HIGH_THRESHOLD)

    evidence = [
        make_finding(f["file"], f["start_line"],
                     f"RN={f['risk_number']} — {f['name']}()",
                     severity="high" if f["risk_number"] >= 8 else "medium")
        for f in sorted(all_functions, key=lambda x: x["risk_number"], reverse=True)[:5]
    ]

    checklist: list[str] = []
    for f in sorted(all_functions, key=lambda x: x["risk_number"], reverse=True)[:3]:
        checklist.extend(_checklist_items(f))

    return make_result(
        dimension="risk_number",
        score=score,
        raw_value={
            "max_rn": max_rn,
            "mean_rn": round(mean_rn, 2),
            "high_risk_functions": [f["name"] for f in high_risk],
            "function_count": len(all_functions),
            "parse_errors": parse_errors,
        },
        weight=WEIGHTS["risk_number"],
        evidence=evidence,
        checklist_items=checklist,
        findings=[
            {
                "function": f["name"],
                "file": f["file"],
                "line": f["start_line"],
                "risk_number": f["risk_number"],
                "top_statements": sorted(f["statements"], key=lambda s: s["rn"], reverse=True)[:3],
            }
            for f in sorted(all_functions, key=lambda x: x["risk_number"], reverse=True)
        ],
    )


def main() -> None:
    args = sys.argv[1:]
    if "--files" in args:
        idx = args.index("--files")
        files = args[idx + 1:]
    else:
        files = args

    files = [f for f in files if f.endswith(".py") and Path(f).exists()]
    if not files:
        result = make_result("risk_number", 0.0, {"error": "no Python files provided"},
                             weight=WEIGHTS["risk_number"], error="no input files")
    else:
        result = analyse_files(files)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
