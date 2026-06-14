#!/usr/bin/env bash
# run_red_team.sh — system-level adversarial red-team at build milestones.
#
# Distinct from run_second_review.sh (change-level, pre-PR) and run_panel.sh
# (PR-level, post-PR). This reads the FULL codebase as a target — not a diff —
# and tries to construct end-to-end attack chains that cross component boundaries.
#
# Run at checkpoints, not after every step:
#   --milestone auth        After auth + TOTP is complete (Step 3)
#   --milestone booking     After booking gates are complete (Step 6)
#   --milestone admin       After admin/operator portals are complete (Step 10)
#   --milestone deploy      After deployment config is complete (Step 11)
#
# Two reviewers (DECISIONS.md D4 — no Claude as independent check):
#   codex (OpenAI)  — adversarial attack: constructs exploit chains
#   agy   (Gemini)  — spec vs implementation gap at system level
#
# REQUIRED OUTPUT: both "EXPLOITABLE" findings AND "NOT EXPLOITABLE" attestations.
# A clean finding list without explicit "not exploitable" attestations is not
# a valid red-team report — absence of findings ≠ absence of vulnerabilities.
#
# Issue creation: critical/high findings → red-team-finding issues.
#
# Usage:
#   ./scripts/run_red_team.sh --milestone auth
#   ./scripts/run_red_team.sh --milestone booking --dry-run
#
# Prerequisites: agy + codex authenticated, gh authenticated

set -euo pipefail

[[ -f .env ]] && set -o allexport && source .env && set +o allexport 2>/dev/null || true

GREEN="\033[32m"; YELLOW="\033[33m"; CYAN="\033[36m"
RED="\033[31m"; BOLD="\033[1m"; RESET="\033[0m"
ok()   { echo -e "  ${GREEN}✔${RESET}  $*"; }
info() { echo -e "  ${CYAN}→${RESET}  $*"; }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $*"; }

MILESTONE=""
DRY_RUN=false
OUT_DIR=".claudetmp/red-team"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --milestone) MILESTONE="$2"; shift 2 ;;
        --dry-run)   DRY_RUN=true; shift ;;
        *) shift ;;
    esac
done

if [[ -z "$MILESTONE" ]]; then
    echo "Usage: $0 --milestone [auth|booking|admin|deploy]"
    exit 1
fi

mkdir -p "$OUT_DIR"
TIMESTAMP=$(date +%Y%m%dT%H%M%S)
OUTFILE="$OUT_DIR/checkpoint-${MILESTONE}-${TIMESTAMP}.md"

# ── Scope definitions ────────────────────────────────────────────────────────
case "$MILESTONE" in
    auth)
        SCOPE="Authentication system: TOTP enrollment and verification, recovery codes, invite tokens, session management, registration flows."
        ATTACK_FOCUS="TOTP replay attacks, recovery code exhaustion/reuse, invite token abuse, session fixation, account enumeration, timing attacks on login."
        SPEC_FOCUS="Attack vectors attackers could use before or during authentication that the spec may not have anticipated."
        CODEX_FILES=$(find . -path "*/accounts/*" -name "*.py" -not -path "*/.venv/*" -not -path "*/__pycache__/*" 2>/dev/null | head -30 | tr '\n' ' ')
        ;;
    booking)
        SCOPE="Core booking logic: availability computation, booking creation with three gates (horizon, one-active, overlap), cancellation, early release."
        ATTACK_FOCUS="Horizon metric gaming, concurrent booking exploits (race conditions), gate bypass combinations, booking-then-cancel abuse cycles, cross-resident booking manipulation."
        SPEC_FOCUS="Business rule gaming vectors and combinations of legitimate actions that produce illegitimate outcomes."
        CODEX_FILES=$(find . -path "*/parking/*" -name "*.py" -not -path "*/.venv/*" -not -path "*/__pycache__/*" 2>/dev/null | head -30 | tr '\n' ' ')
        ;;
    admin)
        SCOPE="Admin and operator access controls: HOA portal, operator console, Django admin, audit logging, tenant isolation for admin views."
        ATTACK_FOCUS="Privilege escalation (resident → HOA admin → operator), cross-tenant data access via admin views, IDOR in admin objects, audit log bypass or forgery."
        SPEC_FOCUS="Whether admin boundaries are correctly enforced end-to-end — not just in views but across all access paths."
        CODEX_FILES=$(find . -path "*/admin*" -name "*.py" -not -path "*/.venv/*" -not -path "*/__pycache__/*" 2>/dev/null | head -30 | tr '\n' ' ')
        ;;
    deploy)
        SCOPE="Deployment configuration: TLS termination, security headers, database exposure, Docker networking, environment variable handling, backup configuration."
        ATTACK_FOCUS="Database port exposure, missing security headers, misconfigured TLS, secrets in environment, HTTP→HTTPS bypass."
        SPEC_FOCUS="Whether the deployed system matches the spec's security requirements for the production environment."
        CODEX_FILES=$(find . -name "*.yml" -o -name "*.yaml" -o -name "Caddyfile" -o -name "Dockerfile" | head -20 | tr '\n' ' ')
        ;;
    *)
        echo "Unknown milestone: $MILESTONE (use: auth | booking | admin | deploy)"
        exit 1
        ;;
