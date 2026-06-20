#!/usr/bin/env bash
# step_range.sh — shared step commit-range helper (SPEC-220 BC-220-5).
#
# Sourced library, NOT an executable entry point. Defines one function,
# get_step_range, consumed by:
#   - oversight-evaluator's Phase 1 BASE_SHA derivation (SPEC-220 R2)
#   - run_second_review.sh's --step N path (SPEC-219, cross-spec binding)
#
# Design contract: docs/v0.4.0/TECHNICAL-DESIGN-220-step-head-final.md §2.1
#
# This file MUST NOT have top-level side effects (no `set -e`, no execution at
# source time). It only defines functions. Safe to source multiple times.
#
# Portability (BC-220-3): step scoping uses the field-delimiter pattern
#   grep -E '"step":'"$N"'[,}]'
# NOT a `\b` word boundary (which fails on BSD/macOS). The `[,}]` after the step
# number is portable and avoids prefix collisions (step 1 vs step 12).
#
# Compact JSONL (BC-220-4): events are single-line compact JSON, so
# "head_sha":"<sha>" has no internal spaces — the sed extraction is stable.

# _shr_preferred_head <step_n> <log_path>
# Resolve the preferred head_sha for a SINGLE step N: prefer step-head-final
# (post-panel) over step-head (pre-panel). Print the sha, or nothing if neither
# event exists for step N. Internal helper.
_shr_preferred_head() {
    local n="$1" log="$2" sha=""

    [ -f "$log" ] || { printf ''; return 0; }

    # 1. step-head-final for step N (preferred).
    sha=$(grep -h '"event":"step-head-final"' "$log" 2>/dev/null \
        | grep -E '"step":'"$n"'[,}]' \
        | tail -1 \
        | sed -n 's/.*"head_sha":"\([0-9a-f]*\)".*/\1/p')

    # 2. Fall back to step-head for step N.
    if [ -z "$sha" ]; then
        sha=$(grep -h '"event":"step-head"' "$log" 2>/dev/null \
            | grep -E '"step":'"$n"'[,}]' \
            | tail -1 \
            | sed -n 's/.*"head_sha":"\([0-9a-f]*\)".*/\1/p')
    fi

    printf '%s' "$sha"
}

# get_step_range <step_n> [log_path]
# Print "BASE_SHA..HEAD_SHA" for step N, where HEAD_SHA is step N's preferred
# head and BASE_SHA is step (N-1)'s preferred head (final-over-plain for both).
#
# Behavior (BC-220-5):
#   - Prefer step-head-final over step-head for the same step.
#   - If step N has NO event at all -> print empty string (NOT an error), exit 0.
#   - If step N-1 has no event (e.g. step 1) -> BASE is empty -> "..HEAD_SHA";
#     the caller owns the merge-base fallback for an empty base.
#   - Missing log file -> empty output, exit 0.
get_step_range() {
    local step_n="$1"
    local log="${2:-audit/oversight-log.jsonl}"
    local head base

    head=$(_shr_preferred_head "$step_n" "$log")

    # No event for step N at all -> empty string, not an error.
    if [ -z "$head" ]; then
        printf ''
        return 0
    fi

    base=$(_shr_preferred_head "$((step_n - 1))" "$log")

    printf '%s..%s' "$base" "$head"
    return 0
}
