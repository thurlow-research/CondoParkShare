#!/usr/bin/env bash
# type_check.sh — static type checking gate (blocking).
#
# Runs mypy on Python files. Requires mypy and django-stubs for Django projects.
# Exit 0 = type-clean. Exit 1 = type errors found.
#
# Usage: ./type_check.sh file.py [file2.py ...]
#        ./type_check.sh --all
#
# Note: mypy may surface false positives on Django ORM code without
# django-stubs installed. Configure per-project in mypy.ini or pyproject.toml.

set -euo pipefail

GATES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/oversight/ensure_venv.sh
source "$GATES_DIR/../ensure_venv.sh"
# shellcheck source=scripts/oversight/gates/check_suspension.sh
source "$GATES_DIR/check_suspension.sh"
is_suspended "types" && { print_suspended "types"; exit 0; }

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
    # bash 3.2 (macOS default) has no `mapfile` — use a portable read loop.
    FILES=()
    while IFS= read -r _f; do
        [[ -n "$_f" ]] && FILES+=("$_f")
    done < <(find . -name "*.py" -not -path "./.venv/*" \
        -not -path "*/migrations/*" -not -path "./.git/*")
fi

if [[ ! -x "$VENV_BIN/mypy" ]]; then
    echo "SKIP: mypy not in oversight venv (run: ./scripts/oversight/ensure_venv.sh)"
    exit 0
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
    # No files specified and --all not set: default to scanning all Python files
    # rather than silently passing (a no-op pass is indistinguishable from a real pass).
    echo "type_check: no files specified — defaulting to --all (full project scan)"
    while IFS= read -r line; do FILES+=("$line"); done < <(find . -name "*.py" \
        -not -path "./.venv/*" -not -path "./scripts/oversight/.venv/*" \
        -not -path "./node_modules/*" -not -path "./.git/*")
    if [[ ${#FILES[@]} -eq 0 ]]; then
        echo "type_check: no Python files found in project — SKIP"
        exit 0
    fi
fi

echo "=== mypy ==="
if "$VENV_BIN/mypy" --ignore-missing-imports --no-error-summary "${FILES[@]}"; then
    echo "GATE PASS: no type errors"
    exit 0
else
    echo "GATE FAIL: type errors found — review before proceeding"
    exit 1
fi
