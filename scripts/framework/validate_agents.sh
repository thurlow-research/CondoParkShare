#!/usr/bin/env bash
# validate_agents.sh — AI-powered cross-vendor review of agent definitions and docs.
#
# Runs agy (Gemini) for consistency/completeness and codex (OpenAI) for adversarial
# gap-finding against the agent pipeline framework files. Designed to be run:
#   - When a new agent is added or an existing agent is significantly changed
#   - When escalation paths between agents are modified
#   - When docs/AGENTS.md or docs/OVERSIGHT-RUNBOOK.md is updated
#   - Periodically as a framework health check
#
# This is the AI counterpart to check_agents_static.sh:
#   check_agents_static.sh  → structural/mechanical correctness, no AI, fast
#   validate_agents.sh      → semantic correctness (loops, gaps, contradictions), uses AI
#
# VENDOR ROLES (mirrors run_second_review.sh DECISIONS.md D4):
#   agy (Gemini)  — consistency + completeness lens
#   codex (OpenAI) — adversarial gap-finding lens
#
# Run check_agents_static.sh first; fix any structural issues before running this.
#
# Usage:
#   ./scripts/framework/validate_agents.sh
#   ./scripts/framework/validate_agents.sh --agents-dir .claude/agents
#   ./scripts/framework/validate_agents.sh --changed-only  # only changed vs HEAD~1
#   ./scripts/framework/validate_agents.sh --skip-codex    # agy only (faster)
#   ./scripts/framework/validate_agents.sh --skip-agy      # codex only
#
# Output: .claudetmp/framework/validation-YYYYMMDDTHHMMSS.md
#
# Exit codes:
#   0 — approved by both reviewers (no blocking findings)
#   1 — one or more blocking findings
#   2 — CLI unavailable or usage error

set -euo pipefail

AGENTS_DIR=".claude/agents"
DOCS_DIR="docs"
OUT_DIR=".claudetmp/framework"
CHANGED_ONLY=false
SKIP_AGY=false
SKIP_CODEX=false

# Load project-specific config if present
PROJECT_NAME="(unnamed project)"
PROJECT_STACK="(stack not configured)"
DESIGN_PACK_PATH=""
EXTRA_REVIEW_FILES=""
[[ -f "scripts/framework/config.sh" ]] && source scripts/framework/config.sh

while [[ $# -gt 0 ]]; do
    case "$1" in
        --agents-dir)   AGENTS_DIR="$2"; shift 2 ;;
        --docs-dir)     DOCS_DIR="$2";   shift 2 ;;
        --changed-only) CHANGED_ONLY=true; shift ;;
        --skip-codex)   SKIP_CODEX=true;  shift ;;
        --skip-agy)     SKIP_AGY=true;    shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

# ── Check CLI availability ───────────────────────────────────────────────────
AGY_AVAILABLE=false
CODEX_AVAILABLE=false
command -v agy   &>/dev/null && AGY_AVAILABLE=true  || true
command -v codex &>/dev/null && CODEX_AVAILABLE=true || true

if ! $SKIP_AGY && ! $AGY_AVAILABLE; then
    echo "WARN: agy not found — agy review will be skipped"
fi
if ! $SKIP_CODEX && ! $CODEX_AVAILABLE; then
    echo "WARN: codex not found — codex review will be skipped"
fi

if (! $AGY_AVAILABLE || $SKIP_AGY) && (! $CODEX_AVAILABLE || $SKIP_CODEX); then
    echo "ERROR: neither agy nor codex available/enabled — cannot run AI review" >&2
    echo "  Run check_agents_static.sh for the static-only check." >&2
    exit 2
fi

mkdir -p "$OUT_DIR"
TIMESTAMP=$(date +%Y%m%dT%H%M%S)
OUTFILE="$OUT_DIR/validation-${TIMESTAMP}.md"

echo "=== Agent pipeline validation: ${TIMESTAMP} ==="
echo "  Agents dir:  $AGENTS_DIR"
echo "  Docs dir:    $DOCS_DIR"
echo "  Output:      $OUTFILE"
echo ""

