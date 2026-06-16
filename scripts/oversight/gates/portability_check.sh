#!/usr/bin/env bash
# portability_check.sh — flag machine-specific absolute paths in source (blocking).
#
# Catches hardcoded developer home-directory paths embedded in source or config
# files — paths that silently break on any host other than the original author's.
#
# Matches:
#   /Users/<name>/...       macOS home dirs
#   /home/<name>/...        Linux home dirs (excludes /home/runner — CI is OK)
#   C:/Users/<name>/...     Windows home dirs (the backslash-separator form is what the pattern matches)
#
# Scans Python, shell, TOML, ini, cfg files — broader than the portability_check.py
# validator (which scores risk) — this gate is the blocking Phase-1 check.
#
# Exit 0 = no machine-specific paths found. Exit 1 = found.
#
# Usage: ./portability_check.sh file.py [file2.py ...]
#        ./portability_check.sh --all

set -euo pipefail

_GATES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/oversight/gates/check_suspension.sh
source "$_GATES_DIR/check_suspension.sh"
is_suspended "portability" && { print_suspended "portability"; exit 0; }

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

if $CHECK_ALL || [[ ${#FILES[@]} -eq 0 ]]; then
    # bash 3.2 (macOS default) has no `mapfile` — use a portable read loop.
    FILES=()
    while IFS= read -r _f; do
        [[ -n "$_f" ]] && FILES+=("$_f")
    done < <(find . -type f \
        \( -name '*.py' -o -name '*.sh' -o -name '*.toml' -o -name '*.cfg' -o -name '*.ini' \) \
        -not -path "./.venv/*" -not -path "./.git/*" -not -path "./node_modules/*")
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "portability_check: no files to check"
    exit $PASS
fi

echo "=== portability check (machine-specific absolute paths) ==="

# /home/runner is excluded — GitHub Actions / standard CI runner paths are portable.
PAT='(/Users/[A-Za-z0-9._-]+/|/home/(?!runner/)[A-Za-z0-9._-]+/|[A-Za-z]:\\Users\\)'

if HITS=$(grep -nEHP "$PAT" "${FILES[@]}" 2>/dev/null); then
    echo "GATE FAIL: machine-specific absolute path(s) found"
    echo ""
    echo "$HITS"
    echo ""
    echo "Use BASE_DIR / Path(__file__).parent / env vars instead of hardcoded home paths."
    echo "If this is a spec_from_file_location workaround, fix the root naming collision instead."
    exit $FAIL
fi

echo "GATE PASS: no machine-specific paths found"
exit $PASS
