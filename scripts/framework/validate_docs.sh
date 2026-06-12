#!/usr/bin/env bash
# validate_docs.sh — AI-powered documentation coverage validator.
#
# Checks that documentation accurately and COMPLETELY describes agent behavior.
# Catches the "omission" class of bug: a doc that mentions an agent but only
# covers a subset of its modes, roles, or escalation paths.
#
# This is the third phase of the framework validation suite:
#   Phase 1 — check_agents_static.sh    structural checks, no AI
#   Phase 2 — validate_agents.sh        semantic review (loops, contradictions)
#   Phase 3 — validate_docs.sh          documentation coverage (omissions, staleness)
#
# The authoritative source for each agent's behavior is its agent file.
# Every doc reference to an agent is checked against that source.
#
# Usage:
#   ./scripts/framework/validate_docs.sh
#   ./scripts/framework/validate_docs.sh --agents-dir .claude/agents
#   ./scripts/framework/validate_docs.sh --skip-codex   # agy only (faster)
#
# Output: .claudetmp/framework/doc-validation-YYYYMMDDTHHMMSS.md
#
# Exit codes:
#   0 — clean (no omissions or stale claims found)
#   1 — findings that require attention
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

# Load project-specific config if present
PROJECT_NAME="(unnamed project)"
[[ -f "scripts/framework/config.sh" ]] && source scripts/framework/config.sh

# ── Check CLI availability ───────────────────────────────────────────────────
AGY_AVAILABLE=false
CODEX_AVAILABLE=false
command -v agy   &>/dev/null && AGY_AVAILABLE=true  || true
command -v codex &>/dev/null && CODEX_AVAILABLE=true || true

if ! $AGY_AVAILABLE && (! $CODEX_AVAILABLE || $SKIP_CODEX); then
    echo "ERROR: agy not available — doc validation requires AI review" >&2
    exit 2
fi

mkdir -p "$OUT_DIR"
TIMESTAMP=$(date +%Y%m%dT%H%M%S)
OUTFILE="$OUT_DIR/doc-validation-${TIMESTAMP}.md"

echo "=== Documentation validation: ${TIMESTAMP} ==="
echo "  Project: $PROJECT_NAME"
echo "  Output:  $OUTFILE"
echo ""

# ── Collect agent files ──────────────────────────────────────────────────────
AGENT_CONTENT=""
AGENT_COUNT=0
while IFS= read -r -d '' f; do
    name=$(grep -m1 '^name:' "$f" | sed 's/^name:[[:space:]]*//' | tr -d '[:space:]')
    [[ -z "$name" ]] && continue
    AGENT_CONTENT+="=== AGENT FILE: $f (name: $name) ===
$(cat "$f")

"
    AGENT_COUNT=$(( AGENT_COUNT + 1 ))
done < <(find "$AGENTS_DIR" -name '*.md' -print0)

# ── Collect documentation files ──────────────────────────────────────────────
DOC_CONTENT=""
DOC_COUNT=0
for doc in \
    "docs/AGENTS.md" \
    "docs/OVERSIGHT-RUNBOOK.md" \
    "docs/SETUP.md" \
    "docs/CUSTOMIZATION.md"
do
    [[ -f "$doc" ]] || continue
    DOC_CONTENT+="=== DOCUMENTATION FILE: $doc ===
$(cat "$doc")

"
    DOC_COUNT=$(( DOC_COUNT + 1 ))
done

# ── Load known bug patterns ──────────────────────────────────────────────────
KNOWN_PATTERNS=""
PATTERNS_FILE="scripts/framework/doc-patterns.md"
if [[ -f "$PATTERNS_FILE" ]]; then
    KNOWN_PATTERNS=$(cat "$PATTERNS_FILE")
    echo "  Loaded known patterns from $PATTERNS_FILE"
fi

# Load decisions (the Verification criteria double as doc correctness checks)
DECISIONS_CONTENT=""
DECISIONS_FILE="scripts/framework/decisions.md"
if [[ -f "$DECISIONS_FILE" ]]; then
    DECISIONS_CONTENT=$(cat "$DECISIONS_FILE")
    echo "  Loaded decisions from $DECISIONS_FILE"
fi

echo "Checking $AGENT_COUNT agent files against $DOC_COUNT documentation files..."
echo ""

