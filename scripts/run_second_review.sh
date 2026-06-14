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

# ── agy: correctness + spec adherence ───────────────────────────────────────
run_agy_review() {
    local lens="$1"
    local extra_instructions="$2"

    local prompt="You are an independent code reviewer. Your lens is CORRECTNESS and SPEC ADHERENCE.

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

    agy -p "$prompt" 2>/dev/null || \
        echo '{"reviewer":"agy","error":"agy invocation failed","findings":[],"verdict":"error"}'
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

    echo "$prompt" | codex --quiet 2>/dev/null || \
        echo '{"reviewer":"codex","error":"codex invocation failed","findings":[],"verdict":"error"}'
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

# Extract JSON blocks (fenced with ```json ... ```)
blocks = re.findall(r'```json\s*(\{.*?\})\s*```', content, re.DOTALL)
severities = ["critical", "high", "medium", "low", "none"]
highest = "none"
request_changes = False
had_error = False
finding_count = 0

for block in blocks:
    try:
        data = json.loads(block)
    except Exception:
        # An unparseable reviewer block is itself a review failure — fail closed.
        had_error = True
        continue
    if data.get("verdict") == "error" or data.get("error"):
        had_error = True
    if data.get("verdict") == "request_changes":
        request_changes = True
    for f in data.get("findings", []):
        sev = str(f.get("severity", "low")).lower()
        if severities.index(sev) < severities.index(highest):
            highest = sev
        if sev in ("critical", "high"):
            finding_count += 1

# Precedence: error > request_changes > approve. A runtime reviewer error
# (timeout, rate-limit, crash after the CLI passed pre-check) must NOT collapse
# into `approve` — that would silently convert the mandatory independent review
# into a PASS. The error-block is swallowed into `approve` only if we let it;
# instead a fired reviewer that errored makes the aggregate `error`, and the
# script exits non-zero so the pipeline fails closed (symmetric with the
# unavailable-at-pre-check guard above). The oversight-evaluator also treats a
# MEDIUM+ second-review file with verdict:error as COMPLIANCE FAIL.
if not blocks or had_error:
    verdict = "error"
elif request_changes:
    verdict = "request_changes"
else:
    verdict = "approve"

# Rewrite the top-level verdict fields in the file
new_content = re.sub(r'^verdict: pending$', f'verdict: {verdict}', content, flags=re.M)
new_content = re.sub(r'^highest_severity: none$', f'highest_severity: {highest}', new_content, flags=re.M)
new_content = re.sub(r'^unresolved_findings: 0$', f'unresolved_findings: {finding_count}', new_content, flags=re.M)
open(path, 'w').write(new_content)
print(f"  verdict={verdict} highest_severity={highest} unresolved={finding_count}")
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
