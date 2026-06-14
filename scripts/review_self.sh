#!/usr/bin/env bash
# review_self.sh — run an external reviewer (agy or codex) against the HOS itself.
#
# Sends all agent definitions, scripts, and the contract to the reviewer and asks it to
# check for:
#   1. Internal consistency — do file paths, schema field names, and variable
#      names match across agents, scripts, and the contract?
#   2. Completeness — are there pipeline gaps where no agent or script handles
#      a required action?
#   3. Correctness — logical flaws, missing error handling, unreachable code,
#      shell quoting issues, broken Python
#   4. Contract compliance — do the agents correctly implement what
#      OVERSIGHT-CONTRACT.md specifies, and vice versa?
#   5. Design intent — anything that looks like it contradicts the stated goals
#      in CLAUDE.md or METHODOLOGY.md
#
# Output goes to .claudetmp/self-review/review-{timestamp}.md and stdout.
#
# Usage:
#   ./scripts/review_self.sh                        # full review (agy by default)
#   ./scripts/review_self.sh --reviewer codex        # use codex instead
#   ./scripts/review_self.sh --dry-run               # show context size, no call
#   ./scripts/review_self.sh --focus consistency     # narrow review scope
#
# Prerequisites: agy or codex authenticated (./scripts/setup_clis.sh auth)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="$REPO_ROOT/.claudetmp/self-review"
DRY_RUN=false
FOCUS=""
REVIEWER="agy"   # agy (Gemini) | codex (OpenAI)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)   DRY_RUN=true; shift ;;
        --focus)     FOCUS="$2"; shift 2 ;;
        --reviewer)  REVIEWER="$2"; shift 2 ;;
        --help|-h)   sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) shift ;;
    esac
done

if [[ "$REVIEWER" != "agy" && "$REVIEWER" != "codex" ]]; then
    echo "Unknown reviewer '$REVIEWER'. Use: agy | codex" >&2
    exit 1
fi

GREEN="\033[32m"; YELLOW="\033[33m"; CYAN="\033[36m"
RED="\033[31m"; BOLD="\033[1m"; RESET="\033[0m"
ok()   { echo -e "  ${GREEN}✔${RESET}  $*"; }
info() { echo -e "  ${CYAN}→${RESET}  $*"; }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $*"; }
err()  { echo -e "  ${RED}✘${RESET}  $*"; }

mkdir -p "$OUT_DIR"
TIMESTAMP=$(date +%Y%m%dT%H%M%S)
OUTFILE="$OUT_DIR/review-${TIMESTAMP}.md"

cd "$REPO_ROOT"

# ── Build context bundle ──────────────────────────────────────────────────────
# Each section is labelled so agy can cross-reference by filename.

build_section() {
    local label="$1"
    local filepath="$2"
    if [[ -f "$filepath" ]]; then
        printf "\n\n=== FILE: %s ===\n" "$label"
        cat "$filepath"
    else
        printf "\n\n=== FILE: %s (NOT FOUND) ===\n" "$label"
    fi
}

info "Building context bundle..."

CONTEXT=""

# Core docs — intent and design history
CONTEXT+=$(build_section "CLAUDE.md"           "CLAUDE.md")
CONTEXT+=$(build_section "METHODOLOGY.md"      "METHODOLOGY.md")

# Contract — the ground truth spec
CONTEXT+=$(build_section "contract/OVERSIGHT-CONTRACT.md"         "contract/OVERSIGHT-CONTRACT.md")
CONTEXT+=$(build_section "contract/step-manifest.template.yaml"   "contract/step-manifest.template.yaml")

# Oversight agents — the six new agents
for agent in risk-assessor dep-mapper risk-historian oversight-evaluator oversight-orchestrator spec-red-team; do
    CONTEXT+=$(build_section ".claude/agents/${agent}.md" ".claude/agents/${agent}.md")
done

