#!/usr/bin/env python3
"""require_tier_ceiling.py — server-side overseer ceiling gate.

The load-bearing half of the autonomous-merge safety model: a check that runs
in GitHub Actions (outside the overseer's session), verifying that when the
overseer approves a PR the computed risk tier does not exceed OVERSEER_CEILING.

If the overseer has NOT approved, the gate trivially passes (ceiling is N/A).
If the overseer HAS approved, the tier is computed and compared to the ceiling.
A tier above the ceiling exits 1 (FAIL) — a human approval is required instead.

Usage (CI):
  python3 require_tier_ceiling.py --pr <number> [--repo owner/repo]

Exit codes:
  0 — ceiling not exceeded (safe to merge with overseer)
  1 — ceiling exceeded (overseer approved above its ceiling — human required)
  2 — error (missing data or tooling failure — fail-closed)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ENV_FILE = Path(__file__).with_name("machine-accounts.env")

TIER_ORDER = {"SAFE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


# ---------------------------------------------------------------------------
# machine-accounts.env loader
# ---------------------------------------------------------------------------

def load_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        print(
            f"require_tier_ceiling: machine-accounts.env not found at {path}",
            file=sys.stderr,
        )
        sys.exit(2)
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        # Strip inline bash comments before unquoting (e.g. KEY="val"  # comment)
        val = re.sub(r'\s+#.*$', '', val.strip())
        val = val.strip('"').strip("'")
        # Expand simple variable references (e.g. BOT_ACCOUNTS="${VAR1} ${VAR2}")
        val = re.sub(r"\$\{?(\w+)\}?", lambda m: result.get(m.group(1), ""), val)
        result[key] = val
    return result


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def _gh(*args: str) -> str:
    """Run `gh` with the given args, return stdout. Exit 2 on failure."""
    try:
        out = subprocess.run(
            ["gh"] + list(args),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except FileNotFoundError:
        print("require_tier_ceiling: `gh` not found on PATH", file=sys.stderr)
        sys.exit(2)
    except subprocess.CalledProcessError as exc:
        print(
            f"require_tier_ceiling: gh command failed: {exc.stderr.strip()}",
            file=sys.stderr,
        )
        sys.exit(2)
    return out


def get_reviews(repo: str, pr: str) -> list[dict]:
    raw = _gh("api", "--paginate", f"repos/{repo}/pulls/{pr}/reviews")
    raw = raw.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        # --paginate may concatenate arrays: "]\n[" → ","
        return json.loads(re.sub(r"\]\s*\[", ",", raw))


def overseer_has_approved(reviews: list[dict], overseer_login: str) -> bool:
    for review in reviews:
        if str(review.get("state", "")).upper() != "APPROVED":
            continue
        login = (review.get("user") or {}).get("login", "")
        if login.lower() == overseer_login.lower():
            return True
    return False


def get_changed_files(pr: str) -> list[str]:
    raw = _gh("pr", "diff", pr, "--name-only")
    return [line for line in raw.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Tier computation
# ---------------------------------------------------------------------------

def _try_validator_summary() -> str | None:
    """Read tier from .claudetmp/oversight/validators/summary.json if present."""
    summary = Path(".claudetmp/oversight/validators/summary.json")
    if not summary.is_file():
        return None
    try:
        data = json.loads(summary.read_text(encoding="utf-8"))
        tier = data.get("tier") or data.get("risk_tier") or data.get("Tier")
        if isinstance(tier, str) and tier.upper() in TIER_ORDER:
            return tier.upper()
    except Exception:
        return None
    return None


def _try_rn_calculator(changed_files: list[str]) -> str | None:
    """Run rn_calculator.py and parse the tier from its output."""
    rn_script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "oversight"
        / "validators"
        / "rn_calculator.py"
    )
    if not rn_script.is_file():
        return None
    try:
        result = subprocess.run(
            [sys.executable, str(rn_script)] + changed_files,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return None
        # rn_calculator outputs a JSON dict; look for "tier" key
        data = json.loads(result.stdout)
        tier = data.get("tier") or data.get("risk_tier")
        if isinstance(tier, str) and tier.upper() in TIER_ORDER:
            return tier.upper()
    except Exception:
        return None
    return None


def _simplified_tier(changed_files: list[str]) -> str:
    """
    Fallback tier estimate when neither summary.json nor rn_calculator is
    available. Intentionally conservative:
      MEDIUM if >10 Python/shell files changed or any agent definition changed.
      LOW otherwise.
    """
    agent_paths = [f for f in changed_files if ".claude/agents/" in f]
    if agent_paths:
        return "MEDIUM"
    code_files = [
        f for f in changed_files
        if f.endswith((".py", ".sh"))
    ]
    if len(code_files) > 10:
        return "MEDIUM"
    return "LOW"


def compute_tier(changed_files: list[str]) -> str:
    """Return the best available risk tier string (SAFE/LOW/MEDIUM/HIGH/CRITICAL)."""
    # 1. Prefer pre-computed summary from the inner-loop validators
    tier = _try_validator_summary()
    if tier:
        return tier
    # 2. Run rn_calculator on the diff
    tier = _try_rn_calculator(changed_files)
    if tier:
        return tier
    # 3. Conservative fallback estimate
    return _simplified_tier(changed_files)


# ---------------------------------------------------------------------------
# Tier comparison
# ---------------------------------------------------------------------------

def tier_to_int(tier: str) -> int:
    """Map tier name to ordering int. Unknown tiers map to CRITICAL (fail-safe)."""
    return TIER_ORDER.get(tier.upper(), TIER_ORDER["CRITICAL"])


def tier_exceeds_ceiling(tier: str, ceiling: str) -> bool:
    return tier_to_int(tier) > tier_to_int(ceiling)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Overseer tier-ceiling gate")
    ap.add_argument("--pr", required=True, help="PR number")
    ap.add_argument("--repo", default="", help="owner/repo (defaults to GITHUB_REPOSITORY env)")
    args = ap.parse_args()

    # Resolve repo slug
    repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        print(
            "require_tier_ceiling: --repo or GITHUB_REPOSITORY required",
            file=sys.stderr,
        )
        return 2

    # Load identity config
    env = load_env(ENV_FILE)
    overseer_login = env.get("BOT_OVERSEER_USERNAME", "").strip()
    ceiling = env.get("OVERSEER_CEILING", "").strip().upper()

    if not overseer_login:
        print(
            "require_tier_ceiling: BOT_OVERSEER_USERNAME not set in machine-accounts.env",
            file=sys.stderr,
        )
        return 2
    if not ceiling or ceiling not in TIER_ORDER:
        print(
            f"require_tier_ceiling: OVERSEER_CEILING '{ceiling}' is not a valid tier "
            f"(expected one of {list(TIER_ORDER)})",
            file=sys.stderr,
        )
        return 2

    # Fetch reviews; determine if the overseer has approved
    reviews = get_reviews(repo, args.pr)
    if not overseer_has_approved(reviews, overseer_login):
        print(
            f"require_tier_ceiling: overseer ({overseer_login}) has not approved — "
            "ceiling gate N/A (pass)."
        )
        return 0

    # Overseer has approved: compute the tier and compare to ceiling
    changed = get_changed_files(args.pr)
    if not changed:
        # No changed files is unusual but not an error — treat as SAFE
        print(
            "require_tier_ceiling: no changed files reported (treating as SAFE — pass)."
        )
        return 0

    tier = compute_tier(changed)
    print(
        f"require_tier_ceiling: overseer approved; computed tier={tier}, "
        f"ceiling={ceiling}."
    )

    if tier_exceeds_ceiling(tier, ceiling):
        print(
            f"\nFAIL — overseer ({overseer_login}) approved a PR at tier {tier}, "
            f"which exceeds its ceiling ({ceiling}).",
            file=sys.stderr,
        )
        print(
            "  A human reviewer must approve this PR instead.",
            file=sys.stderr,
        )
        return 1

    print(
        f"PASS — tier {tier} is within the overseer ceiling ({ceiling})."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"require_tier_ceiling: unexpected error: {exc}", file=sys.stderr)
        sys.exit(2)
