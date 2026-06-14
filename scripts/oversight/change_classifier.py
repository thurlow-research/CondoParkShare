#!/usr/bin/env python3
"""change_classifier.py — deterministic, independent classification of a diff.

This is the *independent* check that closes two governance holes the
oversight-evaluator otherwise trusts upstream actors to get right:

  #75  Self-classification of change type is an unaudited gate to the
       human-approval boundary. Each authoring agent labels its OWN change
       clarifying / additive / structural, and only `structural` escalates to a
       human. Nothing audits the label. This module detects MECHANICAL
       STRUCTURAL SIGNATURES from the diff — new external dependency, new
       permission/auth state, new user-facing flow step/route, new user-facing
       state enum — that FORCE `structural` regardless of the author's label.

  #74  post-change-sweep can write `Status: N/A` register entries on behalf of
       skipped reviewers. An advisory tool mis-determining a domain as "not
       applicable" silently waives a required reviewer. This module independently
       reports which reviewer DOMAINS the diff actually touches, so the evaluator
       can reject an N/A for a domain that was, in fact, changed.

Design stance — fail toward the human (the ratchet):
  The heuristics are a FLOOR, deliberately biased to over-detect. A false
  positive forces a human to look (conservative, safe). A false negative is the
  only real failure mode, so signatures err on the side of catching more.
  Projects with a known stack can extend SIGNATURES / DOMAIN_RULES below; this
  generic base targets Python/Django + common JS/route conventions.

Output: JSON to stdout.
  {
    "base": "<sha>", "head": "<sha>",
    "domains_touched": {"security": {...}, "ui": {...}, ...},
    "structural_signals": [ {"signal": "...", "file": "...", "evidence": "..."} ]
  }

Exit: 0 always (this is a reporter; the evaluator decides). 2 on git error.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

# ── Reviewer-domain detection (for #74 N/A verification) ─────────────────────
# Maps a reviewer ROLE key (as used in step-manifest role_mappings and the
# sign-off register) to how we decide the diff "touched" that domain. Each rule
# is (changed-file-path regex, added-line regex) — either match marks touched.
# `None` means that channel is not used for the role.
#
# Deliberately broad: a domain marked touched simply means "an N/A for this role
# is not credible — require a real review." Mirrors post-change-sweep Step 2.
DOMAIN_RULES: dict[str, tuple[str | None, str | None]] = {
    # security: any application code or template change carries security context
    "security": (r"\.(py|html|js|ts|jsx|tsx)$", None),
    # privacy: paths or code touching identity / PII / data-subject rights
    "privacy": (
        r"(account|user|profile|pii|gdpr|privacy|consent|erasure|retention|personal|email|address|phone)",
        r"(PII|personal_data|anonymi|pseudonymi|erasure|retention|consent|EmailField|"
        r"first_name|last_name|date_of_birth|ssn|national_id)",
    ),
    # ui / a11y: template / component changes
    "ui": (r"(templates/.*\.html$|\.(jsx|tsx|vue|svelte)$)", None),
    "a11y": (r"(templates/.*\.html$|\.(jsx|tsx|vue|svelte)$)", None),
    # ops: background jobs, external calls, async, queues, schedules
    "ops": (
        None,
        r"(celery|@shared_task|@task\b|async\s+def|await\s|requests\.|httpx|aiohttp|"
        r"kafka|rabbit|boto3|queue|BackgroundTask|cron|schedule\(|apscheduler)",
    ),
    # reliability: outbound connections that can time out / fail
    "reliability": (
        None,
        r"(requests\.|httpx|aiohttp|urllib|socket\.|\.execute\(|cursor|"
        r"cache\.(get|set)|redis|\.objects\.(all|filter|get|create)|session\.(get|post|put|delete))",
    ),
    # infra: deployment / orchestration config
    "infra": (
        r"(docker-compose.*\.ya?ml$|Caddyfile$|Dockerfile$|\.env\.example$|"
        r"nginx.*\.conf$|k8s/.*\.ya?ml$|terraform/.*\.tf$|\.github/workflows/.*\.ya?ml$)",
        None,
    ),
}

# ── Structural-override signatures (for #75) ─────────────────────────────────
# Each entry: signal name → (added-line regex, optional added-file-status check).
# A match FORCES the change to be treated as `structural` (human gate), no matter
# how the authoring agent classified it. These encode the prose already in
# ux-designer.md / ops-designer.md ("new user decision point, new blocked/
# permission state, new completion criterion, new step in a user flow,
# previously uninstrumented component, new external dependency") as a
# deterministic, independently-checkable set.
ADDED_LINE_SIGNATURES: list[tuple[str, str]] = [
    (
        "new-permission-or-auth-state",
        r"(permission_required|login_required|IsAuthenticated|IsAdminUser|has_perm|"
        r"PermissionDenied|user_passes_test|staff_member_required|LoginRequiredMixin|"
        r"PermissionRequiredMixin|AccessMixin|@roles?_required|@authorize|require_role)",
    ),
    (
        "new-user-flow-or-route",
        r"(\bpath\(|\bre_path\(|\burl\(|@app\.route|@router\.(get|post|put|delete|patch)|"
        r"router\.(get|post|put|delete|patch)|\bRoute\(|createBrowserRouter|<Route\b)",
    ),
    (
        "new-user-facing-state",
        r"(choices\s*=|TextChoices|IntegerChoices|models\.TextChoices|StateField|FSMField|"
        r"@transition|STATUS_[A-Z]|_STATES\b|enum\.Enum|new\s+state\b)",
    ),
]

# Dependency manifests — an ADDED line here is a new external dependency.
DEPENDENCY_MANIFESTS = re.compile(
    r"(requirements[^/]*\.txt$|pyproject\.toml$|setup\.py$|setup\.cfg$|Pipfile$|"
    r"package\.json$|go\.mod$|Gemfile$|Cargo\.toml$|pom\.xml$|build\.gradle)"
)
# Within a manifest, lines that look like a real dependency addition (skip
# comments, blanks, section headers, and pure version bumps of existing deps are
# still flagged — a new pinned version is structural enough to merit a glance).
DEP_LINE = re.compile(r"^[A-Za-z0-9_.\-\"']")

# Templates added as NEW files are a new user-facing surface.
TEMPLATE_FILE = re.compile(r"(templates/.*\.html$|\.(jsx|tsx|vue|svelte)$)")


def _git(args: list[str]) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError as e:
        print(f"change_classifier: git {' '.join(args)} failed: {e.stderr}", file=sys.stderr)
        sys.exit(2)


def resolve_range(base: str | None, head: str | None) -> tuple[str, str]:
    head = head or _git(["rev-parse", "HEAD"]).strip()
    if not base:
        # Default base: merge-base with the default branch, else previous commit.
        mb = _git(["merge-base", "HEAD", "origin/main"]).strip() if _has_ref("origin/main") else ""
        base = mb or _git(["rev-parse", "HEAD~1"]).strip()
    return base, head


def _has_ref(ref: str) -> bool:
    try:
        subprocess.run(["git", "rev-parse", "--verify", ref], capture_output=True, check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def collect_diff(base: str, head: str) -> tuple[list[tuple[str, str]], dict[str, list[str]]]:
    """Return (name_status, added_lines_by_file).

    name_status: list of (status_letter, path). status A=added, M=modified, etc.
    added_lines_by_file: path → list of added content lines (without leading '+').
    """
    name_status: list[tuple[str, str]] = []
    for line in _git(["diff", "--name-status", f"{base}..{head}"]).splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            name_status.append((parts[0][0], parts[-1]))

    added: dict[str, list[str]] = {}
    current: str | None = None
    # --unified=0 keeps only changed lines; parse per-file added content.
    for line in _git(["diff", "--unified=0", f"{base}..{head}"]).splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            added.setdefault(current, [])
        elif line.startswith("+++ "):
            current = None
        elif current and line.startswith("+") and not line.startswith("+++"):
            added[current].append(line[1:])
    return name_status, added


def detect_domains(name_status, added, roles=None) -> dict[str, dict]:
    files = [p for _, p in name_status]
    result: dict[str, dict] = {}
    rules = (
        DOMAIN_RULES if roles is None else {r: DOMAIN_RULES[r] for r in roles if r in DOMAIN_RULES}
    )
    for role, (file_rx, line_rx) in rules.items():
        evidence = None
        if file_rx:
            for f in files:
                if re.search(file_rx, f, re.IGNORECASE):
                    evidence = {"by": "path", "file": f}
                    break
        if not evidence and line_rx:
            for f, lines in added.items():
                for ln in lines:
                    if re.search(line_rx, ln):
                        evidence = {"by": "added-line", "file": f, "line": ln.strip()[:120]}
                        break
                if evidence:
                    break
        if evidence:
            result[role] = {"touched": True, "evidence": evidence}
    return result


def detect_structural(name_status, added) -> list[dict]:
    signals: list[dict] = []

    # New external dependency — added line in a dependency manifest.
    for _status, path in name_status:
        if DEPENDENCY_MANIFESTS.search(path):
            for ln in added.get(path, []):
                if DEP_LINE.match(ln.strip()):
                    signals.append(
                        {
                            "signal": "new-external-dependency",
                            "file": path,
                            "evidence": ln.strip()[:120],
                        }
                    )
                    break

    # New user-facing surface — a template/component added as a NEW file.
    for status, path in name_status:
        if status == "A" and TEMPLATE_FILE.search(path):
            signals.append(
                {
                    "signal": "new-user-facing-surface",
                    "file": path,
                    "evidence": "new template/component file",
                }
            )

    # Added-line signatures — permission/auth, route/flow, state enums.
    for name, rx in ADDED_LINE_SIGNATURES:
        crx = re.compile(rx)
        for f, lines in added.items():
            for ln in lines:
                if crx.search(ln):
                    signals.append({"signal": name, "file": f, "evidence": ln.strip()[:120]})
                    break  # one per file per signal is enough to force structural

    return signals


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Independent diff classification for the oversight evaluator."
    )
    ap.add_argument("--base", help="base SHA (default: merge-base with origin/main, else HEAD~1)")
    ap.add_argument("--head", help="head SHA (default: HEAD)")
    ap.add_argument("--explain", action="store_true", help="human-readable output instead of JSON")
    # Scoped invocation (the ratchet applied to verification cost): the evaluator
    # only pays to re-derive in the LOOSENING direction. Run --domains-only with
    # --roles for the specific reviewers an N/A would waive (#74); run
    # --structural-only only for steps that did NOT already go through a human
    # gate (#75). No need to verify when upstream asked for MORE review.
    ap.add_argument("--domains-only", action="store_true", help="only report domains_touched (#74)")
    ap.add_argument(
        "--structural-only", action="store_true", help="only report structural_signals (#75)"
    )
    ap.add_argument(
        "--roles",
        help="comma-separated reviewer roles to check (default: all). "
        "Pass only the N/A'd roles to avoid scanning domains nobody waived.",
    )
    args = ap.parse_args()

    base, head = resolve_range(args.base, args.head)
    name_status, added = collect_diff(base, head)

    want_domains = not args.structural_only
    want_structural = not args.domains_only
    roles = [r.strip() for r in args.roles.split(",")] if args.roles else None

    domains = detect_domains(name_status, added, roles) if want_domains else {}
    structural = detect_structural(name_status, added) if want_structural else []

    out = {
        "base": base,
        "head": head,
        "domains_touched": domains,
        "structural_signals": structural,
    }

    if args.explain:
        print(f"Diff {base[:8]}..{head[:8]}")
        print(f"\nDomains touched ({len(domains)}):")
        for role, d in domains.items():
            print(f"  {role:14s} ← {d['evidence']}")
        print(f"\nStructural signals ({len(structural)}) — these FORCE `structural`:")
        for s in structural or []:
            print(f"  {s['signal']:28s} {s['file']}  «{s['evidence']}»")
        if not structural:
            print("  (none)")
    else:
        print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
