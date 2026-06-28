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
# shellcheck source=/dev/null
[[ -f "$_RETRY_HELPER" ]] && source "$_RETRY_HELPER"

# ── Python delegation (HOS#337) ──────────────────────────────────────────────
# The suspension grammar (_SUSPENDED_RE) and the gate-suspended audit-event JSON
# are owned ONLY by suspension_manager.py. This bash helper no longer carries a
# copy of the regex — that duplication caused HOS#105. We shell out to the
# manager per invocation (acceptable per the architect ruling; no caching).
_SUSP_MGR="$(dirname "${BASH_SOURCE[0]}")/../suspension_manager.py"
# OVERSIGHT_PYTHON is set by ensure_venv.sh when a venv exists; bare python3
# otherwise — same convention as secret_scan.sh / run_validators.sh.
_SUSP_PY="${OVERSIGHT_PYTHON:-python3}"

_find_suspension_file() {
    # Locate contract/gate-suspension.md relative to the repo root
    local repo_root
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null || echo ".")
    echo "${repo_root}/contract/gate-suspension.md"
}

is_suspended() {
    local gate="$1"
    # Delegate the grammar match to suspension_manager.py (--is-suspended): the
    # canonical _SUSPENDED_RE lives there, so the two-parser divergence that
    # caused HOS#105 cannot recur. FAIL-CLOSED: only an explicit exit 0 from the
    # manager means "suspended/skip". Any other outcome (python missing, error,
    # unexpected code) returns 1 so the gate RUNS. Failing open (returning 0 on
    # python failure) is forbidden — a missing interpreter must never silently
    # bypass a safety gate.
    if ! command -v "$_SUSP_PY" &>/dev/null; then
        echo "check_suspension: $_SUSP_PY not found — running gate (fail-closed)" >&2
        return 1
    fi
    "$_SUSP_PY" "$_SUSP_MGR" --is-suspended "$gate"
    local rc=$?
    case "$rc" in
        0) return 0 ;;   # suspended
        1) return 1 ;;   # not suspended
        *)
            echo "check_suspension: suspension_manager.py exited $rc for '$gate' — running gate (fail-closed)" >&2
            return 1
            ;;
    esac
}

# A suspended gate is a bypassed safety check. It MUST leave an append-only
# audit trail, not just a console notice — otherwise a skipped gate is
# invisible after the fact and the bypass is unaccountable. (HOS#106)
_emit_suspension_audit() {
    local gate="$1" authorized_by="$2"
    # Delegate JSON construction + append to suspension_manager.py --emit-audit.
    # The manager owns the field set/order (event,gate,authorized_by,timestamp)
    # and the audit/-absent no-op guard. Best-effort: emission failure must not
    # block a suspended gate, but when audit/ exists and python is available the
    # event IS written (the audit-trail requirement of HOS#106 is preserved).
    command -v "$_SUSP_PY" &>/dev/null || return 0
    "$_SUSP_PY" "$_SUSP_MGR" --emit-audit --gate "$gate" \
        --authorized-by "$authorized_by" 2>/dev/null || true
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
