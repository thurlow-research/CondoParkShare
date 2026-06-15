#!/usr/bin/env python3
"""signoff_gate.py — validation-suite sign-off gate (HOS framework script).

Blocks a change from merging (CI / PR mode) or deploying (deploy mode) unless
every agent in the validation suite has a *committed* sign-off stamp that is no
older than every changed source file.

The authoritative clock is the **git commit timestamp**, not the file's mtime on
disk. The commit timestamp is set when `git commit` runs, so the supported
workflow is:

  1. Make changes (not yet committed).
  2. Run the validation suite → each agent writes signoffs/<role>.stamp.
  3. git add -A && git commit        ← changed files AND stamps share commit time T.
  4. Push.
  5. Gate: max(changed-file commit time) <= min(stamp commit time)  → PASS.

Two-commit variant (commit code at T1, then commit stamps at T2 > T1) also
passes. The only case that fails is committing *new* changes after a stamp
without re-signing — exactly what the gate exists to catch.

Required roles are read from contract/step-manifest.yaml: the union of every
step's `required_signoffs`, mapped to agent names via `role_mappings`. That
manifest is the single source of truth for who is in the validation suite.

Modes:
  --base <ref>   PR/CI mode. Compared file set = files changed vs. merge-base(ref).
  --all          Deploy mode. Compared file set = every tracked file.

A stamp's status must be APPROVED, CONDITIONAL, or NOT_APPLICABLE. NOT_APPLICABLE
still has to be re-affirmed (re-committed) after later changes, so a role can
never silently fall behind.

Exit 0 = gate passes. Exit 1 = gate fails. Exit 2 = usage / environment error.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - surfaced as an env error
    # Auto-detect the oversight venv before giving up.  On macOS Homebrew Python
    # 3.14+ and Ubuntu 24.04+ (PEP 668), the system Python has no user packages,
    # so bare `python3 signoff_gate.py` fails.  The oversight venv has PyYAML
    # (a declared dependency in requirements.txt); os.execv replaces this process
    # with the venv Python running the same script — argv, cwd, and exit code all
    # propagate naturally.
    import os

    _venv_py = (Path(__file__).parent / ".venv" / "bin" / "python3").resolve()
    # Loop guard: only re-exec if we are NOT already running as the venv Python.
    # If we are (venv exists but somehow lacks PyYAML — a partial install), a
    # naive `if _venv_py.exists(): execv` would re-exec into ourselves forever.
    # Fall through to the explicit error instead of spinning.
    _already_venv = False
    try:
        _already_venv = _venv_py.exists() and _venv_py.samefile(sys.executable)
    except OSError:
        _already_venv = False
    if _venv_py.exists() and not _already_venv:
        os.execv(str(_venv_py), [str(_venv_py)] + sys.argv)
    sys.stderr.write(
        "signoff_gate: PyYAML is required but missing from the oversight venv.\n"
        "  Repair the venv:  ./scripts/oversight/ensure_venv.sh\n"
        "  Or install:       pip install pyyaml\n"
        f"  Or run via:       {_venv_py} {__file__}\n"
    )
    sys.exit(2)

SIGNOFFS_DIR = "signoffs"
# Oversight-generated, append-only artifacts. The system writes these *about* a
# step — sign-off stamps, the committed audit trail, ephemeral agent state — and
# often does so AFTER reviewers have signed (suspension-census, second-review,
# and the orchestrator all append to audit/oversight-log.jsonl). They are not
# source changes, so a stamp need not be newer than them. Excluding them is what
# stops the oversight tooling's own bookkeeping from perpetually invalidating the
# sign-offs it records. (HOS#112)
OVERSIGHT_ARTIFACT_PREFIXES = (
    f"{SIGNOFFS_DIR}/",
    "audit/",
    ".claudetmp/",
)
# A stamp records a *satisfied* role: APPROVED, CONDITIONAL (human verifies the
# conditional item before merge), or NOT_APPLICABLE (role explicitly out of
# scope for the change — the stamp-level equivalent of the N/A register entry,
# HOS#58). ESCALATED is intentionally NOT a stamp state: an unresolved
# escalation is by definition not satisfied, so it has no passing stamp and the
# gate fails (missing stamp) until the escalation is resolved and the role is
# re-signed. A human-authorized waiver is handled by gate suspension (HOS#22),
# not by a stamp. See signoffs/README.md.
VALID_STATUSES = {"APPROVED", "CONDITIONAL", "NOT_APPLICABLE", "NA"}
STAMP_SUFFIX = ".stamp"


def run_git(args: list[str], cwd: Path) -> str:
    """Run a git command and return stripped stdout (empty string on failure)."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def repo_root(start: Path) -> Path:
    root = run_git(["rev-parse", "--show-toplevel"], start)
    if not root:
        sys.stderr.write("signoff_gate: not inside a git repository.\n")
        sys.exit(2)
    return Path(root)