# Key scripts
CONTEXT+=$(build_section "install.sh"                                    "install.sh")
CONTEXT+=$(build_section "scripts/run_second_review.sh"                  "scripts/run_second_review.sh")
CONTEXT+=$(build_section "scripts/run_red_team.sh"                       "scripts/run_red_team.sh")
CONTEXT+=$(build_section "scripts/oversight/run_validators.sh"            "scripts/oversight/run_validators.sh")
CONTEXT+=$(build_section "scripts/oversight/token_tracker.py"             "scripts/oversight/token_tracker.py")
CONTEXT+=$(build_section "scripts/oversight/validators/schema.py"         "scripts/oversight/validators/schema.py")
CONTEXT+=$(build_section "scripts/oversight/validators/rn_calculator.py"  "scripts/oversight/validators/rn_calculator.py")
CONTEXT+=$(build_section "scripts/oversight/gates/security_scan.sh"       "scripts/oversight/gates/security_scan.sh")

CONTEXT_CHARS=${#CONTEXT}
CONTEXT_TOKENS=$(( CONTEXT_CHARS / 4 ))

ok "Context bundle: ~${CONTEXT_CHARS} chars (~${CONTEXT_TOKENS} tokens)"

if $DRY_RUN; then
    warn "DRY RUN — context built but agy not called"
    echo ""
    echo "Files included:"
    echo "$CONTEXT" | grep "^=== FILE:" | sed 's/=== FILE: /  /' | sed 's/ ===//'
    echo ""
    echo "To run for real: $0"
    exit 0
fi

if ! command -v agy &>/dev/null; then
    err "agy not found. Install + auth: ./scripts/setup_clis.sh"
    exit 1
fi

# ── Build review prompt ────────────────────────────────────────────────────────

SCOPE_INSTRUCTION=""
if [[ -n "$FOCUS" ]]; then
    case "$FOCUS" in
        consistency)
            SCOPE_INSTRUCTION="Focus ONLY on internal consistency: file paths, schema field names, variable names, and data structures that are referenced in multiple files. Ignore other issue types." ;;
        completeness)
            SCOPE_INSTRUCTION="Focus ONLY on pipeline completeness: steps in CLAUDE.md's pipeline that have no corresponding agent or script, required contract fields that nothing writes, and handoffs that appear to be missing." ;;
        correctness)
            SCOPE_INSTRUCTION="Focus ONLY on correctness: shell script bugs, Python logic errors, missing error handling, and broken control flow." ;;
        contract)
            SCOPE_INSTRUCTION="Focus ONLY on contract compliance: whether the agents correctly implement OVERSIGHT-CONTRACT.md and whether the contract is consistent with what the agents actually do." ;;
        *)
            warn "Unknown --focus value '$FOCUS'. Running full review."
            SCOPE_INSTRUCTION="" ;;
    esac
fi

PROMPT="You are an independent technical reviewer examining the Human Oversight System (HOS) — a framework for scaling human oversight of AI-generated code.

${SCOPE_INSTRUCTION}

## What you are reviewing

The HOS consists of:
- 6 oversight agents (.claude/agents/) that sit above a project's base development agents
- Scripts for running cross-vendor reviews (run_second_review.sh, run_red_team.sh)
- Validator scripts for risk scoring (run_validators.sh + validators/)
- An install script (install.sh)
- A portability contract (OVERSIGHT-CONTRACT.md) that any compliant agent team must implement

The system was designed with these goals (from METHODOLOGY.md and CLAUDE.md):
1. Portable: installable into any project via install.sh
2. Contract-based: oversight agents program against the contract, not against project-specific agent names
3. Risk-stratified: expensive external reviews only at higher risk scores
4. Independent: cross-vendor reviewers never see internal reviewer findings (decorrelation)
5. Audit-trail: sign-off register + token tracker + issue creation create a durable record

## Your review tasks

