#!/usr/bin/env python3
"""panel_logic.py — corroboration counting and tier ranking for the review panel.

SPEC-376 / Issue #376. The HOS cross-vendor panel arbiter (run_panel.sh ARBITER
stage) deduplicates the independent reviewers' findings. This module adds the
missing CORROBORATION-RANKING step on top of that dedup pass: it counts how many
INDEPENDENT vendors agreed on each deduplicated finding, classifies findings into
two corroboration tiers, and orders them so cross-vendor-confirmed findings
surface to the human first.

Why this matters (research grounding):
  - CodeRabbit produces ~1.7x single-reviewer volume (Loker 2025); panel volume
    scales with roster size. Without ranking, reviewer fatigue is the failure mode.
  - AgenticSCR's detector->validator architecture raised precision while cutting
    comment volume 81% by surfacing corroborated findings first (Charoenwet 2026).

Authoritative clustering stays with Sonnet (architect binding 2): the arbiter
prompt emits per-finding `merged_from` membership, and THIS module counts vendor
corroboration deterministically from that membership. Same-vendor / different-lens
(codex:security + codex:adversary) collapses to ONE independent source (binding 3).

SPEC-332 / Issue #332 extends this module with the panel's DETERMINISTIC TRIAGE
logic, moved out of run_panel.sh under the #314 policy (Python for logic, shell
for launch): compute_triage_floor (the risk-tier floor from changed-file paths +
added-line count, formerly the `det_floor` bash function) and compute_sqc_sample
(the salted-deterministic red-team audit sample, formerly inline bash SHA256 +
modulo). Both are a BYTE-FOR-BYTE parity refactor (binding 6) — no behavior change.
The shell now calls the `triage-floor` and `sqc-sample` subcommands instead of
re-implementing the rules.

SPEC-333 / Issue #333 extends this module further with three more deterministic
transforms moved out of run_panel.sh under the same #314 policy: extract_json
(best-effort JSON extraction from a raw reviewer/arbiter response, formerly the
`extract_json` bash function), aggregate_findings (the per-reviewer jq merge loop
that tags each finding with reviewer+lens), and the verdict-finalization pair
count_tiers + render_tier_section (the tier-count jq + the `render_tier_findings`
jq filter). All parity refactors (binding 6) — no behavior change, no suppression.
The shell now calls the `extract-json`, `aggregate`, `tier-counts`, and
`render-tier` subcommands instead of re-implementing the logic. Per binding 5 the
aggregate/tier-counts/render-tier subcommands surface STRUCTURAL parse failures
non-zero (exit 2) while still writing a fallback — they do not adopt the default
ranking path's blanket exit-0.

PURITY (binding 6 / AC4): count_corroboration, reconcile_membership,
rank_findings, compute_triage_floor, compute_sqc_sample, extract_json,
aggregate_findings, count_tiers, and render_tier_section perform NO subprocess,
network, or file I/O. They are importable and unit-testable with plain dicts /
synthetic inputs. Only the __main__ CLI shim does I/O.

FAIL-OPEN (binding 7): a finding with no resolvable membership defaults to
corroborated_by=1, corroboration_tier=2. Nothing is ever suppressed (binding 9).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys

# Severity rank: lower = more severe = ordered earlier. Unknown sorts last.
_SEVERITY_RANK = {"tier1": 0, "tier2": 1, "tier3": 2, "tier4": 3}
_SEVERITY_UNKNOWN = 99

# Line-proximity tolerance for the fallback reconciliation (OQ-1 default, ratified).
_LINE_PROXIMITY = 5

# --------------------------------------------------------------------------- #
# SPEC-332 — deterministic triage floor + SQC sampling.                       #
#                                                                             #
# These constants are the SINGLE SOURCE OF TRUTH for the panel's deterministic #
# risk floor. They are transcribed BYTE-FOR-BYTE from the former `det_floor`   #
# bash function in run_panel.sh (architect binding 6 — parity refactor) and    #
# matched case-INSENSITIVELY, reproducing the shell's `grep -qiE`.             #
#                                                                             #
# HARDCODED, NOT CONFIGURABLE (architect binding 3 / Spec OQ-2): changing any  #
# pattern or the size floor is a BEHAVIOR change that requires a separate spec  #
# and a product gate. A consumer who needs project-specific risk escalation    #
# rules must add them in the PROJECT region of the relevant agent, NOT by      #
# editing these constants.                                                     #
# --------------------------------------------------------------------------- #

# Source-code extensions → MEDIUM floor (run_panel.sh det_floor line 268).
_SRC_EXT_RE = re.compile(r"\.(ts|tsx|js|jsx|py|go|rb|java|cs|php|rs|sh)$", re.IGNORECASE)
# Dependency manifests → MEDIUM floor (run_panel.sh det_floor line 269).
_DEP_MANIFEST_RE = re.compile(
    r"(package\.json|package-lock|yarn\.lock|pnpm-lock|requirements\.txt"
    r"|go\.mod|Gemfile|Cargo\.toml|composer\.json)",
    re.IGNORECASE,
)
# Auth/session/persistence path segments → HIGH floor (run_panel.sh line 271).
_HIGH_PATH_RE = re.compile(
    r"(auth|login|session|middleware|password|token|crypto|secret"
    r"|/api/|routes?/|migrations?/|schema|/db/|sql)",
    re.IGNORECASE,
)
# Payment/destructive path segments → CRITICAL floor (run_panel.sh line 272).
_CRITICAL_PATH_RE = re.compile(
    r"(payment|billing|stripe|checkout|/delete|destroy|drop_)",
    re.IGNORECASE,
)

# Added-line count that ratchets the floor to at least MEDIUM (run_panel.sh
# SIZE_FLOOR, line 69). A parameter default — tests exercise the boundary (R1/AC4).
_DEFAULT_SIZE_FLOOR = 500

# Tier ranking — reproduces the shell `rank`/`max_risk` helpers for the floor
# ratchet ONLY. The shell keeps its own rank/max_risk (architect binding 2); this
# is the in-module equivalent used solely to ratchet the floor upward.
_TIER_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def _max_tier(a: str, b: str) -> str:
    """Return the higher of two tier strings by _TIER_RANK (never lowers).

    Reproduces the shell `max_risk` helper for the triage-floor ratchet. Unknown
    tiers rank 0 (LOW), matching the shell `rank` default. Pure.
    """
    return a if _TIER_RANK.get(a, 0) >= _TIER_RANK.get(b, 0) else b


def compute_triage_floor(
    changed_files: list[str],
    added_lines: int,
    size_floor: int = _DEFAULT_SIZE_FLOOR,
) -> str:
    """Compute the deterministic risk floor for a PR (SPEC-332 R1).

    Byte-for-byte parity with the former `det_floor` bash function (binding 6):
    the floor starts at LOW and RATCHETS UP through five sequential rules, each of
    which can only raise it (`max_risk` never lowers):
      1. any source-code extension          -> MEDIUM
      2. any dependency manifest            -> MEDIUM
      3. added_lines > size_floor           -> at least MEDIUM
      4. any auth/persistence path segment  -> HIGH
      5. any payment/destructive segment    -> CRITICAL

    Patterns are matched against the newline-joined file blob, case-insensitively,
    exactly as the shell ran `echo "$files" | grep -qiE`. Returns one of
    "LOW"/"MEDIUM"/"HIGH"/"CRITICAL". Does NOT combine the author trailer or Haiku
    output — that stays in shell (binding 2).

    Pure: no subprocess, file, network, or env I/O (R4). Does not mutate inputs.
    The size_floor boundary is STRICT `>` (parity with shell `(( added > SIZE_FLOOR ))`).
    """
    files_blob = "\n".join(changed_files)
    level = "LOW"
    if _SRC_EXT_RE.search(files_blob):
        level = "MEDIUM"
    if _DEP_MANIFEST_RE.search(files_blob):
        level = "MEDIUM"
    if added_lines > size_floor:
        level = _max_tier(level, "MEDIUM")
    if _HIGH_PATH_RE.search(files_blob):
        level = _max_tier(level, "HIGH")
    if _CRITICAL_PATH_RE.search(files_blob):
        level = _max_tier(level, "CRITICAL")
    return level


def compute_sqc_sample(
    head_sha: str,
    salt: str,
    tier: str,
    sample_rates: dict,
) -> dict:
    """Decide whether a PR is selected for the random red-team audit (SPEC-332 R2).

    Salted-deterministic Statistical Quality Control sample (DECISIONS.md D17):
    selected iff SHA256(head_sha + salt)[:8] (hex) % 100 < tier_rate. Reproducible
    (an auditor with the salt can prove a PR was/wasn't sampled) and non-gameable
    (the salt is secret). Byte-for-byte parity with run_panel.sh lines 326-330
    (binding 6): the shell hashed `printf '%s' "${HEAD_SHA}${SALT}"` (no newline),
    took hex chars 1-8, and `% 100`.

    HIGH/CRITICAL are NOT sampled by this function — they are absent from
    `sample_rates` (rate 0) and fire their adversary pass via a SEPARATE shell path
    (run_panel.sh line 356). For them this returns {"sampled": False, "roll": -1,
    "rate": 0} (AC8).

    Returns {"sampled": bool, "roll": int (0-99, or -1 when no roll), "rate": int}.
    The shell consumes `roll`/`sampled` for its sample-log; this function performs NO
    file I/O and never mints or persists the salt (Spec §5). Pure (R4).
    """
    rate = int(sample_rates.get(tier, 0))
    if rate <= 0:
        return {"sampled": False, "roll": -1, "rate": 0}
    digest = hashlib.sha256((head_sha + salt).encode()).hexdigest()
    roll = int(digest[:8], 16) % 100
    return {"sampled": roll < rate, "roll": roll, "rate": rate}


def count_corroboration(deduplicated_finding: dict) -> tuple[int, list[str]]:
    """Count INDEPENDENT vendors that corroborate one deduplicated finding.

    Reads `merged_from` (list of {"reviewer","lens"}) — the membership list the
    Sonnet arbiter emits (binding 2). Counts DISTINCT `reviewer` (vendor) values:
    the same vendor under two lenses collapses to one (binding 3).

    Returns (corroborated_by_count, sorted_unique_reviewers). The count is always
    >= 1 (binding 7 fail-open): a finding with missing/empty/malformed membership
    defaults to a single corroborating reviewer (its own `reviewer`, or "unknown").
    Pure: no I/O, does not mutate the input.
    """
    merged_from = deduplicated_finding.get("merged_from")
    if isinstance(merged_from, list) and merged_from:
        vendors = sorted(
            {
                entry["reviewer"]
                for entry in merged_from
                if isinstance(entry, dict) and entry.get("reviewer")
            }
        )
        if vendors:
            return (len(vendors), vendors)

    # Fail-open floor: a finding always counts as corroborated by at least itself.
    own = deduplicated_finding.get("reviewer") or "unknown"
    return (1, [own])


def reconcile_membership(raw_findings: list, finding: dict) -> list:
    """Reconstruct membership by file+line proximity — FALLBACK ONLY (binding 3).

    Called only when a finding's `merged_from` is missing/empty (a degraded
    arbiter response). Matches a raw finding to `finding` iff they share the same
    file path AND their line numbers are within +/-5 (binding 3 / OQ-1 default).

    Returns a list of {"reviewer","lens"} dicts (the reconstructed membership)
    suitable to assign to finding["merged_from"]. A finding with no file/line, or
    no matches, returns []. Pure: reads only its two arguments; no I/O.
    """
    f_file = finding.get("file")
    f_line = finding.get("line")
    if not f_file or f_line is None or not isinstance(raw_findings, list):
        return []
    try:
        f_line_i = int(f_line)
    except (TypeError, ValueError):
        return []

    membership: list[dict] = []
    for raw in raw_findings:
        if not isinstance(raw, dict):
            continue
        if not raw.get("file") or raw.get("file") != f_file:
            continue
        r_line = raw.get("line")
        if r_line is None:
            continue
        try:
            r_line_i = int(r_line)
        except (TypeError, ValueError):
            continue
        if abs(r_line_i - f_line_i) <= _LINE_PROXIMITY:
            membership.append(
                {"reviewer": raw.get("reviewer"), "lens": raw.get("lens")}
            )
    return membership


def _severity_key(finding: dict) -> int:
    return _SEVERITY_RANK.get(finding.get("severity"), _SEVERITY_UNKNOWN)


def rank_findings(findings: list) -> list:
    """Order findings: Tier 1 (corroborated_by >= 2) before Tier 2, severity within.

    Sort key (all ascending, binding 3 + 4):
      1. corroboration_tier   (1 before 2; absent -> fail-open 2)
      2. severity rank        (tier1 most severe first; absent -> last)
      3. file, then line      (stable, deterministic tie-break)

    Returns a NEW sorted list. Pure: does not mutate inputs or perform I/O.
    Findings missing corroboration_tier are ordered as tier 2 (binding 7) without
    being mutated.
    """
    if not isinstance(findings, list):
        return []

    def key(finding: dict):
        tier = finding.get("corroboration_tier")
        if tier not in (1, 2):
            tier = 2  # fail-open ordering
        return (
            tier,
            _severity_key(finding),
            str(finding.get("file") or ""),
            int(finding.get("line") or 0) if str(finding.get("line") or "0").lstrip("-").isdigit() else 0,
        )

    return sorted(findings, key=key)


# --------------------------------------------------------------------------- #
# SPEC-333 — JSON extraction, finding aggregation, verdict finalization.      #
#                                                                             #
# Three deterministic transforms moved out of run_panel.sh under the #314      #
# policy (Python for logic, shell for launch). PARITY REFACTOR (binding 6):    #
# extract_json reproduces the former `extract_json` bash function byte-for-    #
# byte; aggregate_findings reproduces the per-reviewer jq merge loop; and      #
# count_tiers / render_tier_section reproduce the tier-count jq + the          #
# `render_tier_findings` jq filter. No behavior change, no suppression.        #
#                                                                             #
# PURITY (binding 6 / Spec R5): all four are pure — no subprocess, network, or #
# file I/O — and do not mutate their inputs. Only the __main__ CLI shim does   #
# I/O. extract_json NEVER raises (Spec R1).                                    #
# --------------------------------------------------------------------------- #

# Fenced-block matcher (strategy 2) — verbatim from the shell extract_json:
# ```json ... ``` or ``` ... ```, dot-matches-newline, json tag case-insensitive.
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.IGNORECASE)


def extract_json(reviewer_response: str) -> dict:
    """Best-effort extract of a JSON value from a raw reviewer/arbiter response.

    Three-strategy parse, reproducing the former `extract_json` bash function
    (run_panel.sh lines 111-134) exactly (Spec R1):
      1. whole string is clean JSON                -> json.loads
      2. fenced ```json ... ``` / ``` ... ``` block -> json.loads the inner text
      3. JSON embedded in prose                     -> JSONDecoder().raw_decode
         from the first '{' or '[' (robust to trailing text after the close)
      4. none parse                                 -> fallback {"findings": []}

    Returns the parsed value (a dict, or a list when the top-level JSON is an
    array — parity with the shell, which returned whatever parsed). The return
    type is annotated `dict` per the task contract; callers apply their own
    `.findings // []` pluck on arrays. NEVER raises; empty/whitespace input is a
    documented benign degrade to the fallback (not an error).
    """
    def _load(s):
        try:
            return json.loads(s)
        except Exception:
            return None

    obj = _load(reviewer_response)              # 1) whole string is clean JSON
    if obj is None:                             # 2) fenced ```json ... ``` block
        m = _FENCE_RE.search(reviewer_response)
        if m:
            obj = _load(m.group(1))
    if obj is None:                             # 3) JSON embedded in prose
        dec = json.JSONDecoder()
        for i, ch in enumerate(reviewer_response):
            if ch in "{[":
                try:
                    obj, _ = dec.raw_decode(reviewer_response[i:])
                    break
                except Exception:
                    continue
    return obj if obj is not None else {"findings": []}


def aggregate_findings(reviewer_responses: list) -> list:
    """Merge findings from multiple reviewer responses into one tagged list.

    Reproduces the per-reviewer jq merge loop (run_panel.sh lines 444-462,
    Spec R2). Each input element is a dict {"reviewer", "lens", "raw"} where
    `raw` is the parsed JSON object for that reviewer. For each response, take
    `raw["findings"]` (default [] if absent), and tag each finding with the
    response's `reviewer` and `lens` (added or OVERWRITTEN — the roster spec is
    source-of-truth, not the model echo).

    Concatenated in input order: all of reviewer-1's findings, then reviewer-2's,
    etc. (insertion order = input order, Spec R2 / AC5). Pure: tags on a shallow
    copy, never mutates the caller's finding dicts. A non-dict finding is skipped
    (cannot tag a non-object — parity with the shell `jq map` + `|| echo '[]'`).
    """
    merged: list[dict] = []
    if not isinstance(reviewer_responses, list):
        return merged
    for response in reviewer_responses:
        if not isinstance(response, dict):
            continue
        reviewer = response.get("reviewer")
        lens = response.get("lens")
        raw = response.get("raw")
        findings = raw.get("findings", []) if isinstance(raw, dict) else []
        if not isinstance(findings, list):
            continue
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            tagged = dict(finding)
            tagged["reviewer"] = reviewer
            tagged["lens"] = lens
            merged.append(tagged)
    return merged


def count_tiers(findings: list) -> dict:
    """Summary counts over the RANKED findings list (Spec R3a).

    Returns {"total": int, "tier1": int, "tier2": int}:
      total = len(findings)
      tier1 = count where corroboration_tier == 1
      tier2 = count where corroboration_tier is absent or 2 ((// 2) == 2)

    Parity with the two independent shell jq filters (run_panel.sh lines
    518-519): a stray corroboration_tier value (e.g. 3) is counted in NEITHER
    tier — the filters are NOT normalized. Pure; non-list input -> all zeros.
    """
    if not isinstance(findings, list):
        return {"total": 0, "tier1": 0, "tier2": 0}
    tier1 = 0
    tier2 = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        tier = finding.get("corroboration_tier")
        if tier == 1:
            tier1 += 1
        if (tier if tier is not None else 2) == 2:
            tier2 += 1
    return {"total": len(findings), "tier1": tier1, "tier2": tier2}


def render_tier_section(findings: list, tier: int) -> str:
    """Render one corroboration tier's findings as markdown bullet lines (Spec R3b).

    Reproduces the `render_tier_findings` jq filter (run_panel.sh lines 566-570).
    Filter: (corroboration_tier // 2) == tier  — tier-2 selection includes
    findings missing the field. Each finding renders as:

      - **{severity} / {lens}** ({reviewers}) — `{file}:{line}` — **{title}** — {detail}

    Defaults: severity "tier?", lens "?", file "?", line 0, title "", detail "".
    `reviewers` = corroborating_reviewers joined by ", "; falls back to
    [reviewer], then ["panel"]. Lines joined by "\\n"; empty string if the tier
    has no findings (Spec AC9). Pure.
    """
    if not isinstance(findings, list):
        return ""
    lines: list[str] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        ftier = finding.get("corroboration_tier")
        if (ftier if ftier is not None else 2) != tier:
            continue
        severity = finding.get("severity") or "tier?"
        lens = finding.get("lens") or "?"
        reviewers = finding.get("corroborating_reviewers")
        if not isinstance(reviewers, list) or not reviewers:
            own = finding.get("reviewer")
            reviewers = [own] if own else ["panel"]
        reviewers_str = ", ".join(str(r) for r in reviewers)
        file = finding.get("file") or "?"
        line = finding.get("line")
        line = 0 if line is None else line
        title = finding.get("title") or ""
        detail = finding.get("detail") or ""
        lines.append(
            f"- **{severity} / {lens}** ({reviewers_str}) — "
            f"`{file}:{line}` — **{title}** — {detail}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI shim — the ONLY place in this module that performs I/O (binding 6 note). #
# Reads the arbiter JSON object on stdin, annotates each finding with the      #
# corroboration fields, reorders findings, writes the object to stdout.        #
# --------------------------------------------------------------------------- #
def annotate_and_rank(arbiter_obj: dict, raw_findings: list | None = None) -> dict:
    """Annotate every finding with corroboration fields and reorder them.

    For a finding with empty/missing `merged_from`, if `raw_findings` is provided
    reconstruct membership via reconcile_membership first (binding 3 fallback).
    Then count corroboration, assign tier, and rank. Returns a NEW object; the
    `summary` field is passed through untouched. Never suppresses a finding
    (binding 9).
    """
    findings = arbiter_obj.get("findings")
    if not isinstance(findings, list):
        return arbiter_obj

    annotated: list[dict] = []
    for finding in findings:
        if not isinstance(finding, dict):
            annotated.append(finding)
            continue
        f = dict(finding)  # do not mutate caller's dict
        mf = f.get("merged_from")
        if (not isinstance(mf, list) or not mf) and raw_findings:
            recovered = reconcile_membership(raw_findings, f)
            if recovered:
                f["merged_from"] = recovered
        count, reviewers = count_corroboration(f)
        f["corroborated_by"] = count
        f["corroborating_reviewers"] = reviewers
        f["corroboration_tier"] = 1 if count >= 2 else 2
        annotated.append(f)

    out = dict(arbiter_obj)
    out["findings"] = rank_findings(annotated)
    return out


def _run_triage_floor(args) -> int:
    """SPEC-332 triage-floor subcommand: file list on stdin -> tier on stdout.

    Gates reviewer staffing — fails LOUD (non-zero) on bad input, never silently
    defaulting to LOW (the shell's set -euo pipefail then surfaces it).
    """
    files = [ln for ln in sys.stdin.read().splitlines()]
    floor = compute_triage_floor(files, args.added_lines, args.size_floor)
    sys.stdout.write(floor + "\n")
    return 0


def _run_sqc_sample(args) -> int:
    """SPEC-332 sqc-sample subcommand: emit {"sampled","roll","rate"} as JSON.

    The shell parses the JSON with jq for its sample-log + roster decision. Fails
    LOUD on bad input — the SQC decision gates whether an adversary pass runs.
    """
    rates = {"LOW": args.sample_low, "MEDIUM": args.sample_med}
    result = compute_sqc_sample(args.head_sha, args.salt, args.tier, rates)
    sys.stdout.write(json.dumps(result))
    return 0


# --------------------------------------------------------------------------- #
# SPEC-333 subcommand handlers. Per binding 5 these do NOT inherit the default #
# ranking path's blanket exit-0: extract-json degrades benignly (exit 0), but  #
# aggregate/tier-counts/render-tier surface a STRUCTURAL parse failure non-zero #
# (exit 2) while STILL writing the fallback value (no finding ever dropped).    #
# --------------------------------------------------------------------------- #
def _run_extract_json(args) -> int:
    """extract-json: raw response on stdin -> extracted JSON on stdout.

    'No parseable JSON' is an EXPECTED outcome on raw model text (Spec R1), so
    this writes the {"findings": []} fallback and exits 0 — it never requires
    valid JSON and never fails structurally.
    """
    raw = sys.stdin.read()
    sys.stdout.write(json.dumps(extract_json(raw)))
    return 0


def _run_aggregate(args) -> int:
    """aggregate: JSON array of {reviewer,lens,raw} on stdin -> tagged findings.

    The shell produced this array; malformed input is a STRUCTURAL break ->
    write the fallback [] but exit 2 (binding 5) so set -e surfaces it.
    """
    try:
        responses = json.loads(sys.stdin.read())
    except Exception:
        sys.stdout.write(json.dumps([]))
        return 2
    sys.stdout.write(json.dumps(aggregate_findings(responses)))
    return 0


def _run_tier_counts(args) -> int:
    """tier-counts: JSON array of ranked findings on stdin -> {total,tier1,tier2}.

    Malformed input -> write zero-counts fallback but exit 2 (binding 5).
    """
    try:
        findings = json.loads(sys.stdin.read())
    except Exception:
        sys.stdout.write(json.dumps({"total": 0, "tier1": 0, "tier2": 0}))
        return 2
    sys.stdout.write(json.dumps(count_tiers(findings)))
    return 0


def _run_render_tier(args) -> int:
    """render-tier: JSON array of ranked findings on stdin -> markdown bullets.

    Malformed input -> write empty string but exit 2 (binding 5).
    """
    try:
        findings = json.loads(sys.stdin.read())
    except Exception:
        return 2
    sys.stdout.write(render_tier_section(findings, args.tier))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Panel deterministic logic (SPEC-376 ranking + SPEC-332 triage/SQC)."
    )
    # SPEC-376 default-path args (kept on the top-level parser so the existing
    # `panel_logic.py --raw <file>` invocation with NO subcommand is unchanged).
    parser.add_argument(
        "--raw",
        default=None,
        help="path to findings.raw.json (fallback membership reconciliation)",
    )

    sub = parser.add_subparsers(dest="cmd")

    # SPEC-332 — triage-floor subcommand (binding 8).
    p_floor = sub.add_parser(
        "triage-floor", help="deterministic risk floor (file list on stdin)"
    )
    p_floor.add_argument("--added-lines", type=int, required=True)
    p_floor.add_argument("--size-floor", type=int, default=_DEFAULT_SIZE_FLOOR)

    # SPEC-332 — sqc-sample subcommand (binding 8).
    p_sqc = sub.add_parser(
        "sqc-sample", help="salted-deterministic red-team audit sample decision"
    )
    p_sqc.add_argument("--head-sha", required=True)
    p_sqc.add_argument("--salt", required=True)
    p_sqc.add_argument("--tier", required=True)
    p_sqc.add_argument("--sample-low", type=int, required=True)
    p_sqc.add_argument("--sample-med", type=int, required=True)

    # SPEC-333 — extraction / aggregation / finalization subcommands (binding 2).
    # All payloads arrive on STDIN (content can be shell-hostile), not argv.
    sub.add_parser("extract-json", help="extract JSON from a raw response (stdin)")
    sub.add_parser("aggregate", help="merge+tag findings from reviewer responses (stdin array)")
    sub.add_parser("tier-counts", help="count total/tier1/tier2 over ranked findings (stdin array)")
    p_render = sub.add_parser(
        "render-tier", help="render one tier's findings as markdown bullets (stdin array)"
    )
    p_render.add_argument("--tier", type=int, required=True, choices=(1, 2))

    args = parser.parse_args(argv)

    if args.cmd == "triage-floor":
        return _run_triage_floor(args)
    if args.cmd == "sqc-sample":
        return _run_sqc_sample(args)
    if args.cmd == "extract-json":
        return _run_extract_json(args)
    if args.cmd == "aggregate":
        return _run_aggregate(args)
    if args.cmd == "tier-counts":
        return _run_tier_counts(args)
    if args.cmd == "render-tier":
        return _run_render_tier(args)

    # Default (no subcommand): SPEC-376 corroboration ranking — unchanged.
    data = sys.stdin.read()
    # Fail-closed-safe: any parse/processing error -> echo input unchanged, exit 0.
    # Ranking is an enhancement, never a gate; the panel must still post findings.
    try:
        arbiter_obj = json.loads(data)
    except Exception:
        sys.stdout.write(data)
        return 0

    raw_findings = None
    if args.raw:
        try:
            with open(args.raw, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, list):
                raw_findings = loaded
        except Exception:
            raw_findings = None

    try:
        result = annotate_and_rank(arbiter_obj, raw_findings)
    except Exception:
        sys.stdout.write(json.dumps(arbiter_obj))
        return 0

    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
