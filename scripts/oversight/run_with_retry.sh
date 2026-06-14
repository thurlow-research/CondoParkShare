#!/usr/bin/env bash
# run_with_retry.sh — shared timeout + retry wrapper for validators and gates.
#
# Source this file. Do not execute directly. Provides:
#   with_timeout   — run a command under the platform timeout binary
#   run_with_retry — retry a unit-of-work function with logging + audit
#
# DESIGN (issue #60): the unit of work is a CALLER-DEFINED FUNCTION, not a
# command string. timeout runs commands in a subprocess and cannot exec a bash
# function, so the function applies timeout to its own external command via
# with_timeout(). The helper owns the retry loop, logging, and audit emission —
# there is exactly one implementation. Callers own env setup (PYTHONPATH),
# output capture, and timeout wrapping of their specific binary.
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/run_with_retry.sh"
#
#   _my_unit() {                       # returns 0 ok / 124 timeout / other fail
#       with_timeout 60 some-binary --flag > "$tmpout" 2>/dev/null
#   }
#   run_with_retry "label" MAX_RETRIES REQUIRED _my_unit
#
# Arguments:
#   label        Human-readable name for log output
#   max_retries  Total attempts = max_retries + 1 (0 = try once, no retry)
#   required     "true" | "false" — whether exhaustion aborts the pipeline
#   run_fn       Name of a function that runs ONE attempt and returns its rc
#
# Return codes:
#   0  success
#   1  exhausted retries on a REQUIRED unit  → caller should fail the job
#   2  exhausted retries on an OPTIONAL unit → caller should log and skip
#
# Logging:
#   Per-retry:  ⟳ label attempt N/MAX — reason
#   Final:      ✔ succeeded on attempt N  |  ⏸ SKIPPED  |  ✘ FAILED
#   Audit:      appends a validator-failure event to audit/oversight-log.jsonl
#               on exhaustion (final outcome only, not per-attempt).

# ── Detect timeout binary (Linux: timeout, macOS brew: gtimeout) ──────────────
_TIMEOUT_BIN=""
if command -v timeout &>/dev/null; then
    _TIMEOUT_BIN="timeout"
elif command -v gtimeout &>/dev/null; then
    _TIMEOUT_BIN="gtimeout"
fi

# with_timeout TIMEOUT_SEC cmd [args...] — run cmd under timeout if available.
# Returns the command's rc (124 if timed out). TIMEOUT_SEC=0 disables timeout.
with_timeout() {
    local timeout_sec="$1"; shift
    if [[ -n "$_TIMEOUT_BIN" && "$timeout_sec" -gt 0 ]]; then
        "$_TIMEOUT_BIN" "$timeout_sec" "$@"
    else
        "$@"
    fi
}

run_with_retry() {
    local label="$1"
    local max_retries="$2"
    local required="$3"
    local run_fn="$4"

    local total_attempts=$(( max_retries + 1 ))
    local attempt=0
    local last_error=""
    local rc=0

    while [[ $attempt -lt $total_attempts ]]; do
        attempt=$(( attempt + 1 ))

        if [[ $attempt -gt 1 ]]; then
            printf "  \033[33m⟳\033[0m  %-28s attempt %d/%d — %s\n" \
                "$label" "$attempt" "$total_attempts" "$last_error"
            sleep 1  # brief pause before retry
        fi

        # Call the caller's unit-of-work function. `if` context lets us capture
        # rc without set -e aborting on a non-zero return.
        if "$run_fn"; then rc=0; else rc=$?; fi

        if [[ $rc -eq 0 ]]; then
            if [[ $attempt -gt 1 ]]; then
                printf "  \033[32m✔\033[0m  %-28s succeeded on attempt %d/%d\n" \
                    "$label" "$attempt" "$total_attempts"
            fi
            return 0
        elif [[ $rc -eq 124 ]]; then
            last_error="timeout"
        else
            last_error="exit ${rc}"
        fi
    done

    # ── All attempts exhausted ───────────────────────────────────────────────
    local outcome
    if [[ "$required" == "true" ]]; then
        printf "  \033[31m✘\033[0m  %-28s FAILED after %d attempt(s) (required — job fails)\n" \
            "$label" "$total_attempts"
        outcome="failed"
    else
        printf "  \033[33m⏸\033[0m  %-28s SKIPPED after %d attempt(s) (optional)\n" \
            "$label" "$total_attempts"
        outcome="skipped"
    fi

    # Append final outcome to the audit log (not per-attempt).
    if [[ -d "audit" ]]; then
        local ts
        ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date +"%Y-%m-%dT%H:%M:%SZ")
        echo "{\"event\":\"validator-failure\",\"validator\":\"${label}\",\"required\":${required},\"attempts\":${total_attempts},\"final_outcome\":\"${outcome}\",\"last_error\":\"${last_error}\",\"timestamp\":\"${ts}\"}" \
            >> "audit/oversight-log.jsonl" 2>/dev/null || true
    fi

    if [[ "$required" == "true" ]]; then
        return 1
    else
        return 2
    fi
}
