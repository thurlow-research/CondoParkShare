#!/usr/bin/env bash
# check_suspension.sh — shared helper for gate suspension checks.
#
# Source this file in any gate script, then call is_suspended() before
# running checks. If suspended, the gate should print a warning and exit 0
# (non-blocking) rather than failing.
#
# The suspension manifest is contract/gate-suspension.md — a human-only file
# that lists which gates/roles are currently suspended. Agents may not create
# or modify this file.
#
# Usage in a gate script:
#   source "$(dirname "${BASH_SOURCE[0]}")/check_suspension.sh"
#   is_suspended "lint" && { print_suspended "lint"; exit 0; }

_SUSPENSION_FILE=""

# ── Source retry helper if available ─────────────────────────────────────────
_RETRY_HELPER="$(dirname "${BASH_SOURCE[0]}")/../run_with_retry.sh"
[[ -f "$_RETRY_HELPER" ]] && source "$_RETRY_HELPER"

_find_suspension_file() {
    # Locate contract/gate-suspension.md relative to the repo root
    local repo_root
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null || echo ".")
    echo "${repo_root}/contract/gate-suspension.md"
}

is_suspended() {
    local gate="$1"
    [[ -z "$_SUSPENSION_FILE" ]] && _SUSPENSION_FILE=$(_find_suspension_file)
    [[ -f "$_SUSPENSION_FILE" ]] || return 1
    grep -q "^SUSPENDED: ${gate}$" "$_SUSPENSION_FILE" 2>/dev/null
}

print_suspended() {
    local gate="$1"
    [[ -z "$_SUSPENSION_FILE" ]] && _SUSPENSION_FILE=$(_find_suspension_file)
    local authorized_by
    authorized_by=$(grep "^Authorized by:" "$_SUSPENSION_FILE" 2>/dev/null | head -1 | cut -d: -f2- | xargs)
    echo ""
    echo "  ⏸  GATE SUSPENDED: ${gate}"
    echo "     Authorized by: ${authorized_by:-unknown}"
    echo "     See contract/gate-suspension.md to view the full suspension record."
    echo "     Remove 'SUSPENDED: ${gate}' from that file to re-enable this gate."
    echo ""
}
