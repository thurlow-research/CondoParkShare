#!/usr/bin/env bash
# run_second_review.sh — pre-PR cross-vendor second code review.
#
# Runs after internal review chain completes and risk validators score the step.
# Provides independent, cross-vendor perspective BEFORE the PR is opened.
# This is distinct from run_panel.sh (post-PR, posts PR threads that must resolve).
# Second review findings go to .claudetmp/second-review/ for oversight-evaluator.
#
# VENDOR ROLES (DECISIONS.md D4 — no Claude model as independent check):
#
#   agy (Gemini) — CONDITIONAL SCREENING
#     Fires when composite score ≥ OVERSIGHT_AGY_THRESHOLD (default: 0.30 = MEDIUM+).
#     Lens: correctness + spec adherence. Large context window fits whole-diff + spec.
#     Subscription: $20/month baseline → upgrade to $100/month to lower the threshold
#     without changing any logic (set OVERSIGHT_AGY_THRESHOLD lower in .env).
#
#   codex (OpenAI) — RESERVE
#     Fires when composite score ≥ OVERSIGHT_CODEX_THRESHOLD (default: 0.55 = HIGH+).
#     Lens: adversarial security probe against the project's specific threat model.
#     Stays at $20/month — scarcity is intentional; threshold controls frequency.
#     Do NOT upgrade to $100/month; it is a reserve tool, not a high-frequency one.
#
#   FALLBACK: if agy is unavailable and score ≥ CODEX_THRESHOLD, codex takes the
#     correctness lens too. NOTE: fallback runs ONE combined review (correctness +
#     security) instead of two separate targeted reviews. This is documented as an
#     intentional degradation — the alternative would be to require two codex calls
#     (expensive) or fail-closed (blocks the pipeline when agy is briefly unavailable).
#     At HIGH+, if BOTH vendors are unavailable, the script exits with a non-zero
#     status so the pipeline does not silently proceed without cross-vendor review.
#
# INDEPENDENCE: Do NOT pass internal reviewer (code-reviewer agent) findings to
# these reviewers. Independence is the value — decorrelated judgement catches
# different classes of bugs. The oversight-evaluator compares all sets of findings.
#
# ISSUE CREATION: critical/high severity findings create GitHub issues immediately
# (labels: second-review-finding). These feed the historical risk database and are
# visible to future risk assessor runs on the same files.
#
# THRESHOLDS (override via environment or .env):
#   OVERSIGHT_AGY_THRESHOLD=0.30    fire agy when composite score >= this
#   OVERSIGHT_CODEX_THRESHOLD=0.55  fire codex when composite score >= this
#
# DIFF-CENTRIC CONTEXT (SPEC-379): --diff-only is DEFAULT ON. Reviewers receive the
# diff as primary input — never the full file tree. Evidence (Kumar 2026 / SWE-PRBench)
# shows that providing more-than-diff context REDUCES reviewer detection rates. Pass
# --no-diff-only to disable (prints a startup warning). When --diff-only is on and a
# reviewer response asks for full-repository context, an ADVISORY entry is appended to
# the run's output file (non-blocking; does not change verdict or exit code).
#
# Usage:
#   ./scripts/run_second_review.sh --step 3 --score 0.67
#   ./scripts/run_second_review.sh --diff HEAD~1 --score 0.45
#   ./scripts/run_second_review.sh --files a.py b.py --score 0.71
#   ./scripts/run_second_review.sh --step 3 --no-diff-only   # disable diff-centric (warns)
#   --diff-only       (default) reviewers get only the diff; advisory on context requests
#   --no-diff-only    disable diff-centric mode — full context allowed (NOT recommended)
#
# Prerequisites: agy authenticated (`agy` login), codex authenticated (`codex` login)

set -euo pipefail

# Load project .env if present (for threshold overrides)
[[ -f .env ]] && set -o allexport && source .env && set +o allexport 2>/dev/null || true

# Thresholds — override via environment
AGY_THRESHOLD="${OVERSIGHT_AGY_THRESHOLD:-0.30}"
CODEX_THRESHOLD="${OVERSIGHT_CODEX_THRESHOLD:-0.55}"

OUT_DIR=".claudetmp/second-review"
STEP=""
SCORE=""
TIER=""
DIFF_REF=""
FILES=()
DIFF_ONLY=1   # SPEC-379: diff-centric review is DEFAULT ON (Kumar 2026 / SWE-PRBench)
# SPEC-78: ledger file resolved after STEP is confirmed; see post-parse dispatch below.
LEDGER_FILE=""
_SUBCMD=""
_REC_FILES=""
_REC_CLASS=""
_REC_DISP=""

# SPEC-219: the reviewed commit range, recorded in every report header so the
# oversight-evaluator can verify the second review covered the step's canonical
# base_sha..head_sha. Default to the absent-range sentinel `none` (architect
# binding B2) so the field is NEVER emitted empty, on any path. Resolved to a
# full-SHA pair, `UNCOMMITTED`, or `none` at diff-derivation time (binding B3).
REVIEWED_RANGE="none"

