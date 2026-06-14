#!/usr/bin/env python3
"""
static_analysis.py — bandit security findings as a risk score.

HIGH severity bandit findings are handled by the gate script (security_scan.sh)
as a blocking check. This validator collects MEDIUM findings as a risk signal
that feeds the composite score without blocking the pipeline.

Optionally runs semgrep with Django security rules if available.

Usage: python static_analysis.py file.py [file2.py ...]
"""

from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

# self-bootstrap: ensure this file's dir (with schema.py) is importable
# regardless of caller cwd/PYTHONPATH (run_validators, run_panel, direct).
import sys as _hos_sys
import pathlib as _hos_pl
_hos_sys.path.insert(0, str(_hos_pl.Path(__file__).resolve().parent))
from schema import make_result, make_finding, normalize, WEIGHTS  # noqa: E402


def _run_bandit(files: list[str]) -> list[dict]:
    try:
        result = subprocess.run(
            ["bandit", "-f", "json", "-ll", "-ii"] + files,
            capture_output=True,
            text=True,
        )
        data = json.loads(result.stdout)
        return data.get("results", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _run_semgrep(files: list[str]) -> list[dict]:
    try:
        subprocess.run(["semgrep", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []
    try:
        result = subprocess.run(
            ["semgrep", "--config", "p/django", "--json", "--quiet"] + files,
            capture_output=True,
            text=True,
            timeout=60,
        )
        data = json.loads(result.stdout)
        return data.get("results", [])
    except (json.JSONDecodeError, subprocess.TimeoutExpired):
        return []


_BANDIT_SEVERITY_SCORE = {"MEDIUM": 0.4, "LOW": 0.2}
_BANDIT_CONF_MULTIPLIER = {"HIGH": 1.0, "MEDIUM": 0.8, "LOW": 0.6}


def analyse_files(file_paths: list[str]) -> dict:
    bandit_results = _run_bandit(file_paths)
    semgrep_results = _run_semgrep(file_paths)

    # Only score MEDIUM severity (HIGH is a gate-level block)
    medium_findings = [r for r in bandit_results if r.get("issue_severity", "LOW") == "MEDIUM"]

    evidence = [
        make_finding(
            r.get("filename", "?"),
            r.get("line_number", 0),
            f"[{r.get('test_id', '?')}] {r.get('issue_text', '')} "
            f"(conf={r.get('issue_confidence', '?')})",
            severity="medium",
        )
        for r in medium_findings[:10]
    ]

    for r in semgrep_results[:5]:
        loc = r.get("start", {})
        evidence.append(
            make_finding(
                r.get("path", "?"),
                loc.get("line", 0),
                f"[semgrep:{r.get('check_id', '?')}] {r.get('extra', {}).get('message', '')}",
                severity="medium",
            )
        )

    total_findings = len(medium_findings) + len(semgrep_results)
    score = normalize(total_findings, 0, 10)

    checklist = []
    seen_ids: set[str] = set()
    for r in medium_findings[:3]:
        test_id = r.get("test_id", "")
        if test_id not in seen_ids:
            seen_ids.add(test_id)
            checklist.append(
                f"{r.get('filename', '?')}:{r.get('line_number', 0)} "
                f"[{test_id}] — {r.get('issue_text', '')}"
            )

    return make_result(
        dimension="static_analysis",
        score=score,
        raw_value={
            "bandit_medium_count": len(medium_findings),
            "semgrep_count": len(semgrep_results),
        },
        weight=WEIGHTS["static_analysis"],
        evidence=evidence,
        checklist_items=checklist,
    )


def main() -> None:
    files = [f for f in sys.argv[1:] if f.endswith(".py") and Path(f).exists()]
    if not files:
        print(
            json.dumps(
                make_result(
                    "static_analysis",
                    0.0,
                    {"error": "no input"},
                    weight=WEIGHTS["static_analysis"],
                    error="no input files",
                ),
                indent=2,
            )
        )
        return
    print(json.dumps(analyse_files(files), indent=2))


if __name__ == "__main__":
    main()
