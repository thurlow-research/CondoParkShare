#!/usr/bin/env python3
"""prompt_audit_logic.py — pure logic for the prompt-artifact audit tool.

SPEC-338 / Issue #338. Per policy #314 (shell launches, Python holds logic),
this module owns the commit-trailer parsing, stats aggregation, and pending-scan
logic formerly embedded in scripts/prompt_audit.sh as git/grep/wc/find pipelines.

GIT BOUNDARY (architect bindings 2 & 3): the SHELL runs git. This module NEVER
spawns git. The shell runs exactly ONE `git log --pretty=format:...` per
invocation, using collision-proof separators %x1e (record / RS, \\x1e) and
%x1f (field / US, \\x1f) — bytes that never occur in commit text — and pipes the
whole output to this module's CLI on stdin. The parsing functions take that
string; they perform no subprocess, network, or git I/O.

PURITY (binding 6 / R4):
  - parse_commit_trailers and the COUNTING half of compute_stats are PURE
    (importable, fixture-testable with plain strings/dicts — no I/O).
  - find_pending_artifacts and the FILE-SCANNING half of compute_stats MAY do
    directory I/O (binding 6 explicitly permits this).
  - Only the __main__ CLI shim reads stdin / writes stdout / touches the
    filesystem for the I/O parts.

STDLIB ONLY (R7): no third-party imports.
"""

from __future__ import annotations

import argparse
import os
import sys

# Record/field separators the shell emits via git's %x1e / %x1f. Exposed as
# function defaults AND as constants so tests can pass custom separators.
RECORD_SEP = "\x1e"
FIELD_SEP = "\x1f"

# Risk levels reported by stats, in fixed display order (parity with legacy).
RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

# Trailer prefixes extracted from a commit body.
_TRAILERS = (
    ("ai_risk", "AI-Risk:"),
    ("prompt_artifact", "Prompt-Artifact:"),
    ("ai_model", "AI-Model:"),
)

# Status markers (preserved exactly per spec §5 non-requirements).
PENDING_MARKER = "⬜ Pending"  # "⬜ Pending"
APPROVED_MARKER = "APPROVED"


# --------------------------------------------------------------------------- #
# PURE: commit-trailer parsing (binding 6 — no I/O)                            #
# --------------------------------------------------------------------------- #
def _extract_trailer(body: str, prefix: str) -> str:
    """Return the value after `prefix` on the first body line that carries it.

    Pure helper. Matches a line whose stripped form starts with `prefix`,
    returning the remainder after the prefix, stripped. Absent -> "".
    """
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix):].strip()
    return ""


def parse_commit_trailers(
    git_log_output: str,
    record_sep: str = RECORD_SEP,
    field_sep: str = FIELD_SEP,
) -> list[dict]:
    """Parse one-pass `git log` output into a list of per-commit dicts.

    Expected per-record field order (set by the shell's --pretty=format):
        [0] hash (%H or %h), [1] date (%ad), [2] subject (%s), [3] body (%b).
    The body field is optional (the filtered --risk pass emits only 3 fields);
    a record with no body yields empty trailer values.

    Each returned dict has keys: hash, date, subject, body, ai_risk,
    prompt_artifact, ai_model. `body` is the raw %b field (retained so stats can
    mirror legacy `git log --grep` substring matching); the trailer keys are the
    structured values for display. Missing fields default to "". Empty records
    (the leading-separator artifact and any trailing whitespace-only record) are
    dropped.

    PURE: no I/O, does not mutate input, never raises on malformed records.
    """
    commits: list[dict] = []
    if not git_log_output:
        return commits

    for record in git_log_output.split(record_sep):
        if not record.strip():
            continue
        fields = record.split(field_sep)
        body = fields[3] if len(fields) > 3 else ""
        commit = {
            "hash": fields[0].strip() if len(fields) > 0 else "",
            "date": fields[1].strip() if len(fields) > 1 else "",
            "subject": fields[2] if len(fields) > 2 else "",
            "body": body,
            "ai_risk": "",
            "prompt_artifact": "",
            "ai_model": "",
        }
        for key, prefix in _TRAILERS:
            commit[key] = _extract_trailer(body, prefix)
        commits.append(commit)
    return commits


# --------------------------------------------------------------------------- #
# Stats aggregation: counting half PURE, file-scan half I/O (binding 6)       #
# --------------------------------------------------------------------------- #
def _scan_artifact_files(prompts_dir: str) -> dict:
    """File-scan half of stats (I/O permitted). Reproduces legacy parity exactly.

    total_artifacts: count of *.md files under prompts_dir (legacy:
        `find prompts -name "*.md" | wc -l`).
    pending / approved: count of ANY file under prompts_dir whose content
        contains the marker (legacy used `grep -rl ... prompts`, NOT *.md-only).
    Unreadable files are skipped (legacy used `2>/dev/null`).
    """
    total_artifacts = 0
    pending = 0
    approved = 0
    for root, _dirs, files in os.walk(prompts_dir):
        for name in files:
            path = os.path.join(root, name)
            if name.endswith(".md"):
                total_artifacts += 1
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
            except OSError:
                continue
            if PENDING_MARKER in content:
                pending += 1
            if APPROVED_MARKER in content:
                approved += 1
    return {
        "total_artifacts": total_artifacts,
        "pending": pending,
        "approved": approved,
    }


