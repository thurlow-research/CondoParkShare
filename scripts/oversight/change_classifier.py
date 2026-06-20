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
    (
        # A telemetry retrofit instruments a previously-uninstrumented component
        # without adding any other §2a signature, so ops-designer could under-
        # classify it as additive (#84). High-confidence telemetry primitives only
        # — deliberately NOT routine `logger.info(...)` or `collections.Counter(`,
        # which would force spurious human gates (the #117 false-match lesson).
        "new-observability-instrumentation",
        r"(get_tracer|start_as_current_span|add_span_processor|TracerProvider|"
        r"\.set_attribute\(|SpanKind|get_current_span|propagat|TraceContext|"
        r"opentelemetry|\bOTLP\b|prometheus|statsd|push_to_gateway|"
        r"structlog\.(get_logger|configure)|logging\.config|dictConfig|\bLOGGING\s*=)",
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

# The HOS framework tooling tree. The added-line signatures below describe
# APPLICATION behavior (auth state, routes, user-facing state); the oversight
# tooling's own source contains those very patterns as literal regex/string
# definitions, so scanning it makes the classifier match ITSELF — phantom
# structural signals whenever a framework file is in the diff (HOS#117).
# Exempt the tooling tree from application-domain signature scanning only;
# dependency-manifest and new-template signals still apply everywhere (a real
# dep added to oversight requirements.txt IS structural).
FRAMEWORK_TOOLING = re.compile(r"(^|/)scripts/(oversight|framework)/.*\.py$")

# ── Structural-modification detection (SPEC-121) ─────────────────────────────
# Tracked design/spec documents and the keyword sets that make one of their
# sections "structural". A diff that both removes AND adds a line in such a
# section signals a modification to a governance-bearing section — the same human
# gate a NEW such section would trigger, but for a CHANGE the additive signatures
# miss. Each entry: (file-path regex, section-keyword regex or None for match-all).
# None ⇒ every section of the document is structural (TELEMETRY-SPEC).
TRACKED_DOC_RULES: list[tuple[re.Pattern, re.Pattern | None]] = [
    # SPEC documents.
    (
        re.compile(r"(^|/)docs/(specs|v[^/]+)/SPEC-[^/]*\.md$"),
        re.compile(
            r"(permission|authorization|auth|approval|gate|required|must|shall|"
            r"deny|block|restrict)",
            re.IGNORECASE,
        ),
    ),
    # Technical-design documents.
    (
        re.compile(r"(^|/)(docs/v[^/]+/)?TECHNICAL-DESIGN-[^/]*\.md$"),
        re.compile(
            r"(permission|authorization|auth|gate|access control|security|"
            r"input validation|sanitiz)",
            re.IGNORECASE,
        ),
    ),
    # Design documents.
    (
        re.compile(r"(^|/)(docs/v[^/]+/)?DESIGN[^/]*\.md$"),
        re.compile(r"(permission|authorization|auth|gate|access control)", re.IGNORECASE),
    ),
    # Telemetry spec — every section is structural (match-all).
    (re.compile(r"(^|/)(docs/ops/)?TELEMETRY-SPEC\.md$"), None),
]

# A unified-diff hunk header, optionally carrying trailing section text appended
# by a funcname / xfuncname diff driver: `@@ -a,b +c,d @@ <section>`. The section
# group is empty under --unified=0 unless a driver is configured for the file
# type; when absent we fall back to file-level attribution (SPEC-121 §3.3).
HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@(?: (.*))?$")


# ── Independent tier-floor re-derivation (#94, SPEC-94) ──────────────────────
# A deterministic MINIMUM tier the evaluator re-derives from the diff, independent
# of the tier `risk-assessor` self-reports in risk-assessment.md. Same anti-gaming
# shape as the structural-override / N/A re-derivation above: the highest matching
# rule wins, biased to over-detect (a false positive sends a benign change to a
# human; a false negative is the only real failure — the ratchet).
#
# Architect binding (SPEC-94): these floor patterns are kept SEPARATE from
# ADDED_LINE_SIGNATURES above and from the composite validators
# (scripts/oversight/validators/rn_calculator.py path globs, migration_scorer.py).
# They are a path/content FLOOR run inside the evaluator, NOT a port of the
# composite-score validator rule sets — see ARCH-Q-1 (keep separate, re-evaluate
# after first use; consolidating risks double-counting against the composite score).
TIER_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def _tier_rank(tier: str) -> int:
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return 0


# FILE-PATH floor rules. Binding 3: these are NOT exempted by FRAMEWORK_TOOLING — a
# financial path in the framework tree is still a structural pattern worth flagging.
# Sibling rule sets live in the composite validators (rn_calculator.py path globs,
# migration_scorer.py); kept separate here per ARCH-Q-1.
# Each: (tier, compiled regex over the path, short pattern label).
TIER_FLOOR_PATH_RULES: list[tuple[str, re.Pattern, str]] = [
    # CRITICAL — payment / financial paths. Sibling: composite financial signals.
    (
        "CRITICAL",
        re.compile(
            r"(^|/)(payment|billing|financial|checkout|subscription|invoice|"
            r"stripe|braintree|paypal)",
            re.IGNORECASE,
        ),
        "payment/financial path",
    ),
    # HIGH — auth / session / credential paths.
    (
        "HIGH",
        re.compile(
            r"(^|/)(auth|login|logout|session|token|credential|password|mfa|"
            r"totp|oauth|sso|jwt)",
            re.IGNORECASE,
        ),
        "auth/session path",
    ),
    # HIGH — DB migration files. Sibling: migration_scorer.py (composite signal);
    # kept separate as a path floor per ARCH-Q-1 (no composite double-count here).
    ("HIGH", re.compile(r"(^|/)migrations/.*\.py$", re.IGNORECASE), "migration file"),
    # HIGH — privacy / PII paths.
    (
        "HIGH",
        re.compile(r"(^|/)(pii|gdpr|privacy|consent)", re.IGNORECASE),
        "privacy/PII path",
    ),
]

# ADDED-LINE floor rules. Binding 3: these ARE exempted by FRAMEWORK_TOOLING — the
# oversight tooling's own source contains these literals as pattern definitions, so
# scanning it self-matches (HOS#117). Sibling: ADDED_LINE_SIGNATURES above (structural)
# — kept separate so the tier floor and the structural-override set evolve independently.
# Each: (tier, compiled regex over an added line, short pattern label).
TIER_FLOOR_LINE_RULES: list[tuple[str, re.Pattern, str]] = [
    # CRITICAL — PCI / financial API surfaces.
    (
        "CRITICAL",
        re.compile(
            r"(stripe\.|braintree\.|PaymentIntent|charge\(|Card\(|ACH|IBAN|account_number)"
        ),
        "PCI/financial API",
    ),
    # HIGH — PII field declarations.
    (
        "HIGH",
        re.compile(
            r"(EmailField|first_name|last_name|date_of_birth|ssn|national_id|"
            r"phone_number|address|personal_data)"
        ),
        "PII field",
    ),
]

# MEDIUM catch-all: general application logic files not covered by a higher tier.
TIER_FLOOR_APP_CODE = re.compile(r"\.(py|js|ts|jsx|tsx)$", re.IGNORECASE)


def detect_tier_floor(name_status, added) -> tuple[str, list[dict]]:
    """Re-derive the minimum tier from the diff (#94).

    Returns (tier_floor, evidence). tier_floor is the HIGHEST matching tier across
    all rules (LOW < MEDIUM < HIGH < CRITICAL). evidence lists every match so the
    evaluator's compliance-fail message can name the specific files/patterns.

    This is a FLOOR (lower bound) only: risk-assessor may compute a higher tier.
    The evaluator fails compliance only when the self-reported tier is BELOW this
    floor (the loosening direction) and no human-tier-override exists.
    """
    files = [p for _, p in name_status]
    floor = "LOW"
    evidence: list[dict] = []

    # File-path rules — scanned over ALL changed paths (framework tree included).
    for f in files:
        for tier, rx, label in TIER_FLOOR_PATH_RULES:
            m = rx.search(f)
            if m:
                evidence.append({"rule": f"{tier} path: {label}", "file": f, "pattern": m.group(0)})
                if _tier_rank(tier) > _tier_rank(floor):
                    floor = tier

    # Added-line rules — skip the framework tooling tree (HOS#117 self-match).
    for f, lines in added.items():
        if FRAMEWORK_TOOLING.search(f):
            continue
        for ln in lines:
            for tier, rx, label in TIER_FLOOR_LINE_RULES:
                m = rx.search(ln)
                if m:
                    evidence.append(
                        {"rule": f"{tier} added-line: {label}", "file": f, "pattern": m.group(0)}
                    )
                    if _tier_rank(tier) > _tier_rank(floor):
                        floor = tier

    # MEDIUM catch-all — any application-code file not already floored higher.
    if _tier_rank(floor) < _tier_rank("MEDIUM"):
        for f in files:
            if TIER_FLOOR_APP_CODE.search(f):
                evidence.append(
                    {"rule": "MEDIUM application-code file", "file": f, "pattern": "code extension"}
                )
                floor = "MEDIUM"
                break

    return floor, evidence


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


def collect_diff(
    base: str, head: str
) -> tuple[list[tuple[str, str]], dict[str, list[str]], dict[str, list[str]]]:
    """Return (name_status, added_lines_by_file, removed_lines_by_file).

    name_status: list of (status_letter, path). status A=added, M=modified, etc.
    added_lines_by_file:   path → list of added content lines (without leading '+').
    removed_lines_by_file: path → list of removed content lines (without leading '-').

    The `removed` channel (SPEC-121) lets `detect_structural_modifications` see the
    BEFORE state of an auth/permission line or a doc section. It is additive: callers
    that only need added lines may ignore the third element. Removed lines exclude the
    `--- a/` file header (parsed symmetrically to the `+++ b/` skip).
    """
    name_status: list[tuple[str, str]] = []
    for line in _git(["diff", "--name-status", f"{base}..{head}"]).splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            name_status.append((parts[0][0], parts[-1]))

    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    current: str | None = None
    # --unified=0 keeps only changed lines; parse per-file added/removed content.
    # Test the 3-char file headers (`+++ ` / `--- `) BEFORE the 1-char content tests
    # so a header line is never miscounted as content.
    for line in _git(["diff", "--unified=0", f"{base}..{head}"]).splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            added.setdefault(current, [])
            removed.setdefault(current, [])
        elif line.startswith("+++ "):
            current = None
        elif line.startswith("--- "):
            # old-file header — not content; `current` is set by the matching +++ b/.
            continue
        elif current and line.startswith("+"):
            added[current].append(line[1:])
        elif current and line.startswith("-"):
            removed[current].append(line[1:])
    return name_status, added, removed


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
    # Skip the framework tooling tree: these application-domain patterns appear
    # there only as the classifier's own literal definitions, not as real app
    # behavior, so scanning it self-matches (HOS#117).
    for name, rx in ADDED_LINE_SIGNATURES:
        crx = re.compile(rx)
        for f, lines in added.items():
            if FRAMEWORK_TOOLING.search(f):
                continue
            for ln in lines:
                if crx.search(ln):
                    signals.append({"signal": name, "file": f, "evidence": ln.strip()[:120]})
                    break  # one per file per signal is enough to force structural

    return signals


def _auth_signature_regex() -> re.Pattern:
    """Compiled `new-permission-or-auth-state` pattern from ADDED_LINE_SIGNATURES.

    Looked up by signal name so Category A reuses the SAME regex the additive scan
    uses — never a second copy that could drift (SPEC-121 §3.1, architect binding 3).
    """
    for name, rx in ADDED_LINE_SIGNATURES:
        if name == "new-permission-or-auth-state":
            return re.compile(rx)
    raise RuntimeError("new-permission-or-auth-state missing from ADDED_LINE_SIGNATURES")


def _doc_hunk_sections(base: str, head: str, path: str) -> list[tuple[str, str]]:
    """Per-changed-line section attribution for a tracked doc, under --unified=0.

    Returns [(section_label, content_line), ...] for every changed (+/-) line in the
    file. `section_label` is the text git appends after the second `@@` of a hunk
    header when a funcname/xfuncname driver is configured (SPEC-121 §3.3 precedence
    1); otherwise it falls back to the file path (precedence 2). We re-read the same
    zero-context diff for this one file — we do NOT widen the global --unified.
    """
    out: list[tuple[str, str]] = []
    section = path  # file-level fallback until a hunk header sets it
    for line in _git(["diff", "--unified=0", f"{base}..{head}", "--", path]).splitlines():
        m = HUNK_HEADER.match(line)
        if m:
            section = m.group(1).strip() if (m.group(1) and m.group(1).strip()) else path
            continue
        if line.startswith("+++ ") or line.startswith("--- "):
            continue
        if line.startswith("+") or line.startswith("-"):
            out.append((section, line[1:]))
    return out


def detect_structural_modifications(
    name_status, added, removed, base=None, head=None
) -> list[dict]:
    """Detect MODIFICATIONS to existing structural signatures (SPEC-121).

    Two categories, both keyed on a file present in BOTH `added` and `removed` (a
    true modification, not a pure add or pure delete):

      A. modified-permission-or-auth-state — an existing auth/permission decorator
         changed: a removed line AND an added line both match the auth pattern, and
         their stripped contents are not identical (a pure move is not a change).
         FRAMEWORK_TOOLING-exempt (HOS#117 self-match hazard).

      B. modified-doc-structural-section — a removed AND added line in a structural
         section of a tracked spec/design document. Section attribution per §3.3;
         over-detect bias on the file-level fallback path.

    `base`/`head` drive Category B section attribution (it re-reads per-file hunk
    headers). When omitted, Category B uses file-level labels plus the over-detect
    keyword test on changed lines. Pure reporter — never raises on a malformed diff.
    """
    signals: list[dict] = []
    auth_rx = _auth_signature_regex()

    # Files modified in place: present (with content) in both channels.
    both = sorted(set(added) & set(removed))

    # ── Category A — auth/permission decorator modification ──────────────────
    for f in both:
        if FRAMEWORK_TOOLING.search(f):
            continue  # binding 3: reuse the existing exemption, no app-domain scan
        removed_auth = [r for r in removed[f] if auth_rx.search(r)]
        added_auth = [a for a in added[f] if auth_rx.search(a)]
        if not removed_auth or not added_auth:
            continue
        rem_set = {s.strip() for s in removed_auth}
        add_set = {s.strip() for s in added_auth}
        if rem_set == add_set:
            continue  # identical stripped sets ⇒ pure move/reorder, not a change
        rem_only = rem_set - add_set
        add_only = add_set - rem_set
        first_removed = next((r for r in removed_auth if r.strip() in rem_only), removed_auth[0])
        first_added = next((a for a in added_auth if a.strip() in add_only), added_auth[0])
        signals.append(
            {
                "signal": "modified-permission-or-auth-state",
                "file": f,
                "section": None,
                "evidence": f"-{first_removed.strip()[:80]} | +{first_added.strip()[:80]}",
            }
        )

    # ── Category B — structural-section modification in tracked documents ─────
    for f in both:
        rule = next(((rx, kw) for (rx, kw) in TRACKED_DOC_RULES if rx.search(f)), None)
        if rule is None:
            continue
        _file_rx, kw_rx = rule

        # Attribute each changed line to its section (driver header or file-level).
        if base is not None and head is not None:
            attributed = _doc_hunk_sections(base, head, f)
        else:
            attributed = [(f, ln) for ln in added[f]] + [(f, ln) for ln in removed[f]]

        by_section: dict[str, list[str]] = {}
        for section, ln in attributed:
            by_section.setdefault(section, []).append(ln)

        added_set = {a.strip() for a in added[f]}
        removed_set = {r.strip() for r in removed[f]}

        for section, lines in by_section.items():
            file_level_fallback = section == f
            if kw_rx is None:  # match-all docs (TELEMETRY): every section structural
                structural = True
            elif kw_rx.search(section):
                structural = True
            elif file_level_fallback:
                # over-detect: no parseable header → test changed lines themselves
                structural = any(kw_rx.search(ln) for ln in lines)
            else:
                structural = False
            if not structural:
                continue

            sec_added = [ln for ln in lines if ln.strip() in added_set]
            sec_removed = [ln for ln in lines if ln.strip() in removed_set]
            if not sec_added or not sec_removed:
                continue  # need both an add and a remove in this structural section

            signals.append(
                {
                    "signal": "modified-doc-structural-section",
                    "file": f,
                    "section": section,
                    "evidence": f"-{sec_removed[0].strip()[:80]} | +{sec_added[0].strip()[:80]}",
                }
            )

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
    # #94: independent tier-floor re-derivation. Takes the whole output — emits
    # ONLY {"tier_floor", "evidence"}, never domains or structural signals (SPEC-94 R3).
    ap.add_argument(
        "--tier-floor",
        action="store_true",
        help="re-derive the minimum tier floor from the diff (#94); "
        "emits {tier_floor, evidence} only",
    )
    # #121: independent MODIFICATION re-derivation. Emits ONLY
    # {"structural_modifications": [...]} — new signals never appear in the default,
    # --structural-only, or --domains-only output (architect binding 6 byte-stability).
    ap.add_argument(
        "--modifications-only",
        action="store_true",
        help="report modifications to existing structural signatures (#121); "
        "emits {structural_modifications} only",
    )
    ap.add_argument(
        "--roles",
        help="comma-separated reviewer roles to check (default: all). "
        "Pass only the N/A'd roles to avoid scanning domains nobody waived.",
    )
    args = ap.parse_args()

    base, head = resolve_range(args.base, args.head)
    # collect_diff returns (name_status, added, removed); `removed` (SPEC-121) feeds
    # the --modifications-only branch only. The tier-floor / domain / structural
    # branches do not consume it, keeping their output byte-stable (binding 1).
    name_status, added, removed = collect_diff(base, head)

    # #121: --modifications-only is a standalone re-derivation of MODIFICATIONS to
    # existing structural signatures. It emits ONLY {"structural_modifications": [...]}
    # — never domains_touched / structural_signals — so existing modes stay byte-stable
    # (architect binding 6). Handle it before the domain/structural branch, return early.
    if args.modifications_only:
        mods = detect_structural_modifications(name_status, added, removed, base, head)
        if args.explain:
            print(f"Diff {base[:8]}..{head[:8]}")
            print(f"\nStructural modifications ({len(mods)}) — these FORCE `structural`:")
            for m in mods:
                sec = f" [{m['section']}]" if m["section"] else ""
                print(f"  {m['signal']:32s} {m['file']}{sec}  «{m['evidence']}»")
            if not mods:
                print("  (none)")
        else:
            print(json.dumps({"structural_modifications": mods}, indent=2))
        return 0

    # #94: --tier-floor is a standalone re-derivation. It takes the whole output
    # (no domains, no structural signals) so the evaluator's condition-11 check
    # gets exactly {tier_floor, evidence}. Handle it before the domain/structural
    # branch and return early.
    if args.tier_floor:
        floor, evidence = detect_tier_floor(name_status, added)
        if args.explain:
            print(f"Diff {base[:8]}..{head[:8]}")
            print(f"\nTier floor: {floor}")
            print(f"Evidence ({len(evidence)}):")
            for e in evidence:
                print(f"  {e['rule']:32s} {e['file']}  «{e['pattern']}»")
            if not evidence:
                print("  (none — LOW floor)")
        else:
            print(json.dumps({"tier_floor": floor, "evidence": evidence}, indent=2))
        return 0

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