For each finding, state:
- Which files are involved
- What the specific problem is (quote the relevant lines/field names)
- Why it matters (what breaks or is inconsistent)
- A concrete fix

### 1. Internal consistency
Cross-reference field names, file paths, and data structures across files.

Specific things to check:
- The sign-off register format in OVERSIGHT-CONTRACT.md §3 vs. what oversight-evaluator.md reads
- The step manifest fields in OVERSIGHT-CONTRACT.md §5 vs. what oversight-evaluator.md checks
- File paths written by agents (e.g. \`.claudetmp/oversight/step{N}-evaluation-{ts}.md\`) vs. what oversight-orchestrator.md reads
- Variable names in run_second_review.sh (AGY_PROMPT, AGY_OUT, CODEX_OUT, etc.) vs. what token_tracker.py expects
- The risk tier thresholds in run_second_review.sh (\$AGY_THRESHOLD) vs. what schema.py defines as tier boundaries
- The sign-off entry that risk-assessor.md says it writes vs. what oversight-evaluator.md says it reads

### 2. Pipeline completeness
Using the pipeline diagram in CLAUDE.md as the source of truth:

- For each pipeline stage, is there an agent or script that handles it?
- Are there handoffs where the output of one component is never consumed by anything?
- Is the sign-off register written by all required agents for every step? Check each agent file.
- The contract says test agents write test declarations — do the base agents (unit-test, system-test) actually produce sign-off register entries? (Hint: they were amended to create GitHub issues but check if they write to the register.)
- Does oversight-orchestrator.md correctly reference the output of oversight-evaluator.md?
- Does run_second_review.sh correctly produce output that oversight-evaluator.md reads?

### 3. Correctness

Shell script issues:
- In run_second_review.sh: is the fallback logic (when agy unavailable, codex covers both lenses) correctly gated? Check the condition that prevents running codex twice.
- In run_red_team.sh: does the token tracker section reference variables (CODEX_OUT, AGY_OUT) that are defined in all code paths?
- In install.sh: does the rsync command for copying oversight scripts have the right source and destination paths?
- In run_validators.sh: does the inline Python script that produces summary.json handle empty validator results correctly?

Python issues:
- In rn_calculator.py: does \`_collect_functions\` correctly avoid double-counting nested functions? The inner \`_Collector\` calls \`ast.walk\` on the function node which would re-visit nested functions already visited at the top level.
- In token_tracker.py: verify that the \`report\` function correctly accesses the \`args.all\` attribute set by the \`--all\` subparser argument (declared with \`action='store_true'\`). The \`getattr(args, 'all', False)\` pattern should work — confirm no AttributeError occurs.

### 4. Contract compliance

- Does oversight-evaluator.md check all the compliance conditions listed in OVERSIGHT-CONTRACT.md §7?
- Does OVERSIGHT-CONTRACT.md §3 (sign-off schema) match what oversight-evaluator.md actually reads from the register?
- Are the \"issue creation rules\" in OVERSIGHT-CONTRACT.md §6 consistent with what the individual agent files (security-reviewer, privacy-reviewer, etc.) actually do?
- Does the step-manifest.template.yaml correctly demonstrate all the fields that OVERSIGHT-CONTRACT.md §5 defines?

### 5. Design intent
- Is there anything in the agents or scripts that contradicts the independence principle (cross-vendor reviewers should not see internal reviewer findings)?
- Does any agent or script create a single point of failure where the whole pipeline stops if one component is missing?
- Is the \"never lowers risk tier\" invariant correctly enforced in risk-assessor.md?

## Output format

Return a structured report with sections matching the 5 tasks above.

For each finding use:
**[SEVERITY]** — severity is one of: CRITICAL (breaks the pipeline), HIGH (significant flaw), MEDIUM (inconsistency or gap), LOW (minor issue or style)

**Finding:** [one sentence]
**Files:** [list of files involved]
**Evidence:** [specific lines or field names that demonstrate the problem]
**Fix:** [concrete change]

End with a brief overall assessment: is the system coherent and likely to work as designed?

---

## Files to review

${CONTEXT}"

# ── Invoke reviewer ───────────────────────────────────────────────────────────
echo ""
info "Sending to $REVIEWER (~${CONTEXT_TOKENS} estimated tokens)..."
echo ""

if ! command -v "$REVIEWER" &>/dev/null; then
    err "$REVIEWER not found. Install + auth: ./scripts/setup_clis.sh"
    exit 1
fi

case "$REVIEWER" in
    agy)   REVIEW_OUTPUT=$(agy   -p "$PROMPT"   2>&1) ;;
    codex) REVIEW_OUTPUT=$(codex exec "$PROMPT"  2>&1) ;;
esac || {
    err "$REVIEWER invocation failed (exit $?)"
    exit 1
}

# ── Write output ──────────────────────────────────────────────────────────────
REVIEWER_LABEL="$REVIEWER"
[[ "$REVIEWER" == "agy"   ]] && REVIEWER_LABEL="agy (Gemini)"
[[ "$REVIEWER" == "codex" ]] && REVIEWER_LABEL="codex (OpenAI)"

{
    printf "# HumanOversightSystem Self-Review\n"
    printf "Timestamp: %s\n" "$TIMESTAMP"
    printf "Reviewer: %s\n" "$REVIEWER_LABEL"
    printf "Context: ~%d tokens\n\n" "$CONTEXT_TOKENS"
    printf '%s\n\n' "---"
    printf '%s\n' "$REVIEW_OUTPUT"
} > "$OUTFILE"

echo "$REVIEW_OUTPUT"
echo ""
ok "Report saved: $OUTFILE"

# ── Token tracking ────────────────────────────────────────────────────────────
TRACKER="$REPO_ROOT/scripts/oversight/token_tracker.py"
if [[ -f "$TRACKER" ]]; then
    python3 "$TRACKER" record \
        --vendor "$REVIEWER" \
        --stage self-review \
        --step "meta" \
        --prompt-chars "${#PROMPT}" \
        --output-chars "${#REVIEW_OUTPUT}" 2>/dev/null || true
    echo ""
    python3 "$TRACKER" report 2>/dev/null || true
fi

# ── Create issues for HIGH/CRITICAL findings ──────────────────────────────────
# grep -c exits 1 on zero matches; || echo "0" would then append a second "0" making
# HIGH_COUNT="0\n0" which breaks [[ -gt ]]. Use || true and default separately.
HIGH_COUNT=$(echo "$REVIEW_OUTPUT" | grep -cE '\*\*\[CRITICAL\]\*\*|\*\*\[HIGH\]\*\*' 2>/dev/null || true)
HIGH_COUNT="${HIGH_COUNT:-0}"
if [[ "$HIGH_COUNT" -gt 0 ]] && command -v gh &>/dev/null; then
    echo ""
    warn "$HIGH_COUNT HIGH/CRITICAL finding(s) — creating GitHub issue..."
    gh issue create \
        --title "[AI: ${REVIEWER}] design-concern: ${HIGH_COUNT} HIGH/CRITICAL self-review findings (${TIMESTAMP})" \
        --body "$(printf '**Reviewer:** %s\n**Report:** %s\n\nSee the full report file for details.\n\n## Summary\n%s\n\n---\n*🤖 Created by `%s` | Step: session | Branch: `%s` | %s*' \
            "$REVIEWER_LABEL" \
            "$OUTFILE" \
            "$(echo "$REVIEW_OUTPUT" | head -60)" \
            "$REVIEWER" \
            "$(git branch --show-current 2>/dev/null || echo unknown)" \
            "$(date +%Y-%m-%d)")" \
        --label "design-concern" \
        2>/dev/null && ok "Issue created" || warn "Issue creation failed (gh auth?)"
fi
