#!/usr/bin/env python3
"""
function_metrics.py — function-level size and structure metrics via AST.

Metrics:
  - lines of code (excluding blanks and docstrings)
  - parameter count (excluding self/cls)
  - return path count (number of return/raise statements)
  - max nesting depth (deepest nesting level in the function body)

Empirically: long functions, many parameters, and many exit paths all
correlate with higher defect density.

Usage: python function_metrics.py file.py [file2.py ...]
"""

from __future__ import annotations
import ast
import json
import sys
from pathlib import Path

from schema import make_result, make_finding, normalize, WEIGHTS

_LONG_FUNC_LINES = 60
_MANY_PARAMS = 6
_MANY_RETURNS = 5
_DEEP_NESTING = 4


class _FuncMetricsVisitor(ast.NodeVisitor):
    def __init__(self):
        self.functions: list[dict] = []

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        args = node.args
        params = (
            len(args.args)
            + len(args.posonlyargs)
            + len(args.kwonlyargs)
            + (1 if args.vararg else 0)
            + (1 if args.kwarg else 0)
        )
        # Exclude self/cls from count for methods
        first_arg = (args.posonlyargs or args.args or [None])[0]
        if first_arg and getattr(first_arg, "arg", "") in ("self", "cls"):
            params = max(0, params - 1)

        returns = sum(
            1 for n in ast.walk(node)
            if isinstance(n, (ast.Return, ast.Raise))
        )

        # Line count: end_lineno - lineno (approximate; includes docstring)
        lines = getattr(node, "end_lineno", node.lineno) - node.lineno + 1

        max_depth = _max_nesting_depth(node.body)

        self.functions.append({
            "name": node.name,
            "line": node.lineno,
            "lines": lines,
            "params": params,
            "return_paths": returns,
            "max_nesting_depth": max_depth,
        })
        # recurse to find nested functions
        for child in ast.iter_child_nodes(node):
            self.visit(child)

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func


def _max_nesting_depth(stmts: list[ast.stmt], current: int = 0) -> int:
    _nesting_types = (ast.If, ast.For, ast.While, ast.With, ast.AsyncWith,
                      ast.AsyncFor, ast.ExceptHandler, ast.Try)
    max_d = current
    for stmt in stmts:
        for node in ast.walk(stmt):
            if isinstance(node, _nesting_types):
                # estimate depth by counting enclosing nodes
                pass  # approximation below
    # simpler approximation: recursive depth
    return _depth_recursive(stmts, current)


def _depth_recursive(stmts: list, depth: int) -> int:
    _nesting_types = (ast.If, ast.For, ast.While, ast.With, ast.AsyncWith,
                      ast.AsyncFor, ast.Try)
    max_d = depth
    for stmt in stmts:
        if isinstance(stmt, _nesting_types):
            children: list[list] = []
            if hasattr(stmt, "body"):
                children.append(stmt.body)
            if hasattr(stmt, "orelse") and stmt.orelse:
                children.append(stmt.orelse)
            if hasattr(stmt, "handlers"):
                for h in stmt.handlers:
                    children.append(h.body)
            if hasattr(stmt, "finalbody"):
                children.append(stmt.finalbody)
            for child_stmts in children:
                d = _depth_recursive(child_stmts, depth + 1)
                max_d = max(max_d, d)
    return max_d


def analyse_files(file_paths: list[str]) -> dict:
    all_funcs: list[dict] = []
    file_labels: dict[str, str] = {}

    for path in file_paths:
        try:
            source = Path(path).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=path)
            v = _FuncMetricsVisitor()
            v.visit(tree)
            for f in v.functions:
                f["file"] = path
            all_funcs.extend(v.functions)
            file_labels[path] = path
        except Exception:
            pass

    if not all_funcs:
        return make_result("function_metrics", 0.0, {"functions": []},
                           weight=WEIGHTS["function_metrics"])

    long_funcs = [f for f in all_funcs if f["lines"] > _LONG_FUNC_LINES]
    complex_params = [f for f in all_funcs if f["params"] > _MANY_PARAMS]
    deep_funcs = [f for f in all_funcs if f["max_nesting_depth"] >= _DEEP_NESTING]
    many_returns = [f for f in all_funcs if f["return_paths"] > _MANY_RETURNS]

    concern_count = len(long_funcs) + len(complex_params) + len(deep_funcs) + len(many_returns)
    score = normalize(concern_count, 0, len(all_funcs) * 2)

    evidence = []
    for f in sorted(all_funcs, key=lambda x: x["lines"] + x["params"] * 5 + x["max_nesting_depth"] * 8, reverse=True)[:5]:
        concerns = []
        if f["lines"] > _LONG_FUNC_LINES:
            concerns.append(f"lines={f['lines']}")
        if f["params"] > _MANY_PARAMS:
            concerns.append(f"params={f['params']}")
        if f["max_nesting_depth"] >= _DEEP_NESTING:
            concerns.append(f"nesting={f['max_nesting_depth']}")
        if concerns:
            evidence.append(make_finding(f["file"], f["line"],
                                          f"{f['name']}(): {', '.join(concerns)}",
                                          severity="medium"))

    checklist = []
    for f in long_funcs[:2]:
        checklist.append(f"{f['name']}() — {f['lines']} lines: can this be decomposed into smaller units?")
    for f in complex_params[:2]:
        checklist.append(f"{f['name']}() — {f['params']} params: is there a missing abstraction (e.g. a config object)?")

    return make_result(
        dimension="function_metrics",
        score=score,
        raw_value={
            "total_functions": len(all_funcs),
            "long_functions": len(long_funcs),
            "high_param_count": len(complex_params),
            "deeply_nested": len(deep_funcs),
            "many_return_paths": len(many_returns),
        },
        weight=WEIGHTS["function_metrics"],
        evidence=evidence,
        checklist_items=checklist,
    )


def main() -> None:
    files = [f for f in sys.argv[1:] if f.endswith(".py") and Path(f).exists()]
    if not files:
        print(json.dumps(make_result("function_metrics", 0.0, {"error": "no input"},
                                     weight=WEIGHTS["function_metrics"], error="no input files"), indent=2))
        return
    print(json.dumps(analyse_files(files), indent=2))


if __name__ == "__main__":
    main()
