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
# Usage:
#   ./scripts/run_second_review.sh --step 3 --score 0.67
#   ./scripts/run_second_review.sh --diff HEAD~1 --score 0.45
#   ./scripts/run_second_review.sh --files a.py b.py --score 0.71
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

while [[ $# -gt 0 ]]; do
    case "$1" in
        --score)   SCORE="$2";    shift 2 ;;
        --tier)    TIER="$2";     shift 2 ;;
        --step)    STEP="$2";     shift 2 ;;
        --diff)    DIFF_REF="$2"; shift 2 ;;
        --files)   shift; while [[ $# -gt 0 && "$1" != --* ]]; do FILES+=("$1"); shift; done ;;
        *)         shift ;;
    esac
done

if [[ -z "$STEP" ]]; then
    echo "Error: --step <N> is required (used to name output and match oversight-evaluator lookup)" >&2
    exit 1
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

# Normalize tier to upper for comparison.
TIER_UC=$(printf '%s' "$TIER" | tr '[:lower:]' '[:upper:]')

# agy is mandatory at MEDIUM+ (tier) or score ≥ AGY_THRESHOLD.
case "$TIER_UC" in MEDIUM|HIGH|CRITICAL) RUN_AGY=true ;; esac
python3 -c "
s=float('$SCORE'); t=float('$AGY_THRESHOLD')
exit(0 if s >= t else 1)" 2>/dev/null && RUN_AGY=true || true

# codex is mandatory at HIGH+ (tier) or score ≥ CODEX_THRESHOLD.
case "$TIER_UC" in HIGH|CRITICAL) RUN_CODEX=true ;; esac
python3 -c "
s=float('$SCORE'); t=float('$CODEX_THRESHOLD')
exit(0 if s >= t else 1)" 2>/dev/null && RUN_CODEX=true || true

if ! $RUN_AGY && ! $RUN_CODEX; then
    echo "run_second_review: score=$SCORE below both thresholds (agy≥$AGY_THRESHOLD, codex≥$CODEX_THRESHOLD) and tier=${TIER:-none} below MEDIUM — skip"
    # Write a sentinel so oversight-evaluator can distinguish "skipped" from "missing"
    mkdir -p ".claudetmp/second-review"
    TS=$(date +%Y%m%dT%H%M%S)
    cat > ".claudetmp/second-review/step${STEP}-${TS}.md" <<EOF
# Second Review — Step ${STEP}
Timestamp: ${TS}
verdict: skipped
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

# --- Build diff content ---
if [[ -n "$DIFF_REF" ]]; then
    DIFF_CONTENT=$(git diff "$DIFF_REF" 2>/dev/null || echo "")
