#!/usr/bin/env bash
# audit_log.sh — Bash facade over the canonical Python audit-record helper.
#                (SPEC-888 / TD-888 §2.2, P1)
#
# Sourced library, NOT an executable entry point. Defines two functions and has
# NO top-level side effects (no `set -e`, no execution at source time). Safe to
# source multiple times.
#
#   audit_write_event '<json-event>' [root]   # -> prints the record relpath
#   audit_read_stream [root]                  # -> prints the ordered JSONL stream
#
# Both delegate canonical serialization to the one Python serializer
# (scripts/oversight/lib/audit_log.py), so the Bash and Python writers produce
# byte-identical records by construction — the load-bearing cross-language
# parity requirement (SPEC R2 / T5). Two independent serializers (json.dumps vs
# jq) could diverge on number formatting, unicode escaping, or key collation;
# delegating to one serializer makes parity true rather than tested-and-hoped.
# `python3` is already a hard framework dependency, so this adds none.

# _audit_log_repo_root
# Walk up from this library's directory to the repo root (the dir that contains
# scripts/oversight/lib/audit_log.py) so `python3 -m` resolves the package.
_audit_log_repo_root() {
    local d
    d="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    while [ "$d" != "/" ]; do
        if [ -f "$d/scripts/oversight/lib/audit_log.py" ]; then
            printf '%s' "$d"
            return 0
        fi
        d="$(dirname "$d")"
    done
    printf '%s' "."
}

# audit_write_event '<json-event>' [root]
# Write the given event JSON as a write-once per-entry record under
# <root>/audit/log/. Prints the record path relative to audit/log.
audit_write_event() {
    local json="$1" root="${2:-.}" repo abs
    repo="$(_audit_log_repo_root)"
    # Resolve root to an absolute path while it still resolves against the
    # caller's CWD (we cd into the repo to run `python3 -m`).
    if abs="$(cd "$root" 2>/dev/null && pwd)"; then
        root="$abs"
    fi
    printf '%s' "$json" | ( cd "$repo" && python3 -m scripts.oversight.lib.audit_log write "$root" )
}

# audit_read_stream [root]
# Print the legacy chronological JSONL event stream reconstructed from
# <root>/audit/log/. Missing/empty dir -> empty stream, exit 0.
audit_read_stream() {
    local root="${1:-.}" repo abs
    repo="$(_audit_log_repo_root)"
    if abs="$(cd "$root" 2>/dev/null && pwd)"; then
        root="$abs"
    fi
    ( cd "$repo" && python3 -m scripts.oversight.lib.audit_log read "$root" )
}