# ── agy: documentation coverage review ──────────────────────────────────────
run_agy_doc_review() {
    local prompt
    prompt="You are validating documentation coverage for an AI agent pipeline framework (project: ${PROJECT_NAME}).

Your task: verify that documentation accurately and COMPLETELY describes each agent's behavior. Find OMISSIONS — places where a doc mentions an agent but only covers a subset of its modes, roles, or capabilities.

This is NOT about contradictions (agent A says X, doc says not-X). It is about coverage (agent file says X and Y, doc only mentions X). The doc is correct as far as it goes; it just silently omits part of the picture.

## Authoritative sources (agent files)
These are the truth. Every claim in the docs should be checkable against these.

${AGENT_CONTENT}

## Documentation files to audit
These are what you are checking. Find where they describe agents incompletely.

${DOC_CONTENT}

## Known bug patterns (check for recurrences of these specifically)

These patterns were discovered in previous sessions and recorded to prevent recurrence:

${KNOWN_PATTERNS}

## Design decisions (the Verification criteria double as doc correctness checks)

For each decision below, the Verification criterion describes what the documentation should say.
Check that the named documentation files satisfy those criteria.

${DECISIONS_CONTENT}

## What to look for (general)

1. MODE OMISSIONS: Agent has multiple operating modes (e.g. proactive project-start + reactive during-build), but a doc only describes one mode. Flag every doc location that describes the agent in a mode-specific way without mentioning the other mode(s).

2. PIPELINE POSITION OMISSIONS: Agent appears in the pipeline at project-start AND as a per-step reviewer, but the pipeline overview or project-start sequence in docs omits one of those positions.

3. STALE DESCRIPTIONS: A doc describes behavior (an escalation target, an output file path, a tool capability) that the current agent file no longer supports.

4. DESCRIPTION FIELD INCOMPLETENESS: The agent's 'description:' frontmatter field omits a significant invocation context (e.g. says 'invoked reactively' when the agent also has a mandatory project-start role).

5. ROLE BOUNDARY DRIFT: A doc says an agent can/cannot do something that contradicts the agent file (e.g. doc says 'escalates to human' for a decision that the agent file routes to another agent).

## How to report findings

Be specific: name the exact file, the approximate line content (not line number — quote the phrase), and what the agent file says that contradicts or completes it.

Only flag documentation files — agent files are the source of truth and are not flagged.
Only flag descriptions of an agent's role (not passing mentions). A doc saying 'pm-agent answers product questions' in a list does not need to enumerate all pm-agent's modes.

Return JSON only:
{
  \"reviewer\": \"agy\",
  \"lens\": \"documentation-coverage\",
  \"findings\": [
    {
      \"severity\": \"blocking|warning\",
      \"type\": \"mode-omission|pipeline-omission|stale|description-incomplete|role-boundary\",
      \"doc_file\": \"docs/file.md\",
      \"doc_quote\": \"exact phrase from the doc that is incomplete or wrong\",
      \"agent\": \"agent-name\",
      \"agent_file_says\": \"what the agent file actually defines\",
      \"fix\": \"specific text to add or change in the doc\"
    }
  ],
  \"verdict\": \"approve|request_changes\",
  \"summary\": \"one paragraph overall assessment\"
}"

    local tmpfile
    tmpfile=$(mktemp /tmp/validate_docs_agy_XXXXXX)
    printf '%s' "$prompt" > "$tmpfile"
    local result
    result=$(agy -p "$(cat "$tmpfile")" 2>/dev/null) || \
        result='{"reviewer":"agy","error":"agy invocation failed","findings":[],"verdict":"error","summary":"agy failed"}'
    rm -f "$tmpfile"
    echo "$result"
}

