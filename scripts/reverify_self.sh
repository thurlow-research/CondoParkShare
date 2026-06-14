#!/usr/bin/env bash
# reverify_self.sh — send agy a targeted re-review of the fixes made in response
# to its initial self-review findings.
#
# Unlike review_self.sh (which sends the full codebase), this script sends:
#   1. The original findings verbatim from the last review
#   2. The actual git diff for every fix
#   3. An explanation for each finding that was NOT fixed, with our reasoning
#   4. A request for agy to verify each fix and agree/disagree on the non-fixes
#
# Usage:
#   ./scripts/reverify_self.sh                  # uses latest review file
#   ./scripts/reverify_self.sh --dry-run        # show prompt size, no call
#   ./scripts/reverify_self.sh --review <file>  # specify a review file explicitly
#
# Prerequisites: agy authenticated

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="$REPO_ROOT/.claudetmp/self-review"
DRY_RUN=false
REVIEW_FILE=""
REVIEWER="agy"   # agy | codex

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)       DRY_RUN=true; shift ;;
        --review)        REVIEW_FILE="$2"; shift 2 ;;
        --reviewer)      REVIEWER="$2"; shift 2 ;;
        --help|-h)       sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) shift ;;
    esac
done

GREEN="\033[32m"; YELLOW="\033[33m"; CYAN="\033[36m"
RED="\033[31m"; BOLD="\033[1m"; RESET="\033[0m"
ok()   { echo -e "  ${GREEN}✔${RESET}  $*"; }
info() { echo -e "  ${CYAN}→${RESET}  $*"; }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $*"; }

mkdir -p "$OUT_DIR"
TIMESTAMP=$(date +%Y%m%dT%H%M%S)
OUTFILE="$OUT_DIR/reverify-${TIMESTAMP}.md"

cd "$REPO_ROOT"

# ── Find the original review file ────────────────────────────────────────────
if [[ -z "$REVIEW_FILE" ]]; then
    # Most recent review- file (not reverify-)
    REVIEW_FILE=$(ls "$OUT_DIR"/review-*.md 2>/dev/null | sort | tail -1)
fi

if [[ -z "$REVIEW_FILE" || ! -f "$REVIEW_FILE" ]]; then
    echo "No review file found. Run: bash scripts/review_self.sh first"
    exit 1
fi

ok "Original review: $REVIEW_FILE"

# ── Get the fix diff (bootstrap → post-fix) ───────────────────────────────────
# Bootstrap commit is the first large commit; fixes are in the next commit
BOOTSTRAP_SHA=$(git log --oneline | grep "Bootstrap Human Oversight System" | awk '{print $1}')
FIXES_SHA=$(git log --oneline | grep "Fix all issues found by agy" | awk '{print $1}')

if [[ -z "$BOOTSTRAP_SHA" || -z "$FIXES_SHA" ]]; then
    # Fallback: diff between HEAD and HEAD~3 (approximate)
    DIFF_CONTENT=$(git diff HEAD~2..HEAD -- \
        scripts/oversight/validators/rn_calculator.py \
        scripts/oversight/validators/schema.py \
        scripts/oversight/run_validators.sh \
        scripts/run_second_review.sh \
        scripts/run_red_team.sh \
        scripts/run_panel.sh \
        install.sh \
        .claude/agents/oversight-evaluator.md \
        .claude/agents/risk-assessor.md \
        contract/OVERSIGHT-CONTRACT.md \
        2>/dev/null || git diff HEAD~1..HEAD)
else
    DIFF_CONTENT=$(git diff "${BOOTSTRAP_SHA}..${FIXES_SHA}" -- \
        scripts/oversight/validators/rn_calculator.py \
        scripts/oversight/validators/schema.py \
        scripts/oversight/run_validators.sh \
        scripts/run_second_review.sh \
        scripts/run_red_team.sh \
        scripts/run_panel.sh \
        install.sh \
        .claude/agents/oversight-evaluator.md \
        .claude/agents/risk-assessor.md \
        contract/OVERSIGHT-CONTRACT.md \
        2>/dev/null)
fi

info "Diff size: $(echo "$DIFF_CONTENT" | wc -c) chars"