def commit_time(root: Path, path: str) -> int:
    """Commit timestamp (epoch seconds) of the last commit to touch `path`.

    Returns 0 when the path has no committed history (e.g. written but not yet
    committed) — callers treat 0 as 'not committed'.
    """
    out = run_git(["log", "-1", "--format=%ct", "--", path], root)
    return int(out) if out.isdigit() else 0


def load_required_roles(manifest_path: Path) -> tuple[list[str], dict[str, str]]:
    """Return (sorted required role keys, role -> agent-name map) from the manifest."""
    try:
        manifest = yaml.safe_load(manifest_path.read_text())
    except FileNotFoundError:
        sys.stderr.write(f"signoff_gate: manifest not found: {manifest_path}\n")
        sys.exit(2)
    except yaml.YAMLError as exc:
        sys.stderr.write(f"signoff_gate: cannot parse manifest: {exc}\n")
        sys.exit(2)

    role_map = manifest.get("role_mappings", {}) or {}
    roles: set[str] = set()
    for step in manifest.get("steps", []) or []:
        for role in step.get("required_signoffs", []) or []:
            roles.add(role)
    return sorted(roles), role_map


def parse_stamp_status(path: Path) -> str | None:
    """Read the `status:` field from a stamp file. None if unreadable/absent."""
    try:
        text = path.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("status:"):
            return stripped.split(":", 1)[1].strip().upper()
    return None


def changed_files(root: Path, base: str) -> list[str]:
    """Files changed between merge-base(base, HEAD) and HEAD."""
    merge_base = run_git(["merge-base", base, "HEAD"], root)
    if not merge_base:
        # Fall back to a direct diff if the refs do not share history.
        merge_base = base
    out = run_git(["diff", "--name-only", merge_base, "HEAD"], root)
    return [line for line in out.splitlines() if line]


def all_tracked_files(root: Path) -> list[str]:
    out = run_git(["ls-files"], root)
    return [line for line in out.splitlines() if line]


def dirty_non_signoff_paths(root: Path) -> list[str]:
    """Working-tree changes (modified/staged/untracked) outside oversight artifacts.

    An unsigned working-tree change means files exist that no stamp can be newer
    than, so the gate must fail. Oversight-generated artifacts (sign-off stamps,
    the audit trail, ephemeral agent state) are exempt — signing and the system's
    own bookkeeping are not source changes. (HOS#112)
    """
    out = run_git(["status", "--porcelain"], root)
    dirty: list[str] = []
    for line in out.splitlines():
        if not line:
            continue
        path = line[3:]
        # Handle rename "old -> new"
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if is_oversight_artifact(path):
            continue
        dirty.append(path)
    return dirty


