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
# shellcheck source=scripts/oversight/gates/check_suspension.sh
source "$GATES_DIR/check_suspension.sh"
is_suspended "security" && { print_suspended "security"; exit 0; }

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
        -not -path "./.git/*")
fi

ERRORS=0
GATE_TIMEOUT="${GATE_TIMEOUT:-120}"   # seconds per tool invocation
GATE_RETRIES="${GATE_RETRIES:-2}"     # retries on crash/timeout

# --- bandit: HIGH severity only (blocking) ---
echo "=== bandit (HIGH severity) ==="
if [[ -x "$VENV_BIN/bandit" ]]; then
    if [[ ${#FILES[@]} -gt 0 ]]; then
        BANDIT_TMP=$(mktemp /tmp/bandit_XXXXXX)
        # Unit of work: run bandit under the configured timeout, capture to temp.
        # bandit exits 1 when it FINDS issues — that is a successful scan for us
        # (we parse the JSON afterward). Only timeout (124) or a bandit error
        # (rc >= 2) counts as a failed attempt worth retrying.
        _run_bandit() {
            with_timeout "$GATE_TIMEOUT" "$VENV_BIN/bandit" -f json -lll "${FILES[@]}" > "$BANDIT_TMP" 2>/dev/null
            local brc=$?
            [[ $brc -eq 0 || $brc -eq 1 ]] && return 0
            return "$brc"
        }
        if run_with_retry "bandit" "$GATE_RETRIES" "true" _run_bandit; then
            BANDIT_OUT=$(cat "$BANDIT_TMP")
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
        else
            echo "GATE FAIL: bandit did not complete after retries"
            ERRORS=$((ERRORS + 1))
        fi
        rm -f "$BANDIT_TMP"
        unset -f _run_bandit
    fi
else
    echo "SKIP: bandit not in oversight venv (run: ./scripts/oversight/ensure_venv.sh)"
fi

# --- pip-audit: dependency vulnerabilities (network-dependent — optional if it hangs) ---
echo ""
echo "=== pip-audit (dependency vulnerabilities) ==="
if [[ -x "$VENV_BIN/pip-audit" ]]; then
    PIP_AUDIT_TMP=$(mktemp /tmp/pip_audit_XXXXXX)
    # Unit of work: run pip-audit under the configured timeout, capture JSON.
    # pip-audit exits 1 when it FINDS vulnerabilities — that is a SUCCESSFUL scan
    # for us, NOT an execution failure (#672). We must separate the two:
    #   • completed scan (rc 0 or 1 AND parseable JSON) → parse, block on findings
    #   • timeout / network error (no parseable JSON)    → retry, then warn+skip
    # Without this split, run_with_retry treats the vulns-found rc=1 as a crash,
    # exhausts retries, and — required=false — fails OPEN (gate exits 0 with real
    # vulnerabilities present). Mirrors the bandit unit-of-work above.
    _run_pip_audit() {
        with_timeout "$GATE_TIMEOUT" "$VENV_BIN/pip-audit" --progress-spinner off \
            --format json > "$PIP_AUDIT_TMP" 2>/dev/null
        local prc=$?
        # A completed scan emits parseable JSON regardless of rc 0/1. Require both
        # an expected rc and valid JSON before declaring the attempt a success.
        if [[ $prc -eq 0 || $prc -eq 1 ]] && PYTHONSAFEPATH=1 "$OVERSIGHT_PYTHON" -c \
            "import json,sys; json.load(open(sys.argv[1]))" "$PIP_AUDIT_TMP" 2>/dev/null; then
            return 0
        fi
        # Preserve a timeout rc so run_with_retry logs "timeout"; otherwise surface
        # the execution failure rc (network error, resolver crash, …).
        [[ $prc -eq 124 ]] && return 124
        return "${prc:-1}"
    }
    if run_with_retry "pip-audit" "$GATE_RETRIES" "false" _run_pip_audit; then
        VULN_COUNT=$(PYTHONSAFEPATH=1 "$OVERSIGHT_PYTHON" -c \
            "import json,sys; d=json.load(open(sys.argv[1])); \
             print(sum(len(dep.get('vulns',[])) for dep in d.get('dependencies',[])))" \
             "$PIP_AUDIT_TMP" 2>/dev/null || echo "0")
        if [[ "$VULN_COUNT" -gt 0 ]]; then
            echo "GATE FAIL: $VULN_COUNT dependency vulnerability(ies) found"
            PYTHONSAFEPATH=1 "$OVERSIGHT_PYTHON" -c \
                "import json,sys; \
                 [print(f\"  {dep['name']} {dep.get('version','?')} [{v.get('id','?')}] fix: {','.join(v.get('fix_versions',[])) or 'none'}\") \
                  for dep in json.load(open(sys.argv[1])).get('dependencies',[]) for v in dep.get('vulns',[])]" \
                 "$PIP_AUDIT_TMP" 2>/dev/null || true
            ERRORS=$((ERRORS + 1))
        else
            echo "OK: no known vulnerabilities"
        fi
    else
        # pip-audit exhausted retries on a genuine execution failure (timeout /
        # network) — required=false so we warn and continue. This branch is NOT
        # reached when vulnerabilities are found; those exit cleanly above.
        echo "WARN: pip-audit did not complete — dependency vulnerability check skipped"
    fi
    rm -f "$PIP_AUDIT_TMP"
    unset -f _run_pip_audit
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
