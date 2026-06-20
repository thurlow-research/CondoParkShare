#!/usr/bin/env bash
# bootstrap/validate_setup.sh — HOS preflight check
#
# Run BEFORE invoking Claude. Zero token cost. Fail-fast if setup is broken.
# Exit 0 = setup OK, proceed. Exit 1 = setup broken, block Claude invocation.
#
# Usage:
#   bash bootstrap/validate_setup.sh                  # check from cwd
#   bash bootstrap/validate_setup.sh --quiet          # suppress OK output (cron use)
#   bash bootstrap/validate_setup.sh --repo /path     # explicit repo root

set -euo pipefail

QUIET=false
REPO_ROOT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet)  QUIET=true; shift ;;
    --repo)   REPO_ROOT="$2"; shift 2 ;;
    *)        echo "Usage: $0 [--quiet] [--repo PATH]" >&2; exit 1 ;;
  esac
done

[[ -z "$REPO_ROOT" ]] && REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo ".")"

fail() { echo "SETUP FAIL: $*" >&2; exit 1; }
ok()   { "$QUIET" || echo "  ✔  $*"; }

echo "=== HOS preflight check ($(date -u '+%Y-%m-%dT%H:%M:%SZ')) ==="

# ── 1. Required specialist agents ─────────────────────────────────────────────
REQUIRED_AGENTS=(
  architect pm-agent technical-design
  coder code-reviewer security-reviewer
  oversight-evaluator worker overseer
)

AGENTS_DIR="$REPO_ROOT/.claude/agents"
[[ -d "$AGENTS_DIR" ]] || fail ".claude/agents/ directory missing — run hos_install.sh"

for agent in "${REQUIRED_AGENTS[@]}"; do
  [[ -f "$AGENTS_DIR/${agent}.md" ]] \
    || fail ".claude/agents/${agent}.md missing — run hos_install.sh"
done
ok "All required agents present (${#REQUIRED_AGENTS[@]} checked)"

# ── 2. Bootstrap scripts ───────────────────────────────────────────────────────
[[ -f "$REPO_ROOT/bootstrap/get_app_token.sh" ]] \
  || fail "bootstrap/get_app_token.sh missing"
[[ -x "$REPO_ROOT/bootstrap/get_app_token.sh" ]] \
  || fail "bootstrap/get_app_token.sh not executable"
ok "bootstrap/get_app_token.sh present and executable"

# ── 3. Config / credentials ───────────────────────────────────────────────────
# Check in priority order: project-level → global
PROJECT_CONFIG="$(cd "$REPO_ROOT/../.." 2>/dev/null && pwd)/.config/hos/apps.env"
GLOBAL_CONFIG="${HOS_CONFIG_DIR:-$HOME/.config/hos}/apps.env"

if [[ -f "$PROJECT_CONFIG" ]]; then
  ok "Config: project-level $PROJECT_CONFIG"
elif [[ -f "$GLOBAL_CONFIG" ]]; then
  ok "Config: global $GLOBAL_CONFIG"
else
  fail "apps.env not found at project-level or global — run hos_bootstrap.sh"
fi

# ── 4. Git repo sanity ────────────────────────────────────────────────────────
# Verify a git remote exists. If HOS_EXPECTED_REMOTE is set, also verify the
# remote matches — useful for HOS self-development but intentionally optional
# so consumer projects with their own remotes pass without false warnings.
REMOTE=$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || echo "")
if [[ -z "$REMOTE" ]]; then
  fail "No git remote configured — is this a proper clone?"
elif [[ -n "${HOS_EXPECTED_REMOTE:-}" ]] && ! echo "$REMOTE" | grep -qF -- "$HOS_EXPECTED_REMOTE"; then
  echo "  WARN: remote is $REMOTE (expected $HOS_EXPECTED_REMOTE)" >&2
  ok "Git remote: $REMOTE (no match against HOS_EXPECTED_REMOTE)"
else
  ok "Git remote: $REMOTE"
fi

echo "=== Preflight PASSED ==="
exit 0
