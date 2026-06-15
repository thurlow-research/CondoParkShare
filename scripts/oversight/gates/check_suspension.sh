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
    # Grammar MUST stay in sync with _SUSPENDED_RE in suspension_manager.py:
    # an active line is `SUSPENDED: <gate>` optionally followed by [pinned]
    # and/or `review-by: YYYY-MM-DD` flags. The old end-anchored bare match
    # ("^SUSPENDED: gate$") rejected those flagged forms — so a suspension the
    # manager/census reported as ACTIVE was silently IGNORED here and the gate
    # kept running. Two parsers, one grammar. (HOS#105)
    grep -Eq "^SUSPENDED:[[:space:]]*${gate}([[:space:]]+\[pinned\]|[[:space:]]+review-by:[[:space:]]*[0-9]{4}-[0-9]{2}-[0-9]{2})*[[:space:]]*$" \
        "$_SUSPENSION_FILE" 2>/dev/null
}

# A suspended gate is a bypassed safety check. It MUST leave an append-only
# audit trail, not just a console notice — otherwise a skipped gate is
# invisible after the fact and the bypass is unaccountable. (HOS#106)
_emit_suspension_audit() {
    local gate="$1" authorized_by="$2"
    local repo_root audit_dir ts
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null || echo ".")
    audit_dir="${repo_root}/audit"
    [[ -d "$audit_dir" ]] || return 0
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date +"%Y-%m-%dT%H:%M:%SZ")
    authorized_by=${authorized_by//\"/\'}   # keep the JSON well-formed
    printf '{"event":"gate-suspended","gate":"%s","authorized_by":"%s","timestamp":"%s"}\n' \
        "$gate" "$authorized_by" "$ts" >> "${audit_dir}/oversight-log.jsonl" 2>/dev/null || true
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
    # Every skip emits a gate-suspended event to the append-only audit log.
    _emit_suspension_audit "$gate" "${authorized_by:-unknown}"
}
