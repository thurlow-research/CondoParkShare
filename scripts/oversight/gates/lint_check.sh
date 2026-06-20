#!/usr/bin/env bash
# lint_check.sh — style and formatting gate (blocking).
#
# Runs flake8, black (check mode), and isort (check mode) on Python files.
# Exit 0 = all pass. Exit 1 = any failure; diff is printed for the human.
#
# Usage: ./lint_check.sh file.py [file2.py ...]
#        ./lint_check.sh --all        (check entire project)
#
# Part of the oversight pipeline cheap-gates stage (DECISIONS.md §D7).
# Run before risk assessment — no point scoring code that fails style checks.

set -euo pipefail

GATES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/oversight/ensure_venv.sh
source "$GATES_DIR/../ensure_venv.sh"
# shellcheck source=scripts/oversight/gates/check_suspension.sh
source "$GATES_DIR/check_suspension.sh"
is_suspended "lint" && { print_suspended "lint"; exit 0; }

PASS=0
FAIL=1

FILES=()
CHECK_ALL=false

for arg in "$@"; do
    if [[ "$arg" == "--all" ]]; then
        CHECK_ALL=true
    else
        FILES+=("$arg")
    fi
done

if $CHECK_ALL; then
    while IFS= read -r line; do FILES+=("$line"); done < <(find . -name "*.py" \
        -not -path "./.venv/*" -not -path "./scripts/oversight/.venv/*" \
        -not -path "./node_modules/*" -not -path "./.git/*")
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
    # No files specified and --all not set: default to scanning all Python files
    # rather than silently passing (a no-op pass is indistinguishable from a real pass).
    echo "lint_check: no files specified — defaulting to --all (full project scan)"
    while IFS= read -r line; do FILES+=("$line"); done < <(find . -name "*.py" \
        -not -path "./.venv/*" -not -path "./scripts/oversight/.venv/*" \
        -not -path "./node_modules/*" -not -path "./.git/*")
    if [[ ${#FILES[@]} -eq 0 ]]; then
        echo "lint_check: no Python files found in project — SKIP"
        exit $PASS
    fi
fi

ERRORS=0

echo "=== flake8 ==="
if [[ -x "$VENV_BIN/flake8" ]]; then
    if ! "$VENV_BIN/flake8" --max-line-length=120 --extend-ignore=E203,W503 "${FILES[@]}"; then
        ERRORS=$((ERRORS + 1))
    fi
else
    echo "SKIP: flake8 not in oversight venv (run: ./scripts/oversight/ensure_venv.sh)"
fi

echo ""
echo "=== black (format check) ==="
if [[ -x "$VENV_BIN/black" ]]; then
    if ! "$VENV_BIN/black" --check --diff --quiet "${FILES[@]}"; then
        ERRORS=$((ERRORS + 1))
        echo "Run: $VENV_BIN/black ${FILES[*]}"
    else
        echo "OK"
    fi
else
    echo "SKIP: black not in oversight venv (run: ./scripts/oversight/ensure_venv.sh)"
fi

echo ""
echo "=== isort (import order check) ==="
if [[ -x "$VENV_BIN/isort" ]]; then
    if ! "$VENV_BIN/isort" --check-only --diff "${FILES[@]}"; then
        ERRORS=$((ERRORS + 1))
        echo "Run: $VENV_BIN/isort ${FILES[*]}"
    else
        echo "OK"
    fi
else
    echo "SKIP: isort not in oversight venv (run: ./scripts/oversight/ensure_venv.sh)"
fi

echo ""
if [[ $ERRORS -gt 0 ]]; then
    echo "GATE FAIL: $ERRORS lint check(s) failed — fix before risk assessment"
    exit $FAIL
else
    echo "GATE PASS: all lint checks clean"
    exit $PASS
fi
