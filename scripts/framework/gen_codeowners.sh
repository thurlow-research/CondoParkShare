#!/usr/bin/env bash
# gen_codeowners.sh — generate .github/CODEOWNERS from the canonical protected
# surface list (scripts/framework/protected_surfaces.txt), so CODEOWNERS and the
# require_human_approval status check can never drift (AGENT-IDENTITY.md §9).
#
# CODEOWNERS is the STATIC half of the §9 human gate: with branch protection's
# "Require review from Code Owners" on, a protected-surface PR needs an approval
# from the listed owner — which must be a HUMAN (the bots are deliberately NOT
# code owners, so a bot approval can't satisfy it). The require-human-approval
# workflow is the DYNAMIC half (re-derives the touched surfaces from the diff).
#
# Usage:
#   ./scripts/framework/gen_codeowners.sh [OWNER_HANDLE]
#   OWNER_HANDLE defaults to @<repo owner> (gh). Pass e.g. @your-username.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

SURFACES="scripts/framework/protected_surfaces.txt"
OUT=".github/CODEOWNERS"
[[ -f "$SURFACES" ]] || { echo "missing $SURFACES" >&2; exit 2; }

OWNER="${1:-}"
if [[ -z "$OWNER" ]]; then
  owner_login="$(gh repo view --json owner -q .owner.login 2>/dev/null || true)"
  OWNER="@${owner_login:-OWNER}"
fi
[[ "$OWNER" == @* ]] || OWNER="@$OWNER"

{
  echo "# CODEOWNERS — GENERATED from scripts/framework/protected_surfaces.txt"
  echo "# Do not edit by hand: run scripts/framework/gen_codeowners.sh to regenerate."
  echo "#"
  echo "# Every protected surface (AGENT-IDENTITY.md §9) requires an approval from a"
  echo "# HUMAN code owner. The machine accounts are deliberately NOT listed here, so a"
  echo "# bot approval cannot satisfy CODEOWNERS. Enable enforcement in branch"
  echo "# protection: 'Require review from Code Owners' (see docs/MACHINE-ACCOUNTS-SETUP.md)."
  echo "#"
  echo "# Owner: ${OWNER}"
  echo ""
  while IFS= read -r line; do
    line="${line%%#*}"; line="$(echo "$line" | xargs || true)"
    [[ -z "$line" ]] && continue
    # Map a protected glob to a CODEOWNERS pattern (gitignore-style, root-anchored).
    if [[ "$line" == *"/**" ]]; then
      pat="/${line%/**}/"                 # dir/** → /dir/
    else
      pat="/${line}"                      # exact file → /path/file
    fi
    printf '%-42s %s\n' "$pat" "$OWNER"
  done < "$SURFACES"
} > "$OUT"

echo "Generated $OUT (owner ${OWNER}) from $SURFACES"