# SPEC-379 R4 — advisory pattern list (case-insensitive). When --diff-only is on and a
# reviewer's response contains one of these, an ADVISORY (non-blocking) is logged.
# This is the single named location for the pattern list in this script.
DIFF_ONLY_REQUEST_PATTERNS='full repo|all files|entire codebase|repository context|all source files|project files'

while [[ $# -gt 0 ]]; do
    case "$1" in
        --score)        SCORE="$2";    shift 2 ;;
        --tier)         TIER="$2";     shift 2 ;;
        --step)         STEP="$2";     shift 2 ;;
        --diff)         DIFF_REF="$2"; shift 2 ;;
        --files)        shift; while [[ $# -gt 0 && "$1" != --* ]]; do FILES+=("$1"); shift; done ;;
        --diff-only)    DIFF_ONLY=1; shift ;;
        --no-diff-only) DIFF_ONLY=0; shift ;;
        # SPEC-78: ledger subcommands. --step must be supplied before these.
        --record)
            _SUBCMD="record"
            _REC_FILES="${2:-}"; _REC_CLASS="${3:-}"; _REC_DISP="${4:-}"
            shift; [[ $# -ge 1 ]] && shift; [[ $# -ge 1 ]] && shift; [[ $# -ge 1 ]] && shift ;;
        --reset)
            _SUBCMD="reset"; shift ;;
        *)              shift ;;
    esac
done

# SPEC-379 R2 — opting out of diff-centric mode emits a startup warning.
if [[ "$DIFF_ONLY" -eq 0 ]]; then
    echo "[WARN] --diff-only disabled: full-file context enabled. Evidence (Kumar 2026 / SWE-PRBench) shows this can reduce reviewer detection rates." >&2
fi

if [[ -z "$STEP" ]]; then
    echo "Error: --step <N> is required (used to name output and match oversight-evaluator lookup)" >&2
    exit 1
fi

# SPEC-78: resolve ledger path now that STEP is confirmed.
LEDGER_FILE="${OUT_DIR}/step${STEP}-ledger.jsonl"

# SPEC-78: post-parse dispatch for --record and --reset subcommands.
# These short-circuit before any reviewer invocation or diff-derivation.
if [[ "$_SUBCMD" == "record" ]]; then
    [[ -z "$_REC_FILES" || -z "$_REC_CLASS" || -z "$_REC_DISP" ]] && {
        echo "Usage: run_second_review.sh --step <N> --record <files> <class> <disposition>" >&2
        echo "  disposition: fixed | filed:#<N> | residual | noise" >&2
        exit 1
    }
    mkdir -p "$OUT_DIR"
    python3 "$(dirname "$0")/oversight/validation_logic.py" record \
        --ledger "$LEDGER_FILE" \
        --files "$_REC_FILES" \
        --class "$_REC_CLASS" \
        --disposition "$_REC_DISP"
    exit $?
fi
if [[ "$_SUBCMD" == "reset" ]]; then
    rm -f "$LEDGER_FILE"
    echo "reset: removed ledger for step ${STEP} (${LEDGER_FILE})"
    exit 0
fi

# Read score from validator summary if not provided
if [[ -z "$SCORE" && -f ".claudetmp/oversight/validators/summary.json" ]]; then
    SCORE=$(python3 -c \
        "import json; d=json.load(open('.claudetmp/oversight/validators/summary.json')); \
         print(d.get('composite_score', 0))" 2>/dev/null || echo "0")
fi

SCORE="${SCORE:-0}"

# Determine which reviewers fire.
# Fire on the validated TIER floor OR the composite score — whichever demands
# more review. The deterministic risk floor raises tier (auth→HIGH, booking/
# payment→CRITICAL) WITHOUT raising the composite score, so a HIGH-by-floor step
# can have a low score; gating on score alone would silently skip the mandatory
# cross-vendor review the tier requires. Tier is the ratchet floor here too.
RUN_AGY=false
RUN_CODEX=false
AGY_AVAILABLE=false
CODEX_AVAILABLE=false

# Reviewer selection (SPEC-331): the threshold comparison and tier-floor rules
# live in scripts/oversight/second_review_logic.py (named, unit-testable). The
# module prints RUN_AGY=true|false and RUN_CODEX=true|false for us to eval. Tier
# normalization and the >= threshold comparison are done inside the module.
SELECT_OUT=$(python3 "$(dirname "$0")/oversight/second_review_logic.py" \
    select-reviewers --score "$SCORE" --tier "$TIER" \
    --agy-threshold "$AGY_THRESHOLD" --codex-threshold "$CODEX_THRESHOLD")
eval "$SELECT_OUT"   # sets RUN_AGY / RUN_CODEX to true|false

if ! $RUN_AGY && ! $RUN_CODEX; then
    echo "run_second_review: score=$SCORE below both thresholds (agy≥$AGY_THRESHOLD, codex≥$CODEX_THRESHOLD) and tier=${TIER:-none} below MEDIUM — skip"
    # Write a sentinel so oversight-evaluator can distinguish "skipped" from "missing"
    mkdir -p ".claudetmp/second-review"
    TS=$(date +%Y%m%dT%H%M%S)
    cat > ".claudetmp/second-review/step${STEP}-${TS}.md" <<EOF