def is_oversight_artifact(path: str) -> bool:
    """True for oversight-generated artifacts excluded from the changed-file set.

    Covers sign-off stamps plus the audit trail and ephemeral agent state — all
    written by the oversight tooling itself, not source the stamps must beat.
    (HOS#112)
    """
    return path.startswith(OVERSIGHT_ARTIFACT_PREFIXES)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate validation-suite sign-offs against changed files.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--base",
        metavar="REF",
        help="PR/CI mode: compare files changed vs merge-base(REF, HEAD).",
    )
    mode.add_argument(
        "--all",
        action="store_true",
        help="Deploy mode: compare against every tracked file.",
    )
    parser.add_argument(
        "--manifest",
        default="contract/step-manifest.yaml",
        help="Path to step-manifest.yaml (default: contract/step-manifest.yaml).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the final PASS/FAIL line.",
    )
    args = parser.parse_args()

    if not args.all and not args.base:
        parser.error("one of --base REF or --all is required")

    root = repo_root(Path.cwd())
    manifest_path = (root / args.manifest).resolve()
    required_roles, role_map = load_required_roles(manifest_path)

    if not required_roles:
        sys.stderr.write(
            "signoff_gate: no required_signoffs found in manifest — refusing to "
            "pass an empty validation suite.\n"
        )
        return 2

    def log(msg: str = "") -> None:
        if not args.quiet:
            print(msg)

    log("=== sign-off gate ===")
    log(f"manifest: {args.manifest}")
    log(f"mode:     {'deploy (--all)' if args.all else f'pr (--base {args.base})'}")
    log("")

    # ── 1. Working tree must be clean of unsigned changes ────────────────────
    dirty = dirty_non_signoff_paths(root)
    failures: list[str] = []
    if dirty:
        failures.append(
            "uncommitted changes outside signoffs/ are not covered by any "
            "sign-off:\n    " + "\n    ".join(sorted(dirty))
        )

    # ── 2. Build the file set whose recency the stamps must beat ─────────────
    if args.all:
        files = all_tracked_files(root)
    else:
        files = changed_files(root, args.base)
    files = [f for f in files if not is_oversight_artifact(f)]

    newest_file = ""
    newest_file_time = 0
    for f in files:
        # Only consider files that still exist with committed history.
        if not (root / f).exists():
            continue
        t = commit_time(root, f)
        if t > newest_file_time:
            newest_file_time, newest_file = t, f

    if files:
        if newest_file_time:
            log(f"newest changed file: {newest_file} " f"@ commit {newest_file_time}")
        else:
            log("changed files have no committed history yet.")
    else:
        log("no non-sign-off files in scope.")
    log("")

    # ── 3. Every required role needs a fresh, approved, committed stamp ──────
    log(f"required validation suite ({len(required_roles)} roles):")
    for role in required_roles:
        agent = role_map.get(role, "?")
        rel = f"{SIGNOFFS_DIR}/{role}{STAMP_SUFFIX}"
        stamp_path = root / rel
        agent_label = f"{role} ({agent})"

        if not stamp_path.exists():
            log(f"  ✗ {agent_label}: MISSING stamp {rel}")
            failures.append(f"{agent_label}: no stamp at {rel}")
            continue

        status = parse_stamp_status(stamp_path)
        if status not in VALID_STATUSES:
            log(f"  ✗ {agent_label}: invalid status {status!r}")
            failures.append(
                f"{agent_label}: status must be one of " f"{sorted(VALID_STATUSES)}, got {status!r}"
            )
            continue

        stamp_time = commit_time(root, rel)
        if stamp_time == 0:
            log(f"  ✗ {agent_label}: stamp not committed yet")
            failures.append(
                f"{agent_label}: stamp {rel} exists but has no commit — "
                f"commit it so it gets an authoritative timestamp"
            )
            continue

        if stamp_time < newest_file_time:
            delta = newest_file_time - stamp_time
            log(f"  ✗ {agent_label}: STALE — signed {delta}s before " f"{newest_file} changed")
            failures.append(
                f"{agent_label}: stamp ({stamp_time}) is older than changed file "
                f"{newest_file} ({newest_file_time}) — re-sign after changes"
            )
            continue

        log(f"  ✓ {agent_label}: {status} @ commit {stamp_time}")

    log("")
    if failures:
        log(f"FAIL — {len(failures)} problem(s):")
        for i, f in enumerate(failures, 1):
            log(f"  {i}. {f}")
        if args.quiet:
            print("sign-off gate: FAIL")
        return 1

    print("sign-off gate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