# ── codex: adversarial doc coverage probe ───────────────────────────────────
run_codex_doc_review() {
    local prompt
    prompt="You are adversarially reviewing documentation for an AI agent pipeline framework (project: ${PROJECT_NAME}).

Your job: find every place where documentation gives an incomplete or misleading picture of an agent's capabilities. You are looking for omissions that could cause a developer or another AI to misuse an agent — invoking it at the wrong time, skipping a mandatory step, or routing to the wrong place because the docs suggested a simpler picture.

## What to attack

1. MANDATORY STEPS DESCRIBED AS OPTIONAL: Does any doc describe a required project-start step as 'on demand' or 'reactive'?

2. INCOMPLETE INVOCATION CONTEXTS: If an agent must be invoked both at project start AND reactively during the build, does every place that describes 'when to invoke it' mention both contexts?

3. OUTPUT DOCUMENT COVERAGE: If an agent writes an output document (e.g. UX-DESIGN-READINESS.md), is that output consistently described everywhere the agent is described?

4. ESCALATION COVERAGE: If an agent escalates to X in one scenario and Y in another, do docs that describe the agent's escalation mention both paths?

5. SILENT SCOPE NARROWING: Does any doc make an agent's role appear narrower than it is? (e.g. 'validates framework files' when the agent also validates docs, or 'answers design questions' when the agent also performs a mandatory initial audit)

## Agent files (authoritative source)
${AGENT_CONTENT}

## Documentation files (what to attack)
${DOC_CONTENT}

Return JSON:
{
  \"reviewer\": \"codex\",
  \"lens\": \"doc-coverage-adversarial\",
  \"attacks\": [
    {
      \"severity\": \"high|medium|low\",
      \"type\": \"mandatory-as-optional|incomplete-context|missing-output|missing-escalation|scope-narrowing\",
      \"doc_file\": \"docs/file.md\",
      \"doc_quote\": \"exact phrase that is misleading\",
      \"agent\": \"agent-name\",
      \"scenario\": \"how this misleads a developer or another AI\",
      \"fix\": \"specific change to make the doc accurate\"
    }
  ],
  \"verdict\": \"approve|request_changes\",
  \"summary\": \"one paragraph\"
}"

    local tmpfile
    tmpfile=$(mktemp /tmp/validate_docs_codex_XXXXXX)
    printf '%s' "$prompt" > "$tmpfile"
    local result
    result=$(codex --quiet < "$tmpfile" 2>/dev/null) || \
        result='{"reviewer":"codex","error":"codex invocation failed","attacks":[],"verdict":"error","summary":"codex failed"}'
    rm -f "$tmpfile"
    echo "$result"
}

# ── Execute and write output ─────────────────────────────────────────────────
{
    printf "# Documentation Coverage Validation\n"
    printf "Timestamp: %s  Project: %s\n" "$TIMESTAMP" "$PROJECT_NAME"
    printf "verdict: pending\n"
    printf "highest_severity: none\n"
    printf "blocking_count: 0\n\n"
} > "$OUTFILE"

AGY_OUT=""
CODEX_OUT=""

if $AGY_AVAILABLE; then
    echo "Running agy (documentation coverage)..."
    AGY_OUT=$(run_agy_doc_review)
    {
        echo "## agy — Documentation Coverage"
        echo '```json'
        echo "$AGY_OUT"
        echo '```'
        echo ""
    } >> "$OUTFILE"
    echo "  done"
fi

if ! $SKIP_CODEX && $CODEX_AVAILABLE; then
    echo "Running codex (adversarial coverage probe)..."
    CODEX_OUT=$(run_codex_doc_review)
    {
        echo "## codex — Adversarial Coverage Probe"
        echo '```json'
        echo "$CODEX_OUT"
        echo '```'
        echo ""
    } >> "$OUTFILE"
    echo "  done"
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
request_changes = False
blocking_count = 0

for block in blocks:
    try:
        data = json.loads(block)
    except Exception:
        continue
    if data.get("verdict") in ("request_changes", "error"):
        request_changes = True
    for f in data.get("findings", []) + data.get("attacks", []):
        sev = str(f.get("severity", "low")).lower()
        try:
            if severities.index(sev) < severities.index(highest):
                highest = sev
        except ValueError:
            pass
        if sev in ("critical", "high", "blocking"):
            blocking_count += 1

verdict = "request_changes" if request_changes else "approve"
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

if [[ "$VERDICT" == "approve" ]]; then
    echo "════════════════════════════════════════════"
    echo "  PASS — documentation coverage clean"
    echo "════════════════════════════════════════════"
    exit 0
else
    echo "════════════════════════════════════════════"
    echo "  FAIL — verdict=${VERDICT} blocking=${BLOCKING:-?}"
    echo "  Invoke doc-validator agent to review and fix."
    echo "  Output: $OUTFILE"
    echo "════════════════════════════════════════════"
    exit 1
fi