# Second Review — Step ${STEP}
Timestamp: ${TS}
verdict: skipped
reviewed_range: none
reason: composite score=${SCORE} below both thresholds (agy≥${AGY_THRESHOLD}, codex≥${CODEX_THRESHOLD}) and tier=${TIER:-none} below MEDIUM
agy_threshold: ${AGY_THRESHOLD}
codex_threshold: ${CODEX_THRESHOLD}
validated_tier: ${TIER:-none}
EOF
    exit 0
fi

# Check availability
command -v agy &>/dev/null  && AGY_AVAILABLE=true  || true
command -v codex &>/dev/null && CODEX_AVAILABLE=true || true

# Fallback: if agy unavailable and codex threshold reached, codex handles both lenses
if $RUN_AGY && ! $AGY_AVAILABLE && $CODEX_AVAILABLE && $RUN_CODEX; then
    echo "run_second_review: agy unavailable — codex will cover correctness lens too (degraded: one combined review)"
fi

# Fail-closed checks by risk band:
#   MEDIUM (score ≥ 0.30): agy is required. If agy unavailable and codex can't cover
#     (score < codex threshold), fail — an unreviewed MEDIUM step cannot proceed silently.
#   HIGH+ (score ≥ CODEX_THRESHOLD): agy required + codex required. Codex FALLBACK is
#     allowed when agy is unavailable (one combined review instead of two targeted ones).
#     Fail only if BOTH vendors are unavailable. This is documented in contract §7.
if $RUN_AGY && ! $AGY_AVAILABLE && ! $RUN_CODEX; then
    echo "ERROR: score=${SCORE} is MEDIUM+ (agy required) but agy is unavailable and" >&2
    echo "       score is below codex threshold (${CODEX_THRESHOLD}) — no fallback reviewer." >&2
    echo "Options:" >&2
    echo "  1. Authenticate agy: ./scripts/setup_clis.sh auth" >&2
    echo "  2. Human override: create .claudetmp/oversight/human-tier-override.md" >&2
    exit 1
fi

python3 -c "
s=float('${SCORE:-0}'); threshold=float('${CODEX_THRESHOLD}')
exit(0 if s < threshold else 1)
" 2>/dev/null || {
    if ! $AGY_AVAILABLE && ! $CODEX_AVAILABLE; then
        echo "ERROR: score=${SCORE} is HIGH+ (≥${CODEX_THRESHOLD}) but neither agy nor codex is available." >&2
        echo "Options:" >&2
        echo "  1. Authenticate a reviewer: ./scripts/setup_clis.sh auth" >&2
        echo "  2. Human override: create .claudetmp/oversight/human-tier-override.md" >&2
        exit 1
    fi
}

mkdir -p "$OUT_DIR"
TIMESTAMP=$(date +%Y%m%dT%H%M%S)
OUTFILE="$OUT_DIR/step${STEP}-${TIMESTAMP}.md"

# --- Build diff content + capture reviewed range (SPEC-219) ---
# REVIEWED_RANGE is resolved in the SAME if/elif/else that derives DIFF_CONTENT so
# the recorded range and the diff source can never diverge (binding B3), and the
# UNCOMMITTED/none sentinels are mutually exclusive by construction (binding B4):
# only one branch runs. All SHAs are full 40-char (git rev-parse / get_step_range).
if [[ -n "$DIFF_REF" && "$DIFF_REF" == *..* ]]; then
    # Path: --diff <A>..<B> (range form). BASE=rev-parse A, HEAD=rev-parse B.
    DIFF_CONTENT=$(git diff "$DIFF_REF" 2>/dev/null || echo "")
    _RANGE_A="${DIFF_REF%%..*}"
    _RANGE_B="${DIFF_REF##*..}"
    _BASE_SHA=$(git rev-parse "$_RANGE_A" 2>/dev/null || echo "")
    _HEAD_SHA=$(git rev-parse "$_RANGE_B" 2>/dev/null || echo "")
    [[ -n "$_BASE_SHA" && -n "$_HEAD_SHA" ]] && REVIEWED_RANGE="${_BASE_SHA}..${_HEAD_SHA}"
elif [[ -n "$DIFF_REF" ]]; then
    # Path 1: --diff <ref> (single ref). git diff <ref> compares <ref> to the tree.
    # BASE=rev-parse <ref>, HEAD=rev-parse HEAD — NEW calls at derivation time (B3).
    DIFF_CONTENT=$(git diff "$DIFF_REF" 2>/dev/null || echo "")
    _BASE_SHA=$(git rev-parse "$DIFF_REF" 2>/dev/null || echo "")
    _HEAD_SHA=$(git rev-parse HEAD 2>/dev/null || echo "")
    [[ -n "$_BASE_SHA" && -n "$_HEAD_SHA" ]] && REVIEWED_RANGE="${_BASE_SHA}..${_HEAD_SHA}"
