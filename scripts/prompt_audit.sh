#!/usr/bin/env bash
# prompt_audit.sh — query the prompt artifact audit trail
#
# Usage:
#   ./scripts/prompt_audit.sh              # list all AI-assisted commits
#   ./scripts/prompt_audit.sh --risk HIGH  # filter by risk level
#   ./scripts/prompt_audit.sh --pending    # show artifacts with Pending review status
#   ./scripts/prompt_audit.sh --stats      # summary statistics
#
# SPEC-338 (#338): this script is a launcher. It runs ONE `git log` per mode
# (collision-proof %x1e record / %x1f field separators) and pipes the output to
# scripts/oversight/prompt_audit_logic.py, which holds all parsing/aggregation
# logic. The shell never parses commit bodies; Python never spawns git.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGIC="$SCRIPT_DIR/oversight/prompt_audit_logic.py"

# Prefer the oversight venv python if present, else system python3 (same pattern
# as run_tests_inner_loop.sh) so the launcher works in and out of the venv.
VENV_PY="$SCRIPT_DIR/oversight/.venv/bin/python"
if [[ -x "$VENV_PY" ]]; then
  PYTHON="$VENV_PY"
else
  PYTHON="python3"
fi

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
      --pretty=format:"%x1e%h%x1f%ad%x1f%s" --date=short \
      | "$PYTHON" "$LOGIC" list --limit 40
  else
    git log --grep="Prompt-Artifact:" \
      --pretty=format:"%x1e%H%x1f%ad%x1f%s%x1f%b" --date=short \
      | "$PYTHON" "$LOGIC" list --limit 60
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
  "$PYTHON" "$LOGIC" pending --prompts-dir prompts
fi

# ── Stats ──────────────────────────────────────────────────────────────────────
if [[ "$MODE" == "stats" ]]; then
  echo "Prompt Artifact Statistics"
  echo "══════════════════════════"
  echo ""

  # Single git pass: UNION of Prompt-Artifact: and AI-Risk: commits (two --grep
  # = OR). Python derives total_commits from records carrying a Prompt-Artifact
  # trailer and by_risk from the AI-Risk trailer — exact parity with the legacy
  # two-pass counting, from ONE git invocation (SPEC-338 §3.2).
  git log --grep="Prompt-Artifact:" --grep="AI-Risk:" \
    --pretty=format:"%x1e%H%x1f%ad%x1f%s%x1f%b" --date=short \
    | "$PYTHON" "$LOGIC" stats --prompts-dir prompts
fi
