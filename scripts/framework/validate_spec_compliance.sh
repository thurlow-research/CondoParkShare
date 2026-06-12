#!/usr/bin/env bash
# validate_spec_compliance.sh — checks that the agent pipeline implementation satisfies
# the governance requirements defined in METHODOLOGY.md, AGENTS.md, and decisions.md.
#
# This is the "system test" for the agent pipeline itself — equivalent to what
# unit-test/system-test do for application code. It asks:
#   "Does the agent pipeline, as implemented in these files, actually satisfy
#    the governance requirements it claims to implement?"
#
# It also checks decisions.md: for each recorded design decision, verifies the
# implementation matches the stated intent.
#
# Phase 4 of the framework validation suite:
#   Phase 1 — check_agents_static.sh    structural checks, no AI
#   Phase 2 — validate_agents.sh        semantic review (loops, contradictions)
#   Phase 3 — validate_docs.sh          documentation coverage (omissions)
#   Phase 4 — validate_spec_compliance  governance requirements + decisions
#
# Usage:
#   ./scripts/framework/validate_spec_compliance.sh
#   ./scripts/framework/validate_spec_compliance.sh --skip-codex
#
# Output: .claudetmp/framework/spec-compliance-YYYYMMDDTHHMMSS.md
#
# Exit codes:
#   0 — compliant
#   1 — compliance failures found
#   2 — CLI unavailable or usage error

set -euo pipefail

AGENTS_DIR=".claude/agents"
OUT_DIR=".claudetmp/framework"
SKIP_CODEX=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --agents-dir) AGENTS_DIR="$2"; shift 2 ;;
        --skip-codex) SKIP_CODEX=true;  shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

# Load project config
PROJECT_NAME="(unnamed project)"
[[ -f "scripts/framework/config.sh" ]] && source scripts/framework/config.sh

AGY_AVAILABLE=false
command -v agy &>/dev/null && AGY_AVAILABLE=true || true

if ! $AGY_AVAILABLE; then
    echo "ERROR: agy not available — spec compliance check requires AI review" >&2
    exit 2
fi

mkdir -p "$OUT_DIR"
TIMESTAMP=$(date +%Y%m%dT%H%M%S)
OUTFILE="$OUT_DIR/spec-compliance-${TIMESTAMP}.md"

echo "=== Spec compliance check: ${TIMESTAMP} ==="
echo "  Project: $PROJECT_NAME"
echo "  Output:  $OUTFILE"
echo ""

# ── Collect governance spec files ────────────────────────────────────────────
METHODOLOGY=""
ROOT_AGENTS_PROTOCOL=""
DECISIONS=""

[[ -f "METHODOLOGY.md" ]] && METHODOLOGY=$(cat "METHODOLOGY.md")
[[ -f "AGENTS.md"      ]] && ROOT_AGENTS_PROTOCOL=$(cat "AGENTS.md")
[[ -f "scripts/framework/decisions.md" ]] && DECISIONS=$(cat "scripts/framework/decisions.md")

# ── Collect agent files ──────────────────────────────────────────────────────
AGENT_CONTENT=""
AGENT_COUNT=0
while IFS= read -r -d '' f; do
    name=$(grep -m1 '^name:' "$f" | sed 's/^name:[[:space:]]*//' | tr -d '[:space:]')
    [[ -z "$name" ]] && continue
    AGENT_CONTENT+="=== AGENT: $name ($f) ===
$(cat "$f")

"
    AGENT_COUNT=$(( AGENT_COUNT + 1 ))
done < <(find "$AGENTS_DIR" -name '*.md' -print0)

# ── Collect relevant scripts ──────────────────────────────────────────────────
SCRIPT_CONTENT=""
for s in \
    "scripts/run_second_review.sh" \
    "scripts/run_panel.sh" \
    "scripts/framework/validate_agents.sh" \
    "scripts/framework/validate_docs.sh"
