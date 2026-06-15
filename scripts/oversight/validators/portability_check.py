#!/usr/bin/env python3
"""
portability_check.py — detect portability defects that prevent code from
running on any host other than the developer's own machine.

Checks:
  1. Hardcoded absolute paths with user-home segments
       /Users/<name>/...  /home/<name>/...  C:\\Users\\<name>\\...
  2. spec_from_file_location() calls with hardcoded absolute paths
       (the load-without-importing workaround pattern that embeds a
        machine-specific path to dodge a naming collision)

Both patterns indicate the developer worked around a structural problem
(e.g. a stdlib-shadowing module name) rather than fixing the root cause.
A single finding scores HIGH so the review chain must address it explicitly.

Usage: python portability_check.py file.py [file2.py ...]
"""

from __future__ import annotations

import pathlib as _hos_pl
import re

# self-bootstrap: ensure this file's dir (with schema.py) is importable
# regardless of caller cwd/PYTHONPATH (run_validators, run_panel, direct).
import sys
import sys as _hos_sys
from pathlib import Path

_hos_sys.path.insert(0, str(_hos_pl.Path(__file__).resolve().parent))
from schema import WEIGHTS, make_finding, make_result  # noqa: E402

DIMENSION = "portability"

# Patterns that indicate machine-specific absolute paths embedded in source.
# Each entry is (compiled_re, human label).
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"""['"/](?:Users|home)/\w+/"""),
        "hardcoded user-home path (/Users/<name>/ or /home/<name>/)",
    ),
    (
        re.compile(r"""C:\\[Uu]sers\\"""),
        "hardcoded Windows user-home path (C:\\Users\\...)",
    ),
    (
        re.compile(
            r"""spec_from_file_location\s*\(\s*['"][^'"]*['"]\s*,\s*['"][/\\]""",
            re.DOTALL,
        ),
        "spec_from_file_location() with hardcoded absolute path",
    ),
]


def _scan_file(path: str) -> list[dict]:
    findings = []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return findings

    for lineno, line in enumerate(lines, start=1):
        for pattern, label in _PATTERNS:
            if pattern.search(line):
                findings.append(
                    make_finding(
                        file=path,
                        line=lineno,
                        message=f"{label}: {line.strip()[:120]}",
                        severity="high",
                    )
                )
                break  # one finding per line per file is enough

    return findings


def main(files: list[str]) -> dict:
    all_findings: list[dict] = []
    for f in files:
        all_findings.extend(_scan_file(f))

    count = len(all_findings)

    # Score: 0 findings → 0.0 (clean).
    # 1 finding → 0.55 (HIGH floor — must be reviewed, cannot quietly pass).
    # Each additional finding adds 0.15 up to 1.0.
    if count == 0:
        score = 0.0
    else:
        score = min(1.0, 0.55 + (count - 1) * 0.15)

    checklist = []
    if all_findings:
        checklist = [
            "Is every hardcoded absolute path replaceable with a relative path "
            "or importlib.resources?",
            "If spec_from_file_location is used to dodge a naming collision, "
            "fix the root name conflict instead.",
            "Will these tests pass on CI, Linux, and a fresh checkout?",
        ]

    return make_result(
        dimension=DIMENSION,
        score=score,
        raw_value={"finding_count": count},
        weight=WEIGHTS.get(DIMENSION, 0.06),
        evidence=all_findings,
        checklist_items=checklist,
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('{"error": "no files provided"}')
        sys.exit(1)

    result = main(sys.argv[1:])
    import json

    print(json.dumps(result, indent=2))
