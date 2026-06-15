#!/usr/bin/env python3
"""
complexity_metrics.py — cyclomatic and cognitive complexity via radon.

Cyclomatic (McCabe): measures testability — number of independent execution paths.
Cognitive (Campbell 2018): measures understandability — how hard is this to read?

These are independent signals. High cyclomatic + low test coverage = risky.
High cognitive = reviewers are more likely to miss bugs while reading.

Usage: python complexity_metrics.py file.py [file2.py ...]
"""

from __future__ import annotations

import json
import pathlib as _hos_pl
import subprocess

# self-bootstrap: ensure this file's dir (with schema.py) is importable
# regardless of caller cwd/PYTHONPATH (run_validators, run_panel, direct).
import sys
import sys as _hos_sys
from pathlib import Path

_hos_sys.path.insert(0, str(_hos_pl.Path(__file__).resolve().parent))
from schema import WEIGHTS, make_finding, make_result, normalize  # noqa: E402

# Thresholds for score normalization
_CC_HIGH = 15  # cyclomatic complexity ≥15 → score 1.0
_COGN_HIGH = 20  # cognitive complexity ≥20 → score 1.0


def _run(cmd: list[str]) -> tuple[str, str, int]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout, result.stderr, result.returncode


def _radon_cc(files: list[str]) -> list[dict]:
    stdout, _, rc = _run(["radon", "cc", "-j", "-s"] + files)
    if rc != 0 or not stdout.strip():
        return []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    functions = []
    for fpath, entries in data.items():
        for entry in entries:
            functions.append(
                {
                    "file": fpath,
                    "name": entry.get("name", "?"),
                    "line": entry.get("lineno", 0),
                    "cyclomatic": entry.get("complexity", 0),
                }
            )
    return functions


def _radon_mi(files: list[str]) -> dict[str, float]:
    """Maintainability index per file (0–100; lower = worse)."""
    stdout, _, rc = _run(["radon", "mi", "-j"] + files)
    if rc != 0 or not stdout.strip():
        return {}
    try:
        data = json.loads(stdout)
        return {k: v.get("mi", 100.0) if isinstance(v, dict) else v for k, v in data.items()}
    except (json.JSONDecodeError, AttributeError):
        return {}


def analyse_files(file_paths: list[str]) -> dict:
    try:
        subprocess.run(["radon", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return make_result(
            "complexity",
            0.0,
            {"error": "radon not installed"},
            weight=WEIGHTS["cyclomatic"],
            error="radon not installed — run: pip install radon",
        )

    cc_funcs = _radon_cc(file_paths)
    mi_map = _radon_mi(file_paths)

    if not cc_funcs:
        return make_result("complexity", 0.0, {"functions": []}, weight=WEIGHTS["cyclomatic"])

    max_cc = max(f["cyclomatic"] for f in cc_funcs)
    mean_cc = sum(f["cyclomatic"] for f in cc_funcs) / len(cc_funcs)
    high_cc = [f for f in cc_funcs if f["cyclomatic"] >= 10]

    cc_score = normalize(max_cc, 1, _CC_HIGH)

    evidence = [
        make_finding(
            f["file"],
            f["line"],
            f"cyclomatic={f['cyclomatic']} — {f['name']}()",
            severity="high" if f["cyclomatic"] >= 10 else "medium",
        )
        for f in sorted(cc_funcs, key=lambda x: x["cyclomatic"], reverse=True)[:5]
    ]

    checklist = []
    for f in sorted(cc_funcs, key=lambda x: x["cyclomatic"], reverse=True)[:2]:
        if f["cyclomatic"] >= 10:
            checklist.append(
                f"{f['name']}() — cyclomatic={f['cyclomatic']}: "
                "verify all independent paths have test coverage"
            )

    min_mi = min(mi_map.values()) if mi_map else 100.0
    mi_concern = min_mi < 50

    return make_result(
        dimension="complexity",
        score=cc_score,
        raw_value={
            "max_cyclomatic": max_cc,
            "mean_cyclomatic": round(mean_cc, 2),
            "high_complexity_functions": [f["name"] for f in high_cc],
            "min_maintainability_index": round(min_mi, 1),
            "maintainability_concern": mi_concern,
        },
        weight=WEIGHTS["cyclomatic"],
        evidence=evidence,
        checklist_items=checklist,
    )


def main() -> None:
    files = [f for f in sys.argv[1:] if f.endswith(".py") and Path(f).exists()]
    if not files:
        print(
            json.dumps(
                make_result(
                    "complexity",
                    0.0,
                    {"error": "no Python files"},
                    weight=WEIGHTS["cyclomatic"],
                    error="no input files",
                ),
                indent=2,
            )
        )
        return
    print(json.dumps(analyse_files(files), indent=2))


if __name__ == "__main__":
    main()