def compute_stats(commit_list: list[dict], prompts_dir: str | None = None) -> dict:
    """Aggregate audit statistics. Counting half PURE; file half I/O (binding 6).

    commit_list is the output of parse_commit_trailers over the UNION git pass
    (`--grep="Prompt-Artifact:" --grep="AI-Risk:"`). Counting mirrors legacy
    `git log --grep=<PATTERN>` semantics EXACTLY — i.e. the pattern is matched as
    a SUBSTRING anywhere in the commit message (subject + body), including prose
    mentions — so the numbers match the bash implementation (R5 parity), not a
    stricter trailer-line interpretation:
      total_commits = records whose message contains "Prompt-Artifact:" (legacy:
          `git log --grep="Prompt-Artifact:" --oneline | wc -l`).
      by_risk       = per-level count of records whose message contains
          "AI-Risk: LEVEL" (legacy: `git log --grep="AI-Risk: LEVEL" ...`).

    File metrics are filled only when prompts_dir is given and exists; otherwise
    they are None and prompts_present is False (shell prints the legacy
    "No prompts/ directory found." line).
    """

    def _msg(c: dict) -> str:
        return f"{c.get('subject', '')}\n{c.get('body', '')}"

    total_commits = sum(1 for c in commit_list if "Prompt-Artifact:" in _msg(c))
    by_risk = {
        level: sum(1 for c in commit_list if f"AI-Risk: {level}" in _msg(c))
        for level in RISK_LEVELS
    }

    stats = {
        "total_commits": total_commits,
        "by_risk": by_risk,
        "prompts_present": False,
        "total_artifacts": None,
        "pending": None,
        "approved": None,
    }

    if prompts_dir and os.path.isdir(prompts_dir):
        stats["prompts_present"] = True
        stats.update(_scan_artifact_files(prompts_dir))
    return stats


# --------------------------------------------------------------------------- #
# Pending scan: directory I/O (binding 6)                                      #
# --------------------------------------------------------------------------- #
def find_pending_artifacts(artifacts_dir: str) -> list[str]:
    """Return sorted paths of *.md files under artifacts_dir containing the
    pending marker. Reproduces legacy `pending` mode (*.md-only). Missing dir
    -> []. Unreadable files skipped. I/O permitted (binding 6).
    """
    pending: list[str] = []
    if not artifacts_dir or not os.path.isdir(artifacts_dir):
        return pending
    for root, _dirs, files in os.walk(artifacts_dir):
        for name in files:
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
            except OSError:
                continue
            if PENDING_MARKER in content:
                pending.append(path)
    return sorted(pending)


# --------------------------------------------------------------------------- #
# Formatting helpers (pure string -> string)                                  #
# --------------------------------------------------------------------------- #
def format_list(commit_list: list[dict], limit: int) -> str:
    """Render commits as the legacy `list` body (no header). Pure.

    Per commit: "<hash> <date> <subject>". When ai_risk is present, a second
    indented line "  AI-Risk: <value>" follows (legacy printed the raw grep'd
    AI-Risk line indented two spaces). Filtered records (no trailer) print one
    line only.
    """
    lines: list[str] = []
    for commit in commit_list[:limit]:
        lines.append(
            f"{commit['hash']} {commit['date']} {commit['subject']}".rstrip()
        )
        if commit["ai_risk"]:
            lines.append(f"  AI-Risk: {commit['ai_risk']}")
    return "\n".join(lines)


def format_stats(stats: dict) -> str:
    """Render the stats body (no header). Pure."""
    lines = [f"AI-assisted commits (all time): {stats['total_commits']}"]
    for level in RISK_LEVELS:
        lines.append(f"  {level}: {stats['by_risk'][level]}")
    lines.append("")
    if stats["prompts_present"]:
        lines.append(f"Prompt artifacts: {stats['total_artifacts']}")
        lines.append(f"  Pending review: {stats['pending']}")
        lines.append(f"  Approved:       {stats['approved']}")
    else:
        lines.append("No prompts/ directory found.")
    return "\n".join(lines)


def format_pending(paths: list[str]) -> str:
    """Render the pending body (no header). Pure."""
    lines = [f"  {p}" for p in paths]
    lines.append("")
    lines.append(f"  {len(paths)} artifact(s) pending review")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI shim — the ONLY I/O site (binding 4).                                    #
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prompt-artifact audit logic (SPEC-338). The shell runs git "
        "and pipes one-pass `git log` output on stdin; this tool parses it."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="format AI-assisted commits from stdin")
    p_list.add_argument("--limit", type=int, default=60)

    p_stats = sub.add_parser("stats", help="aggregate stats from stdin + dir")
    p_stats.add_argument("--prompts-dir", default=None)

    p_pending = sub.add_parser("pending", help="scan a dir for pending artifacts")
    p_pending.add_argument("--prompts-dir", required=True)

    args = parser.parse_args(argv)

    if args.command == "list":
        commits = parse_commit_trailers(sys.stdin.read())
        out = format_list(commits, args.limit)
        if out:
            sys.stdout.write(out + "\n")
        return 0

    if args.command == "stats":
        commits = parse_commit_trailers(sys.stdin.read())
        stats = compute_stats(commits, args.prompts_dir)
        sys.stdout.write(format_stats(stats) + "\n")
        return 0

    if args.command == "pending":
        paths = find_pending_artifacts(args.prompts_dir)
        sys.stdout.write(format_pending(paths) + "\n")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
