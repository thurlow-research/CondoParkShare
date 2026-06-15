#!/usr/bin/env python3
"""
issue_query.py — historical bug density from GitHub issues and git churn.

Two signals:
  1. GitHub issues mentioning these file paths (labelled 'bug', 'security-finding',
     'privacy-finding', 'design-concern', 'spec-gap') → bug density
  2. Git log churn: how frequently has each file been modified? (high churn = likely
     persistently complex or repeatedly buggy)

Both start empty on a new project and accumulate value over time.

Usage: python issue_query.py file.py [file2.py ...]
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

_RISK_LABELS = [
    "bug",
    "security-finding",
    "privacy-finding",
    "design-concern",
    "spec-gap",
    "test-resistance",
    "escaped-defect",
]


def _gh_issues_for_files(file_paths: list[str]) -> list[dict]:
    """Query GitHub issues mentioning any of the given file paths."""
    try:
        subprocess.run(["gh", "issue", "list", "--help"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []

    all_issues: list[dict] = []
    for label in _RISK_LABELS:
        try:
            result = subprocess.run(
                [
                    "gh",
                    "issue",
                    "list",
                    "--label",
                    label,
                    "--state",
                    "all",
                    "--limit",
                    "200",
                    "--json",
                    "number,title,labels,body,url",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                continue
            issues = json.loads(result.stdout)
            for issue in issues:
                body = issue.get("body", "") or ""
                for fp in file_paths:
                    filename = Path(fp).name
                    if filename in body or fp in body:
                        issue["matched_file"] = fp
                        issue["matched_label"] = label
                        all_issues.append(issue)
                        break
        except (
            json.JSONDecodeError,
            subprocess.TimeoutExpired,
            FileNotFoundError,
            subprocess.CalledProcessError,
        ):
            continue

    # Deduplicate by issue number
    seen: set[int] = set()
    unique: list[dict] = []
    for issue in all_issues:
        if issue["number"] not in seen:
            seen.add(issue["number"])
            unique.append(issue)
    return unique


def _git_churn(file_paths: list[str]) -> dict[str, int]:
    """Count number of commits touching each file in the last 90 days."""
    churn: dict[str, int] = {}
    for fp in file_paths:
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "--follow", "--since=90.days", "--", fp],
                capture_output=True,
                text=True,
                timeout=10,
            )
            churn[fp] = len(result.stdout.strip().splitlines())
        except subprocess.TimeoutExpired:
            churn[fp] = 0
    return churn


def analyse_files(file_paths: list[str]) -> dict:
    issues = _gh_issues_for_files(file_paths)
    churn = _git_churn(file_paths)

    issue_count = len(issues)
    max_churn = max(churn.values()) if churn else 0

    # Weight: many issues = high score; high churn contributes moderately
    issue_score = normalize(issue_count, 0, 8)
    churn_score = normalize(max_churn, 0, 20)
    score = issue_score * 0.7 + churn_score * 0.3

    evidence = [
        make_finding(
            issue.get("matched_file", "?"),
            0,
            f"[{issue['matched_label']}] #{issue['number']}: {issue['title']}",
            severity=(
                "high"
                if issue["matched_label"] in ("security-finding", "escaped-defect")
                else "medium"
            ),
        )
        for issue in issues[:10]
    ]

    high_churn = {fp: c for fp, c in churn.items() if c >= 5}
    for fp, c in sorted(high_churn.items(), key=lambda x: x[1], reverse=True)[:3]:
        evidence.append(
            make_finding(fp, 0, f"high churn: {c} commits in 90 days", severity="medium")
        )

    checklist = []
    if issues:
        checklist.append(
            f"{issue_count} historical issue(s) reference these files — "
            "review prior bugs before approving similar logic."
        )
    if high_churn:
        most_churned = max(high_churn, key=lambda k: high_churn[k])
        checklist.append(
            f"{Path(most_churned).name}: {high_churn[most_churned]} commits in 90 days — "
            "high churn may indicate persistent complexity."
        )

    return make_result(
        dimension="historical_density",
        score=score,
        raw_value={
            "issue_count": issue_count,
            "issues": [
                {
                    "number": i["number"],
                    "title": i["title"],
                    "label": i["matched_label"],
                    "file": i.get("matched_file"),
                }
                for i in issues
            ],
            "churn": churn,
            "note": "empty on new projects — accumulates value over time",
        },
        weight=WEIGHTS["historical_density"],
        evidence=evidence,
        checklist_items=checklist,
    )


def main() -> None:
    files = [f for f in sys.argv[1:] if Path(f).exists()]
    if not files:
        print(
            json.dumps(
                make_result(
                    "historical_density",
                    0.0,
                    {"error": "no input"},
                    weight=WEIGHTS["historical_density"],
                    error="no input files",
                ),
                indent=2,
            )
        )
        return
    print(json.dumps(analyse_files(files), indent=2))


if __name__ == "__main__":
    main()