do
    [[ -f "$s" ]] && SCRIPT_CONTENT+="=== SCRIPT: $s ===
$(cat "$s")

"
done

echo "Checking $AGENT_COUNT agents against governance spec..."
echo ""

# ── agy: governance requirements compliance ───────────────────────────────────
run_agy_compliance() {
    local prompt
    prompt="You are checking whether an AI agent pipeline implementation complies with its
own governance requirements. The project is: ${PROJECT_NAME}.

You have three sources of requirements:
1. METHODOLOGY.md — the conceptual governance spec
2. AGENTS.md (root protocol) — mandatory behaviors for the authoring AI
3. decisions.md — design decisions recorded during development sessions

You have two sources of implementation:
4. Agent definition files (.claude/agents/*.md)
5. Framework and oversight scripts

Your task: for each governance requirement, find the agent(s) or script(s) responsible
for implementing it, then check whether the implementation actually satisfies the requirement.

This is different from checking consistency between files. You are checking:
  \"The governance spec says X must happen. Does the implementation actually do X?\"

## Governance Requirements Source

### METHODOLOGY.md
${METHODOLOGY}

### Root AGENTS.md Protocol (mandatory behaviors)
${ROOT_AGENTS_PROTOCOL}

### decisions.md (recorded design decisions)
${DECISIONS}

## Implementation

### Agent files
${AGENT_CONTENT}

### Scripts
${SCRIPT_CONTENT}

## What to check

**Constraint REQ-001: Cross-vendor independence**
METHODOLOGY.md states: \"no Claude model casts an independent review.\" agy (Gemini) and codex (OpenAI) are the independent reviewers. Claude Sonnet is the arbiter only.
Check: Do validate_agents.sh, validate_docs.sh, and validate_spec_compliance.sh send their review prompts to agy/codex, NOT to a Claude CLI? Does any agent file assign a Claude model to an independent reviewer role?

**Constraint REQ-002: Risk-tiered escalation thresholds**
METHODOLOGY.md states: MEDIUM+ requires agy; HIGH+ requires codex.
Check: Does run_second_review.sh implement threshold-gated firing (agy at ≥0.30, codex at ≥0.55)? Does it fail-closed when a required reviewer is unavailable?

**Constraint REQ-003: Human gate at HIGH/CRITICAL**
METHODOLOGY.md states: human review is mandatory at HIGH/CRITICAL.
Check: Do relevant agent files (oversight-evaluator, oversight-orchestrator) specify a human gate for CRITICAL steps? Does the step-manifest reference path exist?

**Constraint REQ-004: Model tier assignments match roles**
METHODOLOGY.md states: Author = Opus; Arbiter = Sonnet; Independent = agy/codex.
docs/AGENTS.md states: architect and technical-design use Opus; all reviewer/test agents use Sonnet.
Check: Do agent files use the correct model tier for their role? Is any reviewer using Haiku (insufficient for judgment calls)?

**Constraint REQ-005: Loop exit conditions on all iterative agents**
METHODOLOGY.md and agent design require every iteration loop to have a defined exit.
docs/AGENTS.md says the pattern is: 5 rounds without approval → escalate to architect/human.
Check: Does every agent that iterates (coder↔reviewers, technical-design↔architect) have an explicit loop exit condition?

**Constraint REQ-006: Self-flagging mandatory behaviors**
AGENTS.md (root protocol) defines 5 mandatory behaviors: risk classification, Human Review Required section, confidence declaration, hallucination surface warning, blast radius assessment.
Check: Does the coder agent explicitly require all 5 behaviors? Do any agents explicitly waive any of these requirements?

**Constraint REQ-007: Design decisions implemented as recorded**
decisions.md records architectural decisions with verification criteria.
Check: For each decision in decisions.md, verify the \"Verification\" criterion is satisfied in the named implementation files.

**Constraint REQ-008: Temp state lifecycle**
Agents that write temp state (.claudetmp/reviews/*) must specify read conditions, staleness rules, and cleanup conditions.
Check: Do all agents that write temp state specify all three lifecycle phases?

Return JSON only:
{
  \"reviewer\": \"agy\",
  \"lens\": \"spec-compliance\",
  \"failures\": [
    {
      \"severity\": \"blocking|warning\",
      \"requirement\": \"REQ-NNN: [name]\",
      \"files_checked\": [\"list of files\"],
      \"expected\": \"what the governance spec requires\",
      \"actual\": \"what the implementation actually does\",
      \"gap\": \"specific description of the non-compliance\",
      \"fix\": \"what to change to achieve compliance\"
    }
  ],
  \"decision_gaps\": [
    {
      \"decision_id\": \"DEC-NNN\",
      \"verification_criterion\": \"the criterion from decisions.md\",
      \"status\": \"satisfied|gap|not_checkable\",
      \"finding\": \"what was found (only needed if gap or not_checkable)\"
    }
  ],
  \"verdict\": \"compliant|non_compliant\",
  \"summary\": \"one paragraph overall assessment\"
}"

    local tmpfile
    tmpfile=$(mktemp .claudetmp/validate_compliance_agy.XXXXXX)
    printf '%s' "$prompt" > "$tmpfile"
    local result
    result=$(agy -p "$(cat "$tmpfile")" 2>/dev/null) || \
        result='{"reviewer":"agy","error":"agy invocation failed","failures":[],"verdict":"error","summary":"agy failed"}'
    rm -f "$tmpfile"
    echo "$result"
}

# ── codex: adversarial compliance probe ──────────────────────────────────────
run_codex_compliance() {
    local prompt
    prompt="You are adversarially checking whether an AI agent pipeline ACTUALLY implements
its claimed governance requirements. Project: ${PROJECT_NAME}.

Your goal: find places where the pipeline claims to implement a governance control
but actually has a gap that would allow the control to be bypassed or weakened.

## Governance spec
${METHODOLOGY}

${ROOT_AGENTS_PROTOCOL}

## Decisions
${DECISIONS}

## Implementation (agent files + scripts)
${AGENT_CONTENT}

${SCRIPT_CONTENT}

## Attack vectors for compliance gaps

1. CROSS-VENDOR BYPASS: Is there any code path where a Claude model could end up in
   the 'independent reviewer' seat? (e.g., fallback logic that calls claude when agy fails)

2. THRESHOLD MANIPULATION: Can the risk score threshold be effectively lowered by how
   risk is reported, allowing MEDIUM+ changes to skip the agy review?

3. HUMAN GATE BYPASS: Is there a path from code change to merged PR that avoids the
   human gate for CRITICAL changes?

4. LOOP EXIT MANIPULATION: Can an agent avoid the 5-round escalation by resetting the
   counter, or by routing to a sub-agent that doesn't track rounds?

5. DECISION DRIFT: For each decision in decisions.md with a 'pending' status, is there
   evidence in the agent files that it was actually implemented? Or is it claimed as
   'implemented' but the implementation files don't match?

6. MANDATORY BEHAVIOR WAIVER: Is there any agent instruction that would cause the
   authoring AI to omit a mandatory behavior (risk flag, confidence declaration, etc.)?

7. TEMP STATE STALENESS: If an agent reads temp state older than its defined staleness
   threshold, does it use stale data without detecting it?

Return JSON:
{
  \"reviewer\": \"codex\",
  \"lens\": \"compliance-adversarial\",
  \"bypass_vectors\": [
    {
      \"severity\": \"critical|high|medium\",
      \"control\": \"which governance control is bypassable\",
      \"vector\": \"specific scenario that bypasses the control\",
      \"evidence\": \"what in the implementation enables this\",
      \"fix\": \"specific change to close the bypass\"
    }
  ],
  \"verdict\": \"compliant|non_compliant\",
  \"summary\": \"one paragraph\"
}"

    local tmpfile
    tmpfile=$(mktemp .claudetmp/validate_compliance_codex.XXXXXX)
    printf '%s' "$prompt" > "$tmpfile"
    local result
    result=$(codex --quiet < "$tmpfile" 2>/dev/null) || \
        result='{"reviewer":"codex","error":"codex invocation failed","bypass_vectors":[],"verdict":"error","summary":"codex failed"}'
    rm -f "$tmpfile"
    echo "$result"
}

# ── Execute and write output ─────────────────────────────────────────────────
{
    printf "# Spec Compliance Validation\n"
    printf "Timestamp: %s  Project: %s\n" "$TIMESTAMP" "$PROJECT_NAME"
    printf "verdict: pending\n"
    printf "highest_severity: none\n"
    printf "blocking_count: 0\n\n"
} > "$OUTFILE"

echo "Running agy (governance requirements compliance)..."
AGY_OUT=$(run_agy_compliance)
{
    echo "## agy — Governance Requirements"
    echo '```json'
    echo "$AGY_OUT"
    echo '```'
    echo ""
} >> "$OUTFILE"
echo "  done"

if ! $SKIP_CODEX && command -v codex &>/dev/null; then
    echo "Running codex (adversarial compliance probe)..."
    CODEX_OUT=$(run_codex_compliance)
    {
        echo "## codex — Bypass Vector Analysis"
        echo '```json'
        echo "$CODEX_OUT"
        echo '```'
        echo ""
    } >> "$OUTFILE"
    echo "  done"
else
    echo "## codex — SKIPPED" >> "$OUTFILE"
fi

echo ""

# ── Finalize verdict ─────────────────────────────────────────────────────────
python3 - "$OUTFILE" <<'PYEOF'
import json, re, sys
path = sys.argv[1]
try:
    content = open(path).read()
except Exception:
    sys.exit(0)

blocks = re.findall(r'```json\s*(\{.*?\})\s*```', content, re.DOTALL)
severities = ["critical", "high", "blocking", "warning", "medium", "low", "none"]
highest = "none"
non_compliant = False
blocking_count = 0

for block in blocks:
    try:
        data = json.loads(block)
    except Exception:
        continue
    if data.get("verdict") in ("non_compliant", "error"):
        non_compliant = True
    for f in data.get("failures", []) + data.get("bypass_vectors", []):
        sev = str(f.get("severity", "low")).lower()
        try:
            if severities.index(sev) < severities.index(highest):
                highest = sev
        except ValueError:
            pass
        if sev in ("critical", "high", "blocking"):
            blocking_count += 1

verdict = "non_compliant" if non_compliant else "compliant"
if not blocks:
    verdict = "error"

new_content = re.sub(r'^verdict: pending$',      f'verdict: {verdict}',          content, flags=re.M)
new_content = re.sub(r'^highest_severity: none$', f'highest_severity: {highest}', new_content, flags=re.M)
new_content = re.sub(r'^blocking_count: 0$',      f'blocking_count: {blocking_count}', new_content, flags=re.M)
open(path, 'w').write(new_content)
print(f"  verdict={verdict} highest_severity={highest} blocking={blocking_count}")
PYEOF

echo ""
echo "Output: $OUTFILE"
echo ""

VERDICT=$(grep '^verdict:' "$OUTFILE" | head -1 | awk '{print $2}')
BLOCKING=$(grep '^blocking_count:' "$OUTFILE" | head -1 | awk '{print $2}')

if [[ "$VERDICT" == "compliant" ]]; then
    echo "════════════════════════════════════════════"
    echo "  PASS — pipeline compliant with governance spec"
    echo "════════════════════════════════════════════"
    exit 0
else
    echo "════════════════════════════════════════════"
    echo "  FAIL — verdict=${VERDICT} blocking=${BLOCKING:-?}"
    echo "  Invoke spec-compliance-validator agent to triage."
    echo "  Output: $OUTFILE"
    echo "════════════════════════════════════════════"
    exit 1
fi