elif [[ ${#FILES[@]} -gt 0 ]]; then
    # Path 3 (scoped): --files ... → HEAD-vs-worktree for named files. A dirty
    # worktree means the review sees uncommitted state → UNCOMMITTED (BC-219-3).
    DIFF_CONTENT=$(git diff HEAD -- "${FILES[@]}" 2>/dev/null || cat "${FILES[@]}" 2>/dev/null || echo "")
    if ! git diff --quiet HEAD -- "${FILES[@]}" 2>/dev/null; then
        REVIEWED_RANGE="UNCOMMITTED"
    else
        _HEAD_SHA=$(git rev-parse HEAD 2>/dev/null || echo "")
        [[ -n "$_HEAD_SHA" ]] && REVIEWED_RANGE="${_HEAD_SHA}..${_HEAD_SHA}"
    fi
else
    # --step N path: canonical step range via the shared helper (SPEC-220 / binding
    # B1). Used when no --diff/--files narrows the diff — the run relies on the
    # step's canonical range. The helper owns range derivation; we own the
    # merge-base fallback for a leading-empty base and the empty→none sentinel.
    if [[ -f "$(dirname "$0")/oversight/lib/step_range.sh" ]]; then
        # shellcheck source=scripts/oversight/lib/step_range.sh
        . "$(dirname "$0")/oversight/lib/step_range.sh"
        _STEP_RANGE=$(get_step_range "$STEP" 2>/dev/null || echo "")
    else
        _STEP_RANGE=""
    fi
    if [[ -z "$_STEP_RANGE" ]]; then
        # Step N has no event in the log → no range. Do NOT invoke a reviewer; emit
        # the no-content sentinel with reviewed_range: none (binding B2, AC-12).
        echo "run_second_review: step ${STEP} has no range in audit log — writing skipped (none) sentinel"
        mkdir -p "$OUT_DIR"
        TS=$(date +%Y%m%dT%H%M%S)
        cat > "$OUT_DIR/step${STEP}-${TS}.md" <<EOF
# Second Review — Step ${STEP}
Timestamp: ${TS}
verdict: skipped
highest_severity: none
unresolved_findings: 0
reviewed_range: none
reason: no commit range for step ${STEP} in audit log
EOF
        exit 0
    fi
    _BASE_SHA="${_STEP_RANGE%%..*}"
    _HEAD_SHA="${_STEP_RANGE##*..}"
    if [[ -z "$_BASE_SHA" ]]; then
        # Leading-empty base (step 1, or step N-1 has no event). Merge-base fallback
        # — byte-identical to the SPEC-220 evaluator so both resolve BASE the same
        # way (binding B1).
        _BASE_SHA=$(git merge-base HEAD "$(git rev-parse HEAD~1 2>/dev/null || echo HEAD)")
    fi
    REVIEWED_RANGE="${_BASE_SHA}..${_HEAD_SHA}"
    DIFF_CONTENT=$(git diff "${_BASE_SHA}..${_HEAD_SHA}" 2>/dev/null || echo "")
fi

if [[ -z "$DIFF_CONTENT" ]]; then
    echo "run_second_review: no diff content — writing skipped sentinel"
    mkdir -p "$OUT_DIR"
    TS=$(date +%Y%m%dT%H%M%S)
    cat > "$OUT_DIR/step${STEP}-${TS}.md" <<EOF
# Second Review — Step ${STEP}
Timestamp: ${TS}
verdict: skipped
highest_severity: none
unresolved_findings: 0
reviewed_range: none
reason: no diff content detected
EOF
    exit 0
fi

SPEC_CONTEXT=""
# Load project identity from config.sh (#685 — was hardcoded to CondoParkShare)
PROJECT_NAME="" PROJECT_STACK="" SPEC_FILE=""
[[ -f "scripts/framework/config.sh" ]] && source scripts/framework/config.sh
# Consumer projects may define SPEC_FILE in their config.sh
SPEC_CONTEXT=""
if [[ -n "${SPEC_FILE:-}" && -f "$SPEC_FILE" ]]; then
    SPEC_CONTEXT=$(cat "$SPEC_FILE")
fi

VALIDATOR_SUMMARY=""
[[ -f ".claudetmp/oversight/validators/summary.json" ]] && \
    VALIDATOR_SUMMARY=$(cat ".claudetmp/oversight/validators/summary.json")

echo "=== Second review: step=${STEP} score=${SCORE} ==="
echo "  agy threshold:   $AGY_THRESHOLD  → $(  $RUN_AGY   && echo "FIRE"   || echo "skip")"
echo "  codex threshold: $CODEX_THRESHOLD → $($RUN_CODEX && echo "FIRE" || echo "skip")"
echo "Output: $OUTFILE"
echo ""

# Machine-readable header written first; evaluator reads these top-level fields.
# Individual reviewer JSON blocks follow inside fenced sections.
# verdict and highest_severity are updated at the end of the script.
{
    printf "# Second Review — Step %s\n" "$STEP"
    printf "Score: %s | Timestamp: %s\n" "$SCORE" "$TIMESTAMP"
    printf "verdict: pending\n"
    printf "reviewed_range: %s\n" "$REVIEWED_RANGE"
    printf "highest_severity: none\n"
    printf "unresolved_findings: 0\n"
    printf "blocking_count: 0\n"
    printf "new_blocking_count: 0\n"
    printf "agy_threshold: %s | codex_threshold: %s\n\n" "$AGY_THRESHOLD" "$CODEX_THRESHOLD"
} > "$OUTFILE"

# ── Helper: create GitHub issue for high/critical findings ──────────────────
create_finding_issues() {
    local reviewer="$1"
    local findings_json="$2"

    python3 - "$reviewer" "$findings_json" "$STEP" <<'PYEOF'
import json, subprocess, sys

reviewer = sys.argv[1]
step = sys.argv[3]

try:
    data = json.loads(sys.argv[2])
    findings = data.get("findings", [])
except Exception:
    sys.exit(0)

for f in findings:
    sev = f.get("severity", "low").lower()
    if sev not in ("critical", "high"):
        continue

    title = f"Second review [{reviewer}]: {f.get('finding','?')[:80]}"
    cwe = f.get("cwe", "")
    body_parts = [
        f"**Reviewer:** {reviewer}",
        f"**Step:** {step}",
        f"**Severity:** {sev}",
    ]
    if cwe:
        body_parts.append(f"**CWE:** {cwe}")
    body_parts += [
        f"**File:** {f.get('file','?')}:{f.get('line','?')}",
        f"**Finding:** {f.get('finding','')}",
        f"**Why:** {f.get('why', f.get('attack_scenario',''))}",
        f"**Suggestion:** {f.get('suggestion','')}",
        "",
        "*Created by run_second_review.sh — feeds historical risk assessor.*",
    ]

    cmd = [
        "gh", "issue", "create",
        "--title", title,
        "--body", "\n".join(body_parts),
        "--label", "second-review-finding",
    ]
    if sev == "critical":
        cmd += ["--label", "security-finding"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            print(f"  Issue created: {result.stdout.strip()}")
    except Exception as e:
        print(f"  Issue creation failed: {e}", file=sys.stderr)
PYEOF
}

# ── JSON salvage (HOS#113) ──────────────────────────────────────────────────
# Agentic review CLIs (agy especially) sometimes wrap the requested JSON in
# markdown fences or prose, or narrate instead of emitting JSON at all. This
# reads a CLI's raw response on stdin and prints the first balanced, parseable
# {...} object that looks like a review (has verdict / findings / attacks). It is
# STRING-AWARE so a brace inside a JSON string value can't fool the scan. Prints
# nothing and exits 1 when there is no review JSON to salvage (true prose).
salvage_review_json() {
    # Data comes via env (REVIEW_RAW), NOT stdin: the heredoc already occupies
    # stdin as the python program, so piping the data in would be discarded.
    REVIEW_RAW="$1" python3 - <<'PYEOF'
import json, os, sys

raw = os.environ.get("REVIEW_RAW", "")

def objects(s):
    depth = 0; start = None; in_str = False; esc = False
    for i, ch in enumerate(s):
        if in_str:
            if esc:            esc = False
            elif ch == '\\':   esc = True
            elif ch == '"':    in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == '{':
            if depth == 0: start = i
            depth += 1
        elif ch == '}' and depth > 0:
            depth -= 1
            if depth == 0:
                yield s[start:i + 1]

for cand in objects(raw):
    try:
        obj = json.loads(cand)
    except Exception:
        continue
    if isinstance(obj, dict) and ("verdict" in obj or "findings" in obj or "attacks" in obj):
        print(json.dumps(obj))
        sys.exit(0)
sys.exit(1)
PYEOF
}

# ── SPEC-379 R4: advisory when a reviewer requests full-repository context ────
# Non-blocking. Appends an ADVISORY block to $OUTFILE (the .claudetmp/second-review/
# file the oversight-evaluator reads). Does NOT change verdict, severity, or exit code.
log_context_advisory() {
    local reviewer="$1" response="$2"
    [[ "$DIFF_ONLY" -eq 1 ]] || return 0
    local match
    match=$(printf '%s' "$response" | grep -ioE "$DIFF_ONLY_REQUEST_PATTERNS" | head -1 || true)
    [[ -n "$match" ]] || return 0
    {
        echo "## [ADVISORY] Full-context request — diff-centric mode (SPEC-379)"
        echo '```'
        echo "[ADVISORY] Reviewer requested full-file/full-repository context while --diff-only is on."
        echo "Reviewer: ${reviewer}"
        echo "Matched pattern: ${match}"
        echo "Action: Full-context request not fulfilled. If a specific artifact is needed,"
        echo "re-invoke with the named file passed as targeted context."
        echo '```'
        echo ""
    } >> "$OUTFILE"
    echo "  [ADVISORY] ${reviewer} requested full-repo context (pattern: '${match}') — logged, non-blocking"
}

# ── agy: correctness + spec adherence ───────────────────────────────────────
run_agy_review() {
    local lens="$1"
    local extra_instructions="$2"

    local prompt="You are an independent code reviewer. Your lens is CORRECTNESS and SPEC ADHERENCE.

IMPORTANT — this is a READ-ONLY review. Base your review ONLY on the diff and context provided below. Do NOT run shell commands, execute tests, or create/modify any files. Output your review directly; do not narrate tool use.

## Application context
${PROJECT_NAME:-this project} — ${PROJECT_STACK:-see config.sh for stack details}.

## Your task
${extra_instructions}

Review this diff for:
1. Logic errors, off-by-one errors, incorrect conditions, missing edge cases
2. Spec adherence gaps — requirements that appear unimplemented or wrong
3. Stack-specific risks: race conditions, N+1 queries, cross-tenant data leaks (see PROJECT_STACK in config.sh)
4. Missing error handling required by the spec

Do NOT comment on style, formatting, or repeat obvious design decisions.

## Risk context (static analysis scores — NOT internal reviewer findings)
\`\`\`json
${VALIDATOR_SUMMARY}
\`\`\`

## Product spec
<spec>
${SPEC_CONTEXT}
</spec>

## Diff
\`\`\`diff
${DIFF_CONTENT}
\`\`\`

Return JSON only:
{
  \"reviewer\": \"agy\",
  \"lens\": \"${lens}\",
  \"findings\": [
    {
      \"severity\": \"critical|high|medium|low\",
      \"file\": \"path/to/file.py\",
      \"line\": 0,
      \"finding\": \"one sentence: what is wrong\",
      \"why\": \"one sentence: why this is a problem\",
      \"suggestion\": \"specific change\"
    }
  ],
  \"verdict\": \"approve|request_changes\",
  \"summary\": \"one paragraph\"
}"

    # --sandbox: terminal restrictions so the review cannot mutate the working
    # tree. agy is an AGENTIC CLI — without this it has run pytest and created
    # files mid-review (HOS#113). A review step must never write to the tree.
    #
    # agy has no JSON-output mode and intermittently returns prose narration
    # instead of the requested JSON (HOS#113), which previously degraded to a
    # SILENT zero-findings "pass" and let the release gate through. Salvage the
    # JSON from any prose wrapper; if the first response is pure narration, retry
    # ONCE with a hard JSON-only reinforcement; only then fail — and fail with a
    # DISTINCT, honest error so the gate sees "review NOT performed", not "clean".
    local raw clean
    raw=$(agy --sandbox -p "$prompt" 2>/dev/null) || raw=""
    clean=$(salvage_review_json "$raw") || clean=""
    if [[ -z "$clean" ]]; then
        local reinforce="$prompt

CRITICAL OUTPUT REQUIREMENT: Your ENTIRE response must be a single JSON object and nothing else — no prose, no explanation, no markdown code fences. Start with { and end with }. Do not narrate tool use or your reasoning."
        raw=$(agy --sandbox -p "$reinforce" 2>/dev/null) || raw=""
        clean=$(salvage_review_json "$raw") || clean=""
    fi
    if [[ -n "$clean" ]]; then
        echo "$clean"
    elif [[ -z "$raw" ]]; then
        # Empty output = a genuine invocation failure (crash/auth) → error → FAIL.
        echo '{"reviewer":"agy","error":"agy invocation failed","findings":[],"verdict":"error"}'
    else
        # agy responded but salvage + retry could not extract JSON: it returned a
        # PROSE review. Do NOT manufacture an `error` here — that would discard a
        # genuine independent review and force a fail/re-run. Emit the raw prose so
        # the aggregator's parse_prose() classifies it `unparseable` (review exists
        # but isn't machine-structured), which oversight-evaluator routes to
        # CONDITIONAL_PROCEED — a human reads the preserved report. `unparseable`
        # (preserve) is the honest signal for "responded, not in JSON", not `error`
        # (crashed). Salvage + retry above still recover JSON whenever possible.
        printf '%s\n' "$raw"
    fi
}

# ── codex: adversarial security probe ───────────────────────────────────────
run_codex_review() {
    local lens="$1"

    local prompt="You are an adversarial security reviewer. BREAK this code. Do not approve it.

## Threat model
- Primary: registered building resident who wants to abuse other residents, view their data, or escalate privileges.
- Secondary: HOA admin at building A trying to access building B's data (multi-tenant isolation).
- External: unauthenticated attacker (credential stuffing, CSRF from malicious sites).

## Your task
${lens}

Probe for:
- Authorization bypasses and IDOR
- Multi-tenant isolation breaks (cross-org data access)
- Input validation gaps (boundary values, nulls, type coercion)
- Race conditions in concurrent booking scenarios
- Authentication bypass paths
- CSRF on state-changing endpoints
- Injection: SQL, template, shell
- TOTP replay or bypass

## Diff
\`\`\`diff
${DIFF_CONTENT}
\`\`\`

Return JSON only:
{
  \"reviewer\": \"codex\",
  \"lens\": \"security-adversarial\",
  \"findings\": [
    {
      \"severity\": \"critical|high|medium\",
      \"cwe\": \"CWE-XXX\",
      \"file\": \"path/to/file.py\",
      \"line\": 0,
      \"attack_scenario\": \"attacker does X and gains Y\",
      \"finding\": \"what is exploitable\",
      \"suggestion\": \"specific remediation\"
    }
  ],
  \"verdict\": \"approve|request_changes\",
  \"summary\": \"one paragraph\"
}"

    # codex reads the prompt on stdin and the subcommand is `codex exec` (HOS#199).
    # The old `codex --quiet` was an invalid invocation that ALWAYS failed; the
    # `2>/dev/null` then masked it as an empty `verdict:error`, so this path looked
    # like it ran a review and found nothing when codex was never actually invoked.
    # Match the working pattern in framework/validate_agents.sh: tmpfile + stdin.
    local tmpfile result clean rc=0
    tmpfile=$(mktemp "${TMPDIR:-/tmp}/second_review_codex_XXXXXX")
    printf '%s' "$prompt" > "$tmpfile"
    result=$(codex exec < "$tmpfile" 2>/dev/null) || rc=$?
    rm -f "$tmpfile"
    if [[ $rc -ne 0 || -z "$result" ]]; then
        echo '{"reviewer":"codex","error":"codex invocation failed","findings":[],"verdict":"error"}'
        return
    fi
    # Salvage the JSON in case codex wrapped it in prose/fences (HOS#113).
    clean=$(salvage_review_json "$result") || clean=""
    if [[ -n "$clean" ]]; then
        echo "$clean"
    else
        # codex responded but salvage failed → prose. Emit the raw prose so the
        # aggregator's parse_prose() classifies it `unparseable` (preserved for a
        # human), not `error`. Same reconciliation as the agy path above.
        printf '%s\n' "$result"
    fi
}

# ── Execute reviewers ────────────────────────────────────────────────────────
if $RUN_AGY && $AGY_AVAILABLE; then
    echo "Running agy (correctness + spec adherence)..."
    AGY_OUT=$(run_agy_review "correctness+spec" "")
    {
        echo "## agy — Correctness + Spec Adherence"
        echo '```json'
        echo "$AGY_OUT"
        echo '```'
        echo ""
    } >> "$OUTFILE"
    create_finding_issues "agy" "$AGY_OUT"
    log_context_advisory "agy" "$AGY_OUT"
    echo "  done"

elif $RUN_AGY && ! $AGY_AVAILABLE && $RUN_CODEX && $CODEX_AVAILABLE; then
    # Fallback: codex handles correctness lens since agy is unavailable
    echo "Running codex (FALLBACK correctness — agy unavailable)..."
    FALLBACK_OUT=$(run_codex_review \
        "agy is unavailable. Cover BOTH correctness + spec adherence AND adversarial security.")
    {
        echo "## codex — Correctness + Security (fallback: agy unavailable)"
        echo '```json'
        echo "$FALLBACK_OUT"
        echo '```'
        echo ""
    } >> "$OUTFILE"
    create_finding_issues "codex-fallback" "$FALLBACK_OUT"
    log_context_advisory "codex-fallback" "$FALLBACK_OUT"
    echo "  done (fallback)"
else
    [[ ! $AGY_AVAILABLE ]] && echo "  SKIP agy: not available"
    echo "## agy — SKIPPED" >> "$OUTFILE"
fi

if $RUN_CODEX && $CODEX_AVAILABLE && ! ( $RUN_AGY && ! $AGY_AVAILABLE ); then
    # Run codex security probe (not already run as fallback above)
    echo "Running codex (adversarial security probe — reserve)..."
    CODEX_OUT=$(run_codex_review "Adversarial security probe only.")
    {
        echo "## codex — Adversarial Security Probe (reserve)"
        echo '```json'
        echo "$CODEX_OUT"
        echo '```'
        echo ""
    } >> "$OUTFILE"
    create_finding_issues "codex" "$CODEX_OUT"
    log_context_advisory "codex" "$CODEX_OUT"
    echo "  done"
elif $RUN_CODEX && ! $CODEX_AVAILABLE; then
    echo "  SKIP codex: not available"
    echo "## codex — SKIPPED" >> "$OUTFILE"
fi

echo ""

# ── Finalize machine-readable verdict header ─────────────────────────────────
# Step 1 (SPEC-331): second_review_logic.py rewrites verdict, highest_severity,
# and unresolved_findings from the prose/JSON classifier. The module reads
# $OUTFILE and rewrites it in place.
python3 "$(dirname "$0")/oversight/second_review_logic.py" aggregate --file "$OUTFILE"

# Step 2 (SPEC-78 C1): validation_logic.py process rewrites new_blocking_count
# (and re-keys verdict) against the step-scoped convergence ledger. This is the
# only place that reads the ledger — imported from validation_logic.py, never
# reimplemented here (C1). A missing ledger is treated as empty (zero seen
# fingerprints), so first-run behavior is unchanged.
python3 "$(dirname "$0")/oversight/validation_logic.py" process \
    --file "$OUTFILE" --ledger "$LEDGER_FILE"

echo "Second review complete: $OUTFILE"
echo "Oversight-evaluator reads this before determining PROCEED/CONDITIONAL/ESCALATE."

# ── Token usage report ───────────────────────────────────────────────────────
TRACKER="$(dirname "$0")/oversight/token_tracker.py"
if [[ -f "$TRACKER" ]]; then
    # Record agy usage — estimate prompt size from source content (prompt is function-local in run_agy_review)
    if $RUN_AGY && [[ -n "${AGY_OUT:-}" ]]; then
        PROMPT_CHARS=$(( ${#DIFF_CONTENT} + ${#SPEC_CONTEXT} + ${#VALIDATOR_SUMMARY} + 800 ))
        OUT_CHARS=${#AGY_OUT}
        # Try to extract actual token counts from agy JSON output
        ACTUAL_IN=$(echo "${AGY_OUT:-}" | python3 -c \
            "import json,sys
d=json.load(sys.stdin)
print(d.get('usage',{}).get('input_tokens',d.get('usage',{}).get('prompt_tokens',0)))" 2>/dev/null || echo "0")
        ACTUAL_OUT=$(echo "${AGY_OUT:-}" | python3 -c \
            "import json,sys
d=json.load(sys.stdin)
print(d.get('usage',{}).get('output_tokens',d.get('usage',{}).get('completion_tokens',0)))" 2>/dev/null || echo "0")
        python3 "$TRACKER" record --vendor agy --stage second-review \
            --step "${STEP:-?}" --prompt-chars "$PROMPT_CHARS" --output-chars "$OUT_CHARS" \
            --actual-prompt-tokens "$ACTUAL_IN" --actual-output-tokens "$ACTUAL_OUT" 2>/dev/null || true
    fi

    # Record codex usage — tag fallback mode separately for telemetry fidelity
    # Fallback (agy unavailable) uses stage "second-review-fallback" so reports
    # distinguish a targeted security probe from a combined correctness+security pass.
    if [[ -n "${FALLBACK_OUT:-}" ]]; then
        _CODEX_ACTUAL="$FALLBACK_OUT"
        _CODEX_STAGE="second-review-fallback"
    elif [[ -n "${CODEX_OUT:-}" ]]; then
        _CODEX_ACTUAL="$CODEX_OUT"
        _CODEX_STAGE="second-review"
    else
        _CODEX_ACTUAL=""
        _CODEX_STAGE="second-review"
    fi
    if $RUN_CODEX && [[ -n "$_CODEX_ACTUAL" ]]; then
        CODEX_PROMPT_CHARS=$(( ${#DIFF_CONTENT} + 600 ))
        CODEX_OUT_CHARS=${#_CODEX_ACTUAL}
        python3 "$TRACKER" record --vendor codex --stage "$_CODEX_STAGE" \
            --step "${STEP:-?}" --prompt-chars "$CODEX_PROMPT_CHARS" --output-chars "$CODEX_OUT_CHARS" \
            2>/dev/null || true
    fi

    echo ""
    python3 "$TRACKER" report 2>/dev/null || true
fi

# ── Fail closed on a runtime reviewer error ──────────────────────────────────
# A fired-and-required reviewer that errored at runtime makes the aggregate
# verdict `error`. Exit non-zero so the pipeline does not proceed on a review
# that never produced an independent judgment — symmetric with the
# vendor-unavailable-at-pre-check guard. The evaluator independently treats a
# MEDIUM+ `verdict: error` file as COMPLIANCE FAIL, but failing here too means a
# transient agy/codex crash blocks the pipeline at the source rather than
# relying solely on the downstream reader.
FINAL_VERDICT=$(grep -m1 '^verdict:' "$OUTFILE" | awk '{print $2}')
if [[ "$FINAL_VERDICT" == "error" ]]; then
    echo "run_second_review: FAIL-CLOSED — a required reviewer errored at runtime (verdict=error)." >&2
    echo "  The mandatory cross-vendor review did not produce an independent judgment. Re-run." >&2
    exit 1
fi
if [[ "$FINAL_VERDICT" == "unparseable" ]]; then
    # The reviewer produced a real review we could not auto-structure (agy returned
    # a markdown report, not JSON — HOS#113). This is NOT a crash: the content is
    # in $OUTFILE. Do not silently fail-closed-as-error, and do not silently pass —
    # surface it for a human to read and disposition.
    echo "run_second_review: ⚠ UNPARSEABLE — a reviewer returned prose, not structured JSON." >&2
    echo "  The independent review DID run and its content is preserved in:" >&2
    echo "    $OUTFILE" >&2
    echo "  A human must read it and decide. (This is distinct from verdict=error/crash.)" >&2
    # Exit 0: the review exists and is recorded. The oversight-evaluator routes an
    # 'unparseable' second-review to human review (CONDITIONAL/ESCALATE), not a
    # silent PASS and not a COMPLIANCE FAIL.
fi