elif [[ ${#FILES[@]} -gt 0 ]]; then
    DIFF_CONTENT=$(git diff HEAD -- "${FILES[@]}" 2>/dev/null || cat "${FILES[@]}" 2>/dev/null || echo "")
else
    DIFF_CONTENT=$(git diff HEAD 2>/dev/null || echo "")
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
reason: no diff content detected
EOF
    exit 0
fi

SPEC_CONTEXT=""
[[ -f "Specs/SPEC-1-pilot.md" ]] && SPEC_CONTEXT=$(cat Specs/SPEC-1-pilot.md)

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
    printf "highest_severity: none\n"
    printf "unresolved_findings: 0\n"
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

# ── agy: correctness + spec adherence ───────────────────────────────────────
run_agy_review() {
    local lens="$1"
    local extra_instructions="$2"

    local prompt="You are an independent code reviewer. Your lens is CORRECTNESS and SPEC ADHERENCE.

IMPORTANT — this is a READ-ONLY review. Base your review ONLY on the diff and context provided below. Do NOT run shell commands, execute tests, or create/modify any files. Output your review directly; do not narrate tool use.

## Application context
Django (Python) + HTMX application — CondoParkShare, a parking spot sharing system for condo residents. Multi-tenant (one Django instance, multiple buildings). Uses PostgreSQL with tstzrange GiST exclusion constraints for booking overlap safety.

## Your task
${extra_instructions}

Review this diff for:
1. Logic errors, off-by-one errors, incorrect conditions, missing edge cases
2. Spec adherence gaps — requirements that appear unimplemented or wrong
3. Django-specific risks: race conditions (missing select_for_update), N+1 queries, cross-tenant data leaks
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
- CSRF on state-changing HTMX endpoints
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
    echo "  done"
elif $RUN_CODEX && ! $CODEX_AVAILABLE; then
    echo "  SKIP codex: not available"
    echo "## codex — SKIPPED" >> "$OUTFILE"
fi

echo ""

# ── Finalize machine-readable verdict header ─────────────────────────────────
# Parse all reviewer JSON blocks to determine aggregate verdict and severity.
python3 - "$OUTFILE" <<'PYEOF'
import json, re, sys
path = sys.argv[1]
try:
    content = open(path).read()
except Exception:
    sys.exit(0)

severities = ["critical", "high", "medium", "low", "none"]
SEV_RANK = {s: i for i, s in enumerate(severities)}

# Parse by REVIEWER SECTION (## header), not by a bare ```json regex. agy is an
# agentic CLI: it returns a narrated transcript + a markdown report, not the
# strict JSON the prompt requested (HOS#113). The old parser required a fenced
# block starting with `{`, found none, and fail-closed every prose review as
# `error` — throwing away a genuine independent review. Section parsing also
# survives the reviewer emitting its own ``` code fences.
sections = re.split(r'(?m)^## ', content)[1:]   # each starts after "## "

def fenced_body(text):
    """Content inside the outer ```json ... ``` if present, else the whole text."""
    m = re.search(r'```(?:json)?\s*\n(.*)\n```', text, re.DOTALL)
    return (m.group(1) if m else text).strip()

def classify_prose(text):
    """Best-effort verdict/severity from a non-JSON markdown review report.
    Returns (verdict, severity): verdict in approve|request_changes|unparseable."""
    low = text.lower()
    risk = re.search(r'\brisk:\s*(critical|high|medium|low|none)\b', low)
    blocking = re.search(r'must[ -]?fix|tier\s*1\b|request[_ ]changes|\bblocking\b|\bcritical\b', low)
    approve = re.search(r'\bverdict:\s*approve\b|no (issues|findings|problems)|lgtm|looks good|\bapprove\b', low)
    if risk and risk.group(1) in ("critical", "high"):
        return "request_changes", risk.group(1)
    if blocking:
        sev = "critical" if "critical" in low else "high"
        return "request_changes", sev
    if approve or (risk and risk.group(1) in ("low", "none")):
        return "approve", (risk.group(1) if risk else "none")
    return "unparseable", (risk.group(1) if risk else "none")

reviewers = []   # (name, verdict, severity, finding_count, parsed_from)
for sec in sections:
    head = sec.splitlines()[0] if sec.splitlines() else ""
    hl = head.lower()
    name = "agy" if hl.startswith("agy") else ("codex" if hl.startswith("codex") else None)
    if name is None:
        continue                      # not a reviewer section (verdict header etc.)
    if "skipped" in hl:
        continue                      # a skipped reviewer is handled by the pre-check
    body = fenced_body(sec[len(head):])
    if not body:
        reviewers.append((name, "error", "none", 0, "empty"))   # true crash / no output
        continue
    # Structured path: the body is valid JSON exactly as the prompt asked.
    try:
        data = json.loads(body)
    except Exception:
        v, sev = classify_prose(body)
        fc = len(re.findall(r'(?m)^\s*#{1,4}\s', body)) if v == "request_changes" else 0
        reviewers.append((name, v, sev, fc, "prose"))
        continue
    if data.get("verdict") == "error" or data.get("error"):
        reviewers.append((name, "error", "none", 0, "json"))
        continue
    v = "request_changes" if data.get("verdict") == "request_changes" else "approve"
    sev, fc = "none", 0
    for f in data.get("findings", []):
        s = str(f.get("severity", "low")).lower()
        if SEV_RANK.get(s, 4) < SEV_RANK[sev]:
            sev = s
        if s in ("critical", "high"):
            fc += 1
    reviewers.append((name, v, sev, fc, "json"))

# Aggregate. Precedence: error > request_changes > unparseable > approve.
#   error        — a fired reviewer genuinely crashed / produced no output.
#                  Fails closed (must not silently become a PASS).
#   request_changes — at least one reviewer flagged blocking issues.
#   unparseable  — a reviewer produced a review we could not structure. This is
#                  DISTINCT from error: the review content exists and is preserved
#                  in this file for a human to read. It must NOT silently pass and
#                  must NOT be misread as the reviewer crashing.
#   approve      — all fired reviewers approved.
if not reviewers:
    verdict, highest, finding_count = "error", "none", 0
else:
    highest = "none"
    finding_count = 0
    for _, _, sev, fc, _ in reviewers:
        if SEV_RANK.get(sev, 4) < SEV_RANK[highest]:
            highest = sev
        finding_count += fc
    verds = [v for _, v, _, _, _ in reviewers]
    if "error" in verds:
        verdict = "error"
    elif "request_changes" in verds:
        verdict = "request_changes"
    elif "unparseable" in verds:
        verdict = "unparseable"
    else:
        verdict = "approve"

new_content = re.sub(r'^verdict: pending$', f'verdict: {verdict}', content, flags=re.M)
new_content = re.sub(r'^highest_severity: none$', f'highest_severity: {highest}', new_content, flags=re.M)
new_content = re.sub(r'^unresolved_findings: 0$', f'unresolved_findings: {finding_count}', new_content, flags=re.M)
open(path, 'w').write(new_content)
prose_note = " (parsed from prose — agy returned a markdown report, not JSON)" \
    if any(pf == "prose" for *_ , pf in reviewers) else ""
print(f"  verdict={verdict} highest_severity={highest} unresolved={finding_count}{prose_note}")
PYEOF

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