esac

echo ""
echo -e "${BOLD}=== Red-team checkpoint: ${MILESTONE} ===${RESET}"
echo "Output: $OUTFILE"
echo ""

if $DRY_RUN; then
    warn "DRY RUN — no external CLI calls will be made"
fi

# ── Branch + PR context (stamped on every issue) ─────────────────────────────
CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
CURRENT_PR=$(gh pr view --json number,url --jq '"#\(.number) \(.url)"' 2>/dev/null || echo "none")

# ── Load context ─────────────────────────────────────────────────────────────
SPEC_CONTENT=""
[[ -f "Specs/SPEC-1-pilot.md" ]] && SPEC_CONTENT=$(cat Specs/SPEC-1-pilot.md)

CODEBASE_SAMPLE=""
if [[ -n "$CODEX_FILES" ]]; then
    for f in $CODEX_FILES; do
        [[ -f "$f" ]] && CODEBASE_SAMPLE+="=== $f ===\n$(cat "$f")\n\n"
    done
fi

{
    printf "# Red-Team Report — %s checkpoint\n" "$MILESTONE"
    printf "Timestamp: %s\n" "$TIMESTAMP"
    printf "Scope: %s\n\n" "$SCOPE"
} > "$OUTFILE"

# ── Helper: create issues for findings ───────────────────────────────────────
create_redteam_issues() {
    local reviewer="$1"
    local findings_json="$2"

    python3 - "$reviewer" "$findings_json" "$MILESTONE" "$CURRENT_BRANCH" "$CURRENT_PR" <<'PYEOF'
import json, subprocess, sys

reviewer, milestone, branch, pr = sys.argv[1], sys.argv[3], sys.argv[4], sys.argv[5]

try:
    data = json.loads(sys.argv[2])
    findings = data.get("exploitable_findings", data.get("findings", []))
except Exception:
    sys.exit(0)

for f in findings:
    sev = str(f.get("severity", "low")).lower()
    if sev not in ("critical", "high"):
        continue

    title = f"[AI: red-team/{reviewer}] red-team-finding [{milestone}]: {str(f.get('finding','?'))[:70]}"
    body = "\n".join([
        f"**Milestone:** {milestone}",
        f"**Reviewer:** {reviewer}",
        f"**Severity:** {sev}",
        f"**Branch:** {branch}",
        f"**PR:** {pr}",
        f"**CWE:** {f.get('cwe','')}",
        f"**Attack chain:** {f.get('attack_chain', f.get('attack_scenario', ''))}",
        f"**Finding:** {f.get('finding', '')}",
        f"**Files involved:** {', '.join(f.get('files', []))}",
        f"**Remediation:** {f.get('remediation', f.get('suggestion', ''))}",
        "",
        "---",
        f"*🤖 Created by `red-team/{reviewer}` | Step: {milestone} | Branch: `{branch}` | PR: {pr}*",
    ])

    cmd = ["gh", "issue", "create", "--title", title, "--body", body,
           "--label", "red-team-finding"]
    if sev == "critical":
        cmd += ["--label", "security-finding"]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            print(f"  Issue: {r.stdout.strip()}")
    except Exception as e:
        print(f"  Issue creation failed: {e}", file=sys.stderr)
PYEOF
}

# ── codex: adversarial attack chains ─────────────────────────────────────────
info "Running codex (adversarial attack chains)..."

CODEX_PROMPT="You are a professional penetration tester attacking ${SCOPE}

## Threat model
- Primary: registered building resident exploiting legitimate features
- Secondary: HOA admin at building A accessing building B data
- External: unauthenticated attacker

## Your task
${ATTACK_FOCUS}

Construct complete attack chains, not just individual vulnerabilities.
For each attack: what sequence of legitimate + illegitimate actions achieves the goal?

## Codebase
<code>
${CODEBASE_SAMPLE}
</code>

## REQUIRED OUTPUT FORMAT
Return JSON with BOTH exploitable findings AND explicit not-exploitable attestations:
{
  \"reviewer\": \"codex\",
  \"milestone\": \"${MILESTONE}\",
  \"exploitable_findings\": [
    {
      \"severity\": \"critical|high|medium\",
      \"cwe\": \"CWE-XXX\",
      \"finding\": \"what is exploitable\",
      \"attack_chain\": \"step-by-step: attacker does X, then Y, then Z to achieve W\",
      \"files\": [\"path/to/file.py\"],
      \"remediation\": \"specific fix\"
    }
  ],
  \"not_exploitable_attestations\": [
    {
      \"vector\": \"description of attack vector tested\",
      \"verdict\": \"NOT EXPLOITABLE\",
      \"reason\": \"why this is safe\"
    }
  ],
  \"summary\": \"one paragraph\"
}"

