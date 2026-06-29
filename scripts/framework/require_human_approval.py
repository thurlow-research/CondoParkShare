#!/usr/bin/env python3
"""require_human_approval.py — server-side §9 protected-surface gate.

The load-bearing half of the determination-honesty model (AGENT-IDENTITY.md §5.1):
a check that runs where the agent's session does NOT control it (GitHub Actions),
so a bot cannot satisfy it locally. It fails a PR that touches a protected surface
(scripts/framework/protected_surfaces.txt) unless a HUMAN has approved it — where
"human" = an approving review whose author is not in BOT_ACCOUNTS.

This is intentionally path-based and re-derivable from the diff (not self-reported):
touching a control-defining surface forces a human approver regardless of the
worker's claimed risk tier. The tier-vs-ceiling gate (overseer ceiling) layers on
top of this and is built separately.

Modes:
  # CI: compute changed files from the base..head range and fetch reviews via gh
  require_human_approval.py --base "$BASE_SHA" --head "$HEAD_SHA" --pr "$PR_NUMBER"

  # Local/test: feed inputs directly (no git, no network)
  require_human_approval.py --changed-files-file files.txt --reviews-file reviews.json

Exit: 0 = pass (no protected surface, or a human approval present)
      1 = FAIL (protected surface touched, no human approval)
      2 = usage/tooling error
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SURFACES_FILE = Path(__file__).with_name("protected_surfaces.txt")


def load_globs(path: Path) -> list[str]:
    if not path.is_file():
        print(f"require_human_approval: missing {path}", file=sys.stderr)
        sys.exit(2)
    globs = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            globs.append(line)
    return globs


def glob_to_regex(glob: str) -> re.Pattern:
    """Translate a protected-surface glob to an anchored regex.

    `dir/**` → the directory and everything under it; `*` → one path segment
    (no `/`); a plain path → that exact file. We build the regex token-by-token
    so `**` and `*` get the right cross-segment vs intra-segment semantics.
    """
    out = ["^"]
    i, n = 0, len(glob)
    while i < n:
        c = glob[i]
        if glob.startswith("**", i):
            out.append(".*")  # cross-segment: any chars incl. '/'
            i += 2
            if i < n and glob[i] == "/":
                i += 1  # `**/` already consumed the slash role
        elif c == "*":
            out.append("[^/]*")  # one segment
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def matched_surfaces(changed: list[str], globs: list[str]) -> list[tuple[str, str]]:
    """Return (changed_file, matching_glob) pairs for every protected hit."""
    pats = [(g, glob_to_regex(g)) for g in globs]
    hits = []
    for f in changed:
        f = f.strip()
        if not f:
            continue
        for g, rx in pats:
            if rx.match(f):
                hits.append((f, g))
                break
    return hits


def changed_from_git(base: str, head: str) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", f"{base}..{head}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except subprocess.CalledProcessError as e:
        print(f"require_human_approval: git diff failed: {e.stderr}", file=sys.stderr)
        sys.exit(2)
    return [line for line in out.splitlines() if line.strip()]


def reviews_from_gh(pr: str) -> list[dict]:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        print(
            "require_human_approval: GITHUB_REPOSITORY unset (need it to fetch reviews)",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        out = subprocess.run(
            ["gh", "api", "--paginate", f"repos/{repo}/pulls/{pr}/reviews"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except subprocess.CalledProcessError as e:
        print(f"require_human_approval: gh api reviews failed: {e.stderr}", file=sys.stderr)
        sys.exit(2)
    # --paginate may concatenate JSON arrays; normalize to one list.
    out = out.strip()
    if not out:
        return []
    try:
        data = json.loads(out)
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        # gh --paginate concatenates page arrays with arbitrary whitespace
        # between them ("]\n[", "] ["), not a literal "][" — normalize any
        # close-bracket / open-bracket boundary to a comma.
        return json.loads(re.sub(r"\]\s*\[", ",", out))


def human_approval_present(reviews: list[dict], bot_accounts: set[str]) -> list[str]:
    """Return the list of human approver logins (APPROVED, not a bot)."""
    approvers = []
    for r in reviews:
        if str(r.get("state", "")).upper() != "APPROVED":
            continue
        login = (r.get("user") or {}).get("login", "")
        if login and login not in bot_accounts:
            approvers.append(login)
    return sorted(set(approvers))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--pr")
    ap.add_argument("--changed-files-file", help="local test: newline-separated changed paths")
    ap.add_argument("--reviews-file", help="local test: PR reviews JSON")
    args = ap.parse_args()

    globs = load_globs(SURFACES_FILE)

    if args.changed_files_file:
        changed = [
            line for line in Path(args.changed_files_file).read_text().splitlines() if line.strip()
        ]
    elif args.base:
        changed = changed_from_git(args.base, args.head)
    else:
        print("require_human_approval: need --changed-files-file or --base", file=sys.stderr)
        return 2

    hits = matched_surfaces(changed, globs)
    if not hits:
        print("✔ require-human-approval: no protected surface touched — gate N/A.")
        return 0

    surfaces = sorted({g for _, g in hits})
    print("Protected surface(s) touched (AGENT-IDENTITY.md §9):")
    for f, g in hits:
        print(f"    {f}   (matches {g})")

    bot_accounts = {b for b in os.environ.get("BOT_ACCOUNTS", "").split() if b}

    if args.reviews_file:
        reviews = json.loads(Path(args.reviews_file).read_text())
    elif args.pr:
        reviews = reviews_from_gh(args.pr)
    else:
        print(
            "require_human_approval: need --reviews-file or --pr to check approvals",
            file=sys.stderr,
        )
        return 2

    humans = human_approval_present(reviews, bot_accounts)
    if humans:
        print(
            f"✔ require-human-approval: human approval present from {', '.join(humans)} — gate satisfied."
        )
        return 0

    print("", file=sys.stderr)
    print(
        "✘ require-human-approval: FAIL — this PR touches a protected surface and has",
        file=sys.stderr,
    )
    print(
        "  NO human approval. A bot (worker/overseer) may not approve or merge it.", file=sys.stderr
    )
    print(f"  Protected surfaces: {', '.join(surfaces)}", file=sys.stderr)
    if not bot_accounts:
        print(
            "  (BOT_ACCOUNTS unset — any human-collaborator approval will satisfy this gate.)",
            file=sys.stderr,
        )
    print("  A human with repo access must review and approve before merge.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
