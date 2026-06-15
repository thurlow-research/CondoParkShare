#!/usr/bin/env bash
# collection_integrity.sh — test-suite collection-integrity gate (blocking on errors).
#
# Runs the test collector ONLY (no test execution) to verify the whole suite
# still *imports*. This catches the escape behind #157: a change deletes or
# renames a module and orphans imports in OTHER test files, leaving the suite
# erroring on collection (ModuleNotFoundError) on every run since — undetected,
# because the per-step unit/system reviewers only touch the *changed* files and
# never collect the full suite. A red suite then reads as "expected red".
#
# This gate runs `pytest --collect-only` over the whole repo and FAILS if
# collection errors (import errors) exist — independent of which files changed.
# It does NOT run tests (fast, no side effects).
#
# Skips gracefully when pytest is unavailable or no tests are collected, so it
# is a no-op for non-pytest projects. Other stacks (jest, go test, etc.) should
# provide their own collection check — see CUSTOMIZATION.md.
#
# Python resolution order:
#   1. $COLLECTION_PYTHON env var (explicit override)
#   2. .venv/bin/python  (project venv at repo root)
#   3. venv/bin/python
#   4. python3 (system — last resort, may lack project deps)
#
# Exit 0 = suite collects cleanly, or N/A (no pytest / no tests).
# Exit 1 = collection errors (the suite is broken and the pipeline must stop).
#
# Usage: ./collection_integrity.sh
#        COLLECTION_PYTHON=/path/to/python ./collection_integrity.sh

set -euo pipefail

_GATES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/oversight/gates/check_suspension.sh
source "$_GATES_DIR/check_suspension.sh"
is_suspended "collection_integrity" && { print_suspended "collection_integrity"; exit 0; }

echo "=== test collection integrity (pytest --collect-only) ==="

# Resolve Python — prefer the project's own venv so all test deps are present.
if [[ -n "${COLLECTION_PYTHON:-}" ]]; then
    PY="$COLLECTION_PYTHON"
elif [[ -x ".venv/bin/python" ]]; then
    PY=".venv/bin/python"
elif [[ -x "venv/bin/python" ]]; then
    PY="venv/bin/python"
else
    PY="python3"
fi

# Is pytest importable for the resolved interpreter? If not, this gate is N/A.
if ! "$PY" -m pytest --version >/dev/null 2>&1; then
    echo "SKIP: pytest not available for $PY (not a pytest project)"
    exit 0
fi

# Collect only — no test execution. Capture output and the exit code.
# pytest exit codes: 0 = collected OK, 2 = collection/usage error,
# 5 = no tests collected. We treat 2 as a hard failure, 5 as N/A.
set +e
COLLECT_OUT="$("$PY" -m pytest --collect-only -q 2>&1)"
COLLECT_RC=$?
set -e

case "$COLLECT_RC" in
    0)
        echo "PASS: full suite collects cleanly"
        exit 0
        ;;
    5)
        echo "SKIP: pytest collected no tests (no suite to check)"
        exit 0
        ;;
    *)
        echo "FAIL: test suite has collection errors (exit $COLLECT_RC) —"
        echo "      a module was likely deleted/renamed and left orphaned imports."
        echo "----- pytest --collect-only output (errors) -----"
        # Surface the import errors, not the full collected-item list.
        printf '%s\n' "$COLLECT_OUT" | grep -iE "error|Error|cannot import|ModuleNotFound|no module named" | head -40
        echo "-------------------------------------------------"
        exit 1
        ;;
esac
