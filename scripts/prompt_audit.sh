#!/usr/bin/env bash
# prompt_audit.sh — query the prompt artifact audit trail
#
# Usage:
#   ./scripts/prompt_audit.sh              # list all AI-assisted commits
#   ./scripts/prompt_audit.sh --risk HIGH  # filter by risk level
#   ./scripts/prompt_audit.sh --pending    # show artifacts with Pending review status
#   ./scripts/prompt_audit.sh --stats      # summary statistics

set -euo pipefail

MODE="list"
RISK_FILTER=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --risk)    RISK_FILTER="$2"; MODE="list"; shift 2 ;;
    --pending) MODE="pending"; shift ;;
    --stats)   MODE="stats"; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── List AI commits ────────────────────────────────────────────────────────────
if [[ "$MODE" == "list" ]]; then
  echo "AI-assisted commits:"
  echo ""
  if [[ -n "$RISK_FILTER" ]]; then
    git log --grep="AI-Risk: ${RISK_FILTER}" \
      --pretty=format:"%h %ad %s" --date=short | head -40
  else
    git log --grep="Prompt-Artifact:" \
      --pretty=format:"%h %ad %s%n  $(git log --pretty=format:'%b' -1 %H 2>/dev/null | grep 'AI-Risk:' || echo '')" \
      --date=short | head -60
  fi
fi

# ── Pending review ─────────────────────────────────────────────────────────────
if [[ "$MODE" == "pending" ]]; then
  echo "Prompt artifacts with Pending human review:"
  echo ""
  if [[ ! -d "prompts" ]]; then
    echo "  No prompts/ directory found."
    exit 0
  fi
  COUNT=0
  while IFS= read -r -d '' f; do
    if grep -q "⬜ Pending" "$f" 2>/dev/null; then
      echo "  $f"
      COUNT=$((COUNT + 1))
    fi
  done < <(find prompts -name "*.md" -print0 2>/dev/null)
  echo ""
  echo "  ${COUNT} artifact(s) pending review"
fi

# ── Stats ──────────────────────────────────────────────────────────────────────
if [[ "$MODE" == "stats" ]]; then
  echo "Prompt Artifact Statistics"
  echo "══════════════════════════"
  echo ""

  TOTAL_COMMITS=$(git log --grep="Prompt-Artifact:" --oneline 2>/dev/null | wc -l | tr -d ' ')
  echo "AI-assisted commits (all time): ${TOTAL_COMMITS}"

  for risk in LOW MEDIUM HIGH CRITICAL; do
    COUNT=$(git log --grep="AI-Risk: ${risk}" --oneline 2>/dev/null | wc -l | tr -d ' ')
    echo "  ${risk}: ${COUNT}"
  done

  echo ""
  if [[ -d "prompts" ]]; then
    TOTAL_ARTIFACTS=$(find prompts -name "*.md" 2>/dev/null | wc -l | tr -d ' ')
    PENDING=$(grep -rl "⬜ Pending" prompts 2>/dev/null | wc -l | tr -d ' ')
    APPROVED=$(grep -rl "APPROVED" prompts 2>/dev/null | wc -l | tr -d ' ')
    echo "Prompt artifacts: ${TOTAL_ARTIFACTS}"
    echo "  Pending review: ${PENDING}"
    echo "  Approved:       ${APPROVED}"
  else
    echo "No prompts/ directory found."
  fi
fi
