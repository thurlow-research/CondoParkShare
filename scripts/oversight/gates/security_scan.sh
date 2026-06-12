#!/usr/bin/env bash
# security_scan.sh — security static analysis gate (blocking on HIGH).
#
# Runs bandit for security issues and pip-audit for dependency vulnerabilities.
#
# HIGH severity bandit findings = gate failure (blocking).
# MEDIUM findings are collected but do NOT block here — they feed into the
# static_analysis.py risk validator score instead.
#
# Exit 0 = no HIGH findings. Exit 1 = HIGH findings or dependency vulnerabilities.
#
# Usage: ./security_scan.sh file.py [file2.py ...]
#        ./security_scan.sh --all

set -euo pipefail

GATES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/oversight/ensure_venv.sh
source "$GATES_DIR/../ensure_venv.sh"

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
    mapfile -t FILES < <(find . -name "*.py" -not -path "./.venv/*" \
        -not -path "./.git/*")
fi

ERRORS=0

# --- bandit: HIGH severity only (blocking) ---
echo "=== bandit (HIGH severity) ==="
if [[ -x "$VENV_BIN/bandit" ]]; then
    if [[ ${#FILES[@]} -gt 0 ]]; then
        # -l: LOW, -ll: MEDIUM+, -lll: HIGH only
        # We run HIGH only for the gate; MEDIUM is handled by static_analysis.py
        BANDIT_OUT=$("$VENV_BIN/bandit" -f json -lll "${FILES[@]}" 2>/dev/null || true)
        HIGH_COUNT=$(echo "$BANDIT_OUT" | PYTHONSAFEPATH=1 "$OVERSIGHT_PYTHON" -c \
            "import json,sys; d=json.load(sys.stdin); \
             print(len([r for r in d.get('results',[]) if r.get('issue_severity')=='HIGH']))" 2>/dev/null || echo "0")
        if [[ "$HIGH_COUNT" -gt 0 ]]; then
            echo "GATE FAIL: $HIGH_COUNT HIGH severity bandit finding(s)"
            echo "$BANDIT_OUT" | PYTHONSAFEPATH=1 "$OVERSIGHT_PYTHON" -c \
                "import json,sys; [print(f\"  {r['filename']}:{r['line_number']} [{r['test_id']}] {r['issue_text']}\") \
                 for r in json.load(sys.stdin).get('results',[]) if r.get('issue_severity')=='HIGH']" 2>/dev/null || true
            ERRORS=$((ERRORS + 1))
        else
            echo "OK: no HIGH severity findings"
        fi
    fi
else
    echo "SKIP: bandit not in oversight venv (run: ./scripts/oversight/ensure_venv.sh)"
fi

# --- pip-audit: dependency vulnerabilities ---
echo ""
echo "=== pip-audit (dependency vulnerabilities) ==="
if [[ -x "$VENV_BIN/pip-audit" ]]; then
    if ! "$VENV_BIN/pip-audit" --progress-spinner off -q 2>&1; then
        echo "GATE FAIL: vulnerable dependencies found — update before proceeding"
        ERRORS=$((ERRORS + 1))
    else
        echo "OK: no known vulnerabilities"
    fi
else
    echo "SKIP: pip-audit not in oversight venv (run: ./scripts/oversight/ensure_venv.sh)"
fi

echo ""
if [[ $ERRORS -gt 0 ]]; then
    echo "GATE FAIL: $ERRORS security check(s) failed"
    exit 1
else
    echo "GATE PASS: no blocking security issues"
    exit 0
fi