if ! $DRY_RUN && command -v codex &>/dev/null; then
    CODEX_OUT=$(codex exec "$CODEX_PROMPT" 2>/dev/null || \
        echo '{"reviewer":"codex","error":"invocation failed","exploitable_findings":[],"not_exploitable_attestations":[],"summary":"error"}')
else
    CODEX_OUT='{"reviewer":"codex","skipped":true,"exploitable_findings":[],"not_exploitable_attestations":[],"summary":"dry-run or codex unavailable"}'
fi

{
    echo "## codex — Adversarial Attack Chains"
    echo '```json'
    echo "$CODEX_OUT"
    echo '```'
    echo ""
} >> "$OUTFILE"

$DRY_RUN || create_redteam_issues "codex" "$CODEX_OUT"
ok "codex done"

# ── agy: spec vs implementation gap at system level ──────────────────────────
info "Running agy (spec vs. implementation gap)..."

AGY_PROMPT="You are a system-level spec reviewer examining ${SCOPE}

## Your task
${SPEC_FOCUS}

Compare the spec's stated requirements against the actual implementation. Find:
1. Requirements the spec states that the code doesn't implement correctly
2. Behaviors the code implements that the spec doesn't cover (implicit behavior)
3. Combinations of features that produce spec-violating outcomes
4. Missing invariants (properties that should always hold but aren't enforced)

## Spec
<spec>
${SPEC_CONTENT}
</spec>

## Implementation sample
<code>
${CODEBASE_SAMPLE}
</code>

## REQUIRED OUTPUT FORMAT
{
  \"reviewer\": \"agy\",
  \"milestone\": \"${MILESTONE}\",
  \"exploitable_findings\": [
    {
      \"severity\": \"high|medium|low\",
      \"finding\": \"spec says X, code does Y\",
      \"spec_section\": \"§N\",
      \"files\": [\"path/to/file.py\"],
      \"suggestion\": \"what needs to change\"
    }
  ],
  \"not_exploitable_attestations\": [
    {
      \"vector\": \"spec requirement tested\",
      \"verdict\": \"CORRECTLY IMPLEMENTED\",
      \"reason\": \"how verified\"
    }
  ],
  \"summary\": \"one paragraph\"
}"

if ! $DRY_RUN && command -v agy &>/dev/null; then
    AGY_OUT=$(agy -p "$AGY_PROMPT" 2>/dev/null || \
        echo '{"reviewer":"agy","error":"invocation failed","exploitable_findings":[],"not_exploitable_attestations":[],"summary":"error"}')
else
    AGY_OUT='{"reviewer":"agy","skipped":true,"exploitable_findings":[],"not_exploitable_attestations":[],"summary":"dry-run or agy unavailable"}'
fi

{
    echo "## agy — Spec vs Implementation Gap"
    echo '```json'
    echo "$AGY_OUT"
    echo '```'
    echo ""
} >> "$OUTFILE"

$DRY_RUN || create_redteam_issues "agy" "$AGY_OUT"
ok "agy done"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
ok "Red-team complete: $OUTFILE"
echo ""
echo "This report contains both EXPLOITABLE findings and NOT EXPLOITABLE attestations."
echo "A clean finding list without attestations is not a valid red-team report."
echo ""
$DRY_RUN || echo "GitHub issues created for critical/high findings."

# ── Token usage ───────────────────────────────────────────────────────────────
TRACKER="$(dirname "$0")/oversight/token_tracker.py"
if [[ -f "$TRACKER" ]] && ! $DRY_RUN; then
    CODEBASE_CHARS=${#CODEBASE_SAMPLE}
    SPEC_CHARS=${#SPEC_CONTENT}
    PROMPT_BASE=$((CODEBASE_CHARS + SPEC_CHARS))

    # Only record if the tool actually ran (not a dry-run placeholder JSON)
    [[ -n "${CODEX_OUT:-}" ]] && ! echo "${CODEX_OUT}" | grep -q '"skipped":true' && \
        python3 "$TRACKER" record \
            --vendor codex --stage red-team --step "$MILESTONE" \
            --prompt-chars $((PROMPT_BASE + 800)) \
            --output-chars ${#CODEX_OUT} 2>/dev/null || true

    [[ -n "${AGY_OUT:-}" ]] && ! echo "${AGY_OUT}" | grep -q '"skipped":true' && \
        python3 "$TRACKER" record \
            --vendor agy --stage red-team --step "$MILESTONE" \
            --prompt-chars $((PROMPT_BASE + 600)) \
            --output-chars ${#AGY_OUT} 2>/dev/null || true

    echo ""
    python3 "$TRACKER" report 2>/dev/null || true
fi