# ── Collect files ────────────────────────────────────────────────────────────
collect_files() {
    local content=""
    local files=()

    if $CHANGED_ONLY; then
        while IFS= read -r f; do
            [[ -f "$f" ]] && files+=("$f")
        done < <(git diff --name-only HEAD~1 -- "$AGENTS_DIR" "$DOCS_DIR" 2>/dev/null || true)
        if [[ ${#files[@]} -eq 0 ]]; then
            echo "WARN: --changed-only specified but no changed agent/doc files vs HEAD~1 — using all files" >&2
            CHANGED_ONLY=false
        fi
    fi

    if ! $CHANGED_ONLY; then
        while IFS= read -r -d '' f; do files+=("$f"); done \
            < <(find "$AGENTS_DIR" -name '*.md' -print0)
        [[ -f "$DOCS_DIR/AGENTS.md" ]]            && files+=("$DOCS_DIR/AGENTS.md")
        [[ -f "$DOCS_DIR/OVERSIGHT-RUNBOOK.md" ]] && files+=("$DOCS_DIR/OVERSIGHT-RUNBOOK.md")
        # Include design pack / extra files declared in config.sh
        [[ -n "$DESIGN_PACK_PATH" && -f "$DESIGN_PACK_PATH" ]] && files+=("$DESIGN_PACK_PATH")
        for ef in $EXTRA_REVIEW_FILES; do
            [[ -f "$ef" ]] && files+=("$ef")
        done
    fi

    echo "Collecting ${#files[@]} files..." >&2
    for f in "${files[@]}"; do
        content+="=== FILE: $f ===
$(cat "$f")

"
    done
    echo "$content"
}

REVIEW_PACKAGE=$(collect_files)

# ── agy: consistency and completeness ───────────────────────────────────────
run_agy() {
    local prompt
    prompt="You are reviewing AI agent definition files and documentation for a multi-agent software development pipeline. Project: ${PROJECT_NAME} (${PROJECT_STACK}). Your lens is CONSISTENCY and COMPLETENESS.

Check for:
1. ESCALATION LOOPS — any agent A that escalates to B which escalates back to A (directly or transitively)
2. DEAD ENDS — agent escalates to a role with no defined handler
3. CROSS-FILE MISMATCHES — when agent A says it sends X to agent B, does B's definition describe receiving and handling X?
4. FILE PATH INCONSISTENCY — any doc path referenced differently across files (e.g. docs/design/UX-DESIGN-READINESS.md vs design/UX-DESIGN-READINESS.md)
5. PIPELINE ORDERING PROBLEMS — does each agent's listed inputs exist as outputs from the preceding agent?
6. INVOCATION GAPS — scenarios where a design decision or gap could arise but no agent is instructed to handle it
7. TERMINOLOGY DRIFT — same concept named differently in different files

For each finding, name the exact files and lines affected. Be specific enough that a developer can fix the problem without additional investigation.

=== AGENT FILES AND DOCS ===
${REVIEW_PACKAGE}

Return JSON only — no prose outside the JSON block:
{
  \"reviewer\": \"agy\",
  \"lens\": \"consistency-completeness\",
  \"findings\": [
    {
      \"severity\": \"blocking|warning\",
      \"category\": \"loop|dead-end|mismatch|path|ordering|gap|terminology\",
      \"files\": [\"file1.md\", \"file2.md\"],
      \"description\": \"precise description — what is wrong and where\",
      \"fix\": \"specific change needed\"
    }
  ],
  \"verdict\": \"approve|request_changes\",
  \"summary\": \"one paragraph overall assessment\"
}"

    local tmpfile
    tmpfile=$(mktemp /tmp/validate_agents_agy_XXXXXX)
    echo "$prompt" > "$tmpfile"
    local result
    result=$(agy -p "$(cat "$tmpfile")" 2>/dev/null) || \
        result='{"reviewer":"agy","error":"agy invocation failed","findings":[],"verdict":"error","summary":"agy failed"}'
    rm -f "$tmpfile"
    echo "$result"
}

# ── codex: adversarial gap-finding ──────────────────────────────────────────
run_codex() {
    local prompt
    prompt="You are adversarially reviewing AI agent pipeline documentation for a project called ${PROJECT_NAME}. Your job is to find every gap, contradiction, and failure mode. Break this design.

Attack vectors to probe:
1. SCOPE CREEP — can any agent's 'additive'/'clarifying' classification be exploited to make structural changes without human approval? Are change-type boundaries tight enough?
2. SINGLE POINTS OF FAILURE — if any startup-phase agent misses something in its initial audit, what is the recovery path? Is it clearly defined?
3. HUMAN GATE BYPASS — are there decisions that should require human approval but are routed to an agent instead?
4. RESPONSIBILITY VACUUM — find scenarios where a gap or conflict arises but no agent is clearly responsible for resolving it
5. CONTRADICTIONS — find any case where two agent files give conflicting instructions for the exact same scenario
6. UNDERSPECIFIED HANDOFFS — 'notify X' or 'inform Y' with no defined input protocol for the receiver
7. MISSING LOOP EXITS — any agent iteration loop that could run indefinitely with no defined exit condition

For each attack, describe the specific scenario that causes failure, not just the category.

=== FILES TO ATTACK ===
${REVIEW_PACKAGE}

Return JSON only:
{
  \"reviewer\": \"codex\",
  \"lens\": \"adversarial-gaps\",
  \"attacks\": [
    {
      \"severity\": \"critical|high|medium\",
      \"type\": \"scope-creep|single-point-failure|human-gate-bypass|vacuum|contradiction|underspecified|no-exit\",
      \"files\": [\"file1.md\"],
      \"scenario\": \"specific scenario where this fails\",
      \"impact\": \"what goes wrong\",
      \"fix\": \"specific change to close the gap\"
    }
  ],
  \"verdict\": \"approve|request_changes\",
  \"summary\": \"one paragraph overall assessment\"
}"

    local tmpfile
    tmpfile=$(mktemp /tmp/validate_agents_codex_XXXXXX)
    echo "$prompt" > "$tmpfile"
    local result
    result=$(codex --quiet < "$tmpfile" 2>/dev/null) || \
        result='{"reviewer":"codex","error":"codex invocation failed","attacks":[],"verdict":"error","summary":"codex failed"}'
    rm -f "$tmpfile"
    echo "$result"
}

# ── Execute reviewers and write output ──────────────────────────────────────
{
    printf "# Agent Pipeline Validation\n"
    printf "Timestamp: %s\n" "$TIMESTAMP"
    printf "verdict: pending\n"
    printf "highest_severity: none\n"
    printf "blocking_count: 0\n\n"
} > "$OUTFILE"

AGY_OUT=""
CODEX_OUT=""

if ! $SKIP_AGY && $AGY_AVAILABLE; then
    echo "Running agy (consistency + completeness)..."
    AGY_OUT=$(run_agy)
    {
        echo "## agy — Consistency + Completeness"
        echo '```json'
        echo "$AGY_OUT"
        echo '```'
        echo ""
    } >> "$OUTFILE"
    echo "  done"
else
    echo "## agy — SKIPPED" >> "$OUTFILE"
fi

if ! $SKIP_CODEX && $CODEX_AVAILABLE; then
    echo "Running codex (adversarial gap-finding)..."
    CODEX_OUT=$(run_codex)
    {
        echo "## codex — Adversarial Gap Analysis"
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

# ── Finalize verdict header ──────────────────────────────────────────────────
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

new_content = re.sub(r'^verdict: pending$',    f'verdict: {verdict}',       content, flags=re.M)
new_content = re.sub(r'^highest_severity: none$', f'highest_severity: {highest}', new_content, flags=re.M)
new_content = re.sub(r'^blocking_count: 0$',   f'blocking_count: {blocking_count}', new_content, flags=re.M)
open(path, 'w').write(new_content)
print(f"  verdict={verdict} highest_severity={highest} blocking={blocking_count}")
PYEOF

# ── Print summary ────────────────────────────────────────────────────────────
echo ""
echo "Output: $OUTFILE"
echo ""

VERDICT=$(grep '^verdict:' "$OUTFILE" | head -1 | awk '{print $2}')
BLOCKING=$(grep '^blocking_count:' "$OUTFILE" | head -1 | awk '{print $2}')

if [[ "$VERDICT" == "approve" ]]; then
    echo "════════════════════════════════════════════"
    echo "  PASS — both reviewers approved"
    echo "════════════════════════════════════════════"
    exit 0
else
    echo "════════════════════════════════════════════"
    echo "  FAIL — verdict=${VERDICT} blocking=${BLOCKING:-?}"
    echo "  Review: $OUTFILE"
    echo "════════════════════════════════════════════"
    exit 1
fi
