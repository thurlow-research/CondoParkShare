#!/usr/bin/env python3
"""
diff_size.py — Diff-size risk-tier floor and multi-purpose split trigger (#377).

Watanabe et al. (2026) found agentic PRs are empirically larger and far more
frequently multi-purpose than human PRs, and reviewers reject oversized agent
PRs because review becomes impractical. The intra-file complexity validators
(rn_calculator, cyclomatic, cognitive) have no signal for the raw size or
topical breadth of a diff. This validator adds two deterministic rules:

  R1 — Diff-size floor: changed_lines > HOS_DIFF_SIZE_FLOOR OR
       changed_files > HOS_FILE_COUNT_FLOOR  →  tier_floor="HIGH".
       (A discrete promotion signal the risk-assessor reads; it does NOT
       change the numeric score and does NOT block the build.)
  R2 — Split trigger: distinct top-level domains >= HOS_DOMAIN_SPLIT_THRESHOLD
       →  advisory in checklist_items. Advisory only; never sets tier_floor.

This validator performs NO git calls and NO filesystem/content reads. All diff
metadata arrives via CLI flags from run_validators.sh (architect binding #3):

  diff_size.py --changed-lines N --changed-files N \
               --changed-file-list f1 f2 f3 ...

Zero values for changed-lines/changed-files mean "data unavailable" and the
floor does NOT fire (REQ-377-18). stderr carries [diff-size] rule-fired
messages; stdout is always a single make_result-conformant JSON object.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path as _Path

# self-bootstrap: make this file's dir (with schema.py) importable regardless
# of caller cwd/PYTHONPATH (run_validators, run_panel, direct invocation).
sys.path.insert(0, str(_Path(__file__).resolve().parent))
from schema import WEIGHTS, make_result  # noqa: E402

# ── Env-var threshold defaults (binding #8, REQ-377-16) ──────────────────────
_DEFAULT_DIFF_SIZE_FLOOR = 400
_DEFAULT_FILE_COUNT_FLOOR = 15
_DEFAULT_DOMAIN_SPLIT_THRESHOLD = 3

# ── Default domain map (binding #5 — HOS source layout; ordered, first wins) ──
# A path matching no prefix falls through to the implicit catch-all "other".
_DEFAULT_DOMAIN_MAP: list[tuple[str, str]] = [
    ("scripts/", "scripts"),
    (".claude/agents/", "agents"),
    ("docs/", "docs"),
    ("packs/", "packs"),
    ("bootstrap/", "bootstrap"),
    ("contract/", "contract"),
    ("audit/", "audit"),
]


def _log(msg: str) -> None:
    """Write a [diff-size] line to stderr (never contaminates stdout JSON)."""
    print(f"[diff-size] {msg}", file=sys.stderr)


def _read_positive_int_env(name: str, default: int) -> int:
    """
    Read a positive-integer threshold from the environment (REQ-377-17/18).

    Absent → default silently. Present but not a positive int (non-numeric,
    <= 0, or fractional) → [diff-size] warning + default. A configured 0 is
    invalid and falls back.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (ValueError, TypeError):
        _log(f"warning: {name}={raw!r} is not an integer; using default {default}")
        return default
    if value <= 0:
        _log(f"warning: {name}={raw!r} is not a positive integer; using default {default}")
        return default
    return value


def parse_domain_map() -> list[tuple[str, str]]:
    """
    Resolve the active domain map (binding #4, REQ-377-15).

    HOS_DOMAIN_MAP format: "prefix=label;prefix=label;...". When set and
    well-formed it REPLACES the default map (order = match order). Malformed
    (any entry lacking exactly one '=', empty prefix, or empty label, or zero
    usable entries) → [diff-size] warning + full fallback to the default map.
    """
    raw = os.environ.get("HOS_DOMAIN_MAP")
    if raw is None:
        return _DEFAULT_DOMAIN_MAP

    entries: list[tuple[str, str]] = []
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue  # tolerate trailing/empty segments between separators
        parts = chunk.split("=")
        if len(parts) != 2:
            _log(
                f"warning: HOS_DOMAIN_MAP entry {chunk!r} is malformed "
                f"(expected 'prefix=label'); falling back to default map"
            )
            return _DEFAULT_DOMAIN_MAP
        prefix, label = parts[0].strip(), parts[1].strip()
        if not prefix or not label:
            _log(
                f"warning: HOS_DOMAIN_MAP entry {chunk!r} has empty prefix or label; "
                f"falling back to default map"
            )
            return _DEFAULT_DOMAIN_MAP
        entries.append((prefix, label))

    if not entries:
        _log("warning: HOS_DOMAIN_MAP produced no usable entries; falling back to default map")
        return _DEFAULT_DOMAIN_MAP
    return entries


def classify_domain(path: str, domain_map: list[tuple[str, str]]) -> str:
    """Assign a path to exactly one domain label; first matching prefix wins."""
    for prefix, label in domain_map:
        if path.startswith(prefix):
            return label
    return "other"


