#!/usr/bin/env bash
# run_framework_validation.sh — run the full framework validation suite.
#
# Run before committing any change to agent files, pipeline docs, or
# the framework scripts themselves.
#
# Sequence:
#   1. Static check  (check_agents_static.sh) — fast, no AI, blocks on findings
#   2. AI review     (validate_agents.sh)      — agy + codex, blocks on blocking findings
#
# Usage:
#   ./scripts/framework/run_framework_validation.sh
#   ./scripts/framework/run_framework_validation.sh --static-only   # skip AI review
#   ./scripts/framework/run_framework_validation.sh --changed-only  # only changed files vs HEAD~1
#   ./scripts/framework/run_framework_validation.sh --skip-codex    # agy only
#   ./scripts/framework/run_framework_validation.sh --skip-agy      # codex only
#
# Exit codes:
#   0 — all checks pass
#   1 — findings that block commit
#   2 — tooling error

set -euo pipefail

STATIC_ONLY=false
SKIP_DOCS=false
SKIP_COMPLIANCE=false
CHANGED_ONLY=""
SKIP_CODEX=""
SKIP_AGY=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --static-only)      STATIC_ONLY=true;             shift ;;
        --skip-docs)        SKIP_DOCS=true;               shift ;;
        --skip-compliance)  SKIP_COMPLIANCE=true;         shift ;;
        --changed-only)     CHANGED_ONLY="--changed-only"; shift ;;
        --skip-codex)       SKIP_CODEX="--skip-codex";    shift ;;
        --skip-agy)         SKIP_AGY="--skip-agy";        shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "══════════════════════════════════════════════════"
echo "  Framework Validation Suite"
echo "══════════════════════════════════════════════════"
echo ""

# ── Phase 1: Static check ────────────────────────────────────────────────────
echo "Phase 1 — Static consistency check"
echo ""
if ! bash "$SCRIPT_DIR/check_agents_static.sh"; then
    echo ""
    echo "  Static check FAILED. Fix findings above before running AI review."
    exit 1
fi
echo ""

if $STATIC_ONLY; then
    echo "  --static-only: skipping AI review"
    echo ""
    echo "  PASS — static checks clean"
    exit 0
fi

# ── Phase 2: Agent semantic review ──────────────────────────────────────────
echo "Phase 2 — Agent semantic review (agy + codex)"
echo ""

AI_ARGS=""
[[ -n "$CHANGED_ONLY" ]] && AI_ARGS="$AI_ARGS $CHANGED_ONLY"
[[ -n "$SKIP_CODEX"   ]] && AI_ARGS="$AI_ARGS $SKIP_CODEX"
[[ -n "$SKIP_AGY"     ]] && AI_ARGS="$AI_ARGS $SKIP_AGY"

if ! bash "$SCRIPT_DIR/validate_agents.sh" $AI_ARGS; then
    echo ""
    echo "  Agent semantic review found blocking issues."
    echo "  Invoke framework-validator agent to triage and fix before committing."
    exit 1
fi

echo ""

# ── Phase 3: Documentation coverage review ──────────────────────────────────
if $SKIP_DOCS; then
    echo "Phase 3 — Documentation coverage review (skipped via --skip-docs)"
else
    echo "Phase 3 — Documentation coverage review (agy + codex)"
    echo ""

    DOC_ARGS=""
    [[ -n "$SKIP_CODEX" ]] && DOC_ARGS="$DOC_ARGS $SKIP_CODEX"

    if ! bash "$SCRIPT_DIR/validate_docs.sh" $DOC_ARGS; then
        echo ""
        echo "  Documentation coverage review found issues."
        echo "  Invoke doc-validator agent to review and fix before committing."
        exit 1
    fi
fi

# ── Phase 4: Spec compliance ─────────────────────────────────────────────────
if $SKIP_COMPLIANCE; then
    echo "Phase 4 — Spec compliance check (skipped via --skip-compliance)"
else
    echo "Phase 4 — Spec compliance check (governance requirements + decisions)"
    echo ""

    COMP_ARGS=""
    [[ -n "$SKIP_CODEX" ]] && COMP_ARGS="$COMP_ARGS $SKIP_CODEX"

    if ! bash "$SCRIPT_DIR/validate_spec_compliance.sh" $COMP_ARGS; then
        echo ""
        echo "  Spec compliance failures found."
        echo "  Invoke spec-compliance-validator agent to triage and fix."
        exit 1
    fi
fi

echo ""
echo "══════════════════════════════════════════════════"
echo "  All framework checks passed — clear to commit."
echo "══════════════════════════════════════════════════"
exit 0