# ── Extract original findings section from the review file ───────────────────
# Everything from "# Technical Review Report" onwards, excluding the preamble
ORIGINAL_FINDINGS=$(sed -n '/^# Technical Review Report/,$p' "$REVIEW_FILE" 2>/dev/null || \
    tail -300 "$REVIEW_FILE")

# ── Build the re-review prompt ────────────────────────────────────────────────
PROMPT="You previously reviewed the Human Oversight System (HOS) codebase and produced
the findings below. We have now applied fixes in response. This is your re-review.

Your tasks:
1. For each finding you raised, examine the diff and confirm whether it is RESOLVED,
   PARTIALLY RESOLVED, or UNRESOLVED. Be specific about what the fix does and whether
   it fully addresses the original issue.
2. Two findings were intentionally not fixed. We explain our reasoning below.
   Please indicate whether you AGREE or DISAGREE with each explanation.
3. If you find any new issues introduced by the fixes, flag them.

---

## Your original findings

${ORIGINAL_FINDINGS}

---

## What was fixed

Here is the full diff of changes made in response to your findings:

\`\`\`diff
${DIFF_CONTENT}
\`\`\`

### Fix mapping (finding → change)

**[CRITICAL] Double-counting nested functions in rn_calculator.py**
→ Replaced \`ast.walk(node)\` loop with \`self.generic_visit(node)\` in \`_collect_functions\`.
  The NodeVisitor machinery now handles recursion naturally, eliminating double-processing
  of depth-3+ functions.

**[HIGH] Stale validator results contaminate composite score**
→ Added \`rm -f \"\$OUT_DIR\"/*.json\` before each run_validators.sh execution.

**[HIGH] Token tracker sees empty AGY_PROMPT/CODEX_PROMPT (function-local)**
→ Changed token tracking to estimate from globally-available content:
  prompt size estimated as length(DIFF_CONTENT) + length(SPEC_CONTEXT) + length(VALIDATOR_SUMMARY) + 800
  (CODEX likewise from DIFF_CONTENT alone + 600)

**[HIGH] Handoff document written but never consumed by panel**
→ Modified run_panel.sh: loads the most recent \`.claudetmp/oversight/step*-handoff.md\`
  and injects it as a context section in \`build_review_prompt()\`, clearly labelled
  so reviewers know it is from the internal team (independence maintained by
  instructing reviewers not to let it prevent them finding issues the team missed).

**[HIGH] Contradiction: who creates GitHub issues (evaluator vs orchestrator)**
→ Fixed oversight-evaluator.md \"What you do NOT do\" section:
  \"Do not create GitHub issues — issue creation is the base agents' and scripts' responsibility.\"

**[MEDIUM] Boundary inconsistency: 0.30 → LOW in schema.py, MEDIUM in run_validators.sh**
→ Replaced \`score_to_tier\` in schema.py with explicit exclusive upper bounds:
  \`if score < 0.30: return \"LOW\"\`, etc. — now consistent with run_validators.sh.

**[MEDIUM] Missing --step validation in run_second_review.sh**
→ Added guard immediately after arg parsing:
  \`if [[ -z \"\$STEP\" ]]; then echo \"Error: --step is required\"; exit 1; fi\`

**[MEDIUM] Token tracker records skipped/dry-run placeholder JSON as real usage**
→ Added \`! echo \"\${CODEX_OUT}\" | grep -q '\"skipped\":true'\` guard before recording.

**[MEDIUM] CONDITIONAL register status unspecified in evaluator**
→ Updated Phase 1 compliance check: CONDITIONAL passes compliance but automatically
  forces CONDITIONAL_PROCEED in Phase 2.

**[MEDIUM] Test declaration fields referenced but not in §3 schema**
→ Added clarifying paragraph in OVERSIGHT-CONTRACT.md §3 stating test agents write
  §4 fields inline in their §3 register entry.

**[MEDIUM] risk-assessor writes register entry evaluator never reads**
→ Removed the sign-off register write from risk-assessor.md entirely.
  Added \`.claudetmp/oversight/human-tier-override.md\` as the concrete mechanism
  for the \"human concurrence\" requirement to lower a tier.

**[LOW] Redundant copy of capture_prompt.sh / prompt_audit.sh in install.sh**
→ Removed those two scripts from the loop; setup_oversight.sh already copies them.

---

## What was NOT fixed, and why

### Finding: Unimplemented pipeline stages (EXPENSIVE GATES and MERGE → ARCHIVE)
**Your finding [MEDIUM]:** CLAUDE.md §6 mentions steps 8 (EXPENSIVE GATES) and 12
(MERGE → ARCHIVE) but no scripts implement them.

**Our decision: intentionally not fixed.**

These stages are marked 🔧 in CLAUDE.md — the notation used throughout this codebase
to mean \"designed and agreed, not yet implemented.\" They are not bugs or oversights;
they are honest status markers for future work. Implementing them now would be
premature — EXPENSIVE GATES depends on a project's specific CI configuration, and
MERGE → ARCHIVE requires knowing the PR structure once it exists. The METHODOLOGY.md
explicitly lists them as planned.

**Do you agree that these are correctly deferred rather than bugs? Or do you think
the 🔧 notation is insufficient and we should add placeholder stubs or a stub script?**

---

### Finding: False positive — token_tracker.py \"signature mismatch\"
**Your finding [LOW]:** The self-review checklist in review_self.sh claimed that
token_tracker.py had a signature mismatch (args.all vs store_true).

**Our decision: not fixed — your own report called this a false positive.**

You wrote in §3 of your findings: \"This is a false positive in the review checklist;
the code behaves correctly and requires no changes.\" We agree. \`getattr(args, 'all', False)\`
works correctly with \`action='store_true'\`. The only thing we could change is removing
the false positive from the review_self.sh checklist prompt, but since the prompt is
asking *you* to check it, and you correctly identified it as a false positive, the
check is doing its job — prompting adversarial verification, not asserting a bug.

**Do you agree nothing needs to change here, or is there a cleaner way to phrase
the checklist item so future runs don't generate noise?**

---

## Your re-review output format

For each original finding, state one of:
- **RESOLVED** — fix fully addresses the issue
- **PARTIALLY RESOLVED** — fix addresses it but leaves a residual concern (explain)
- **UNRESOLVED** — fix does not address the issue (explain)

For each non-fix explanation, state:
- **AGREE** — the reasoning is sound
- **DISAGREE** — the reasoning is flawed (explain why)
- **SUGGEST** — agree with the intent but recommend a different approach

Flag any new issues introduced by the fixes.

End with an overall verdict: is the codebase in a better state than before?"

# ── Dry run ───────────────────────────────────────────────────────────────────
PROMPT_CHARS=${#PROMPT}
PROMPT_TOKENS=$(( PROMPT_CHARS / 4 ))

info "Prompt size: ~${PROMPT_CHARS} chars (~${PROMPT_TOKENS} tokens)"

if $DRY_RUN; then
    warn "DRY RUN — prompt built, $REVIEWER not called"
    echo ""
    echo "Original review: $REVIEW_FILE"
    echo "To run: $0 --reviewer $REVIEWER"
    exit 0
fi

if ! command -v "$REVIEWER" &>/dev/null; then
    echo "$REVIEWER not found. Install + auth via the HOS machine bootstrap (bootstrap/setup_clis.sh)"
    exit 1
fi

# ── Call reviewer ─────────────────────────────────────────────────────────────
info "Sending to $REVIEWER (~${PROMPT_TOKENS} estimated tokens)..."
echo ""

case "$REVIEWER" in
    agy)   REVIEW_OUTPUT=$(agy   -p "$PROMPT"   2>&1) ;;
    codex) REVIEW_OUTPUT=$(codex exec "$PROMPT"  2>&1) ;;
esac || {
    echo "$REVIEWER invocation failed (exit $?)" >&2
    exit 1
}

# ── Write output ──────────────────────────────────────────────────────────────
{
    printf "# HumanOversightSystem Re-Verification\n"
    printf "Timestamp: %s\n" "$TIMESTAMP"
    printf "Reviewer: %s\n" "$REVIEWER"
    printf "Based on: %s\n" "$REVIEW_FILE"
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
        --vendor "$REVIEWER" --stage self-review \
        --step "meta-reverify" \
        --prompt-chars "$PROMPT_CHARS" \
        --output-chars "${#REVIEW_OUTPUT}" 2>/dev/null || true
    echo ""
    python3 "$TRACKER" report 2>/dev/null || true
fi