def detect_domains(
    file_list: list[str], domain_map: list[tuple[str, str]]
) -> tuple[int, list[str]]:
    """
    Return (domain_count, domains_detected) in first-appearance order.

    domains_detected holds the distinct labels; "other" collapses to a single
    label however many files land there (REQ-377-11/14).
    """
    domains: list[str] = []
    for path in file_list:
        label = classify_domain(path, domain_map)
        if label not in domains:
            domains.append(label)
    return len(domains), domains


def evaluate(
    changed_lines: int,
    changed_files: int,
    file_list: list[str],
    diff_size_floor: int,
    file_count_floor: int,
    domain_split_threshold: int,
    domain_map: list[tuple[str, str]],
) -> dict:
    """Apply R1 (floor) and R2 (split trigger); return a make_result envelope."""
    # ── R1: diff-size floor (strict >, zero = data-unavailable) ──────────────
    lines_fires = changed_lines > 0 and changed_lines > diff_size_floor
    files_fires = changed_files > 0 and changed_files > file_count_floor

    if lines_fires or files_fires:
        tier_floor: str | None = "HIGH"
        if lines_fires and files_fires:
            floor_rule_fired: str | None = "both"
        elif lines_fires:
            floor_rule_fired = "changed_lines"
        else:
            floor_rule_fired = "changed_files"
    else:
        tier_floor = None
        floor_rule_fired = None

    if lines_fires:
        _log(
            f"tier_floor=HIGH: changed_lines={changed_lines} > "
            f"HOS_DIFF_SIZE_FLOOR={diff_size_floor}"
        )
    if files_fires:
        _log(
            f"tier_floor=HIGH: changed_files={changed_files} > "
            f"HOS_FILE_COUNT_FLOOR={file_count_floor}"
        )

    # ── R2: multi-purpose split trigger (advisory only) ──────────────────────
    domain_count, domains_detected = detect_domains(file_list, domain_map)
    checklist_items: list[str] = []
    if domain_count >= domain_split_threshold:
        domains_str = ", ".join(domains_detected)
        checklist_items.append(
            f"Consider splitting into focused PRs: changes span {domain_count} domains "
            f"(threshold {domain_split_threshold}); domains: {domains_str}"
        )
        _log(
            f"split advisory: domain_count={domain_count} >= "
            f"HOS_DOMAIN_SPLIT_THRESHOLD={domain_split_threshold} "
            f"(domains: {domains_str})"
        )

    return make_result(
        dimension="diff_size",
        score=0.0,  # inert: WEIGHTS["diff_size"] == 0.0; floor is a discrete signal
        raw_value={
            "changed_lines": changed_lines,
            "changed_files": changed_files,
            "floor_rule_fired": floor_rule_fired,
            "domain_count": domain_count,
            "domains_detected": domains_detected,
            "thresholds": {
                "diff_size_floor": diff_size_floor,
                "file_count_floor": file_count_floor,
                "domain_split_threshold": domain_split_threshold,
            },
        },
        weight=WEIGHTS["diff_size"],
        checklist_items=checklist_items,
        tier_floor=tier_floor,
    )


def _parse_int_flag(value: str | None, flag: str) -> int:
    """Parse a CLI integer flag; non-integer → [diff-size] warning + 0 (unavailable)."""
    if value is None:
        return 0
    try:
        return int(value)
    except (ValueError, TypeError):
        _log(f"warning: {flag}={value!r} is not an integer; treating as 0 (data unavailable)")
        return 0


def parse_args(argv: list[str]) -> tuple[int, int, list[str]]:
    """
    Parse the CLI (binding #3). --changed-file-list is variadic and trailing:
    it consumes every remaining token.

      --changed-lines N --changed-files N --changed-file-list f1 f2 f3 ...
    """
    changed_lines_raw: str | None = None
    changed_files_raw: str | None = None
    file_list: list[str] = []

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--changed-lines":
            changed_lines_raw = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
        elif arg == "--changed-files":
            changed_files_raw = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
        elif arg == "--changed-file-list":
            # Variadic, trailing: consume everything that follows.
            file_list = [p for p in argv[i + 1 :] if p]
            break
        else:
            # Unknown bare token before the file list — ignore defensively.
            i += 1

    changed_lines = _parse_int_flag(changed_lines_raw, "--changed-lines")
    changed_files = _parse_int_flag(changed_files_raw, "--changed-files")
    return changed_lines, changed_files, file_list


def main() -> None:
    changed_lines, changed_files, file_list = parse_args(sys.argv[1:])

    diff_size_floor = _read_positive_int_env("HOS_DIFF_SIZE_FLOOR", _DEFAULT_DIFF_SIZE_FLOOR)
    file_count_floor = _read_positive_int_env("HOS_FILE_COUNT_FLOOR", _DEFAULT_FILE_COUNT_FLOOR)
    domain_split_threshold = _read_positive_int_env(
        "HOS_DOMAIN_SPLIT_THRESHOLD", _DEFAULT_DOMAIN_SPLIT_THRESHOLD
    )
    domain_map = parse_domain_map()

    result = evaluate(
        changed_lines=changed_lines,
        changed_files=changed_files,
        file_list=file_list,
        diff_size_floor=diff_size_floor,
        file_count_floor=file_count_floor,
        domain_split_threshold=domain_split_threshold,
        domain_map=domain_map,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
