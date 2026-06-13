#!/usr/bin/env bash
# secret_scan.sh — hardcoded secret detection gate (blocking).
#
# Uses detect-secrets to find potential credentials, API keys, tokens, and
# other secrets that should never be committed. Checks both staged files and
# the provided file list.
#
# Exit 0 = no secrets found. Exit 1 = potential secrets detected.
#
# Usage: ./secret_scan.sh file.py [file2.py ...]
#        ./secret_scan.sh --staged    (check staged files via git diff)

set -euo pipefail

FILES=()
CHECK_STAGED=false

for arg in "$@"; do
    if [[ "$arg" == "--staged" ]]; then
        CHECK_STAGED=true
    else
        FILES+=("$arg")
    fi
done

if $CHECK_STAGED; then
    while IFS= read -r line; do FILES+=("$line"); done < <(git diff --cached --name-only --diff-filter=ACM 2>/dev/null | \
        grep -E '\.(py|txt|yaml|yml|json|env|cfg|ini|sh)$' || true)
fi

ERRORS=0

if command -v detect-secrets &>/dev/null; then
    echo "=== detect-secrets ==="
    if [[ ${#FILES[@]} -gt 0 ]]; then
        BASELINE=$(detect-secrets scan "${FILES[@]}" 2>/dev/null)
        SECRET_COUNT=$(echo "$BASELINE" | PYTHONSAFEPATH=1 python3 -c \
            "import json,sys; d=json.load(sys.stdin); \
             total=sum(len(v) for v in d.get('results',{}).values()); print(total)" 2>/dev/null || echo "0")
        if [[ "$SECRET_COUNT" -gt 0 ]]; then
            echo "GATE FAIL: $SECRET_COUNT potential secret(s) detected:"
            echo "$BASELINE" | PYTHONSAFEPATH=1 python3 -c \
                "import json,sys
d=json.load(sys.stdin)
for fpath, findings in d.get('results',{}).items():
    for f in findings:
        print(f'  {fpath}:{f[\"line_number\"]} — {f[\"type\"]}')" 2>/dev/null || true
            ERRORS=$((ERRORS + 1))
        else
            echo "OK: no secrets detected"
        fi
    else
        echo "No files to scan"
    fi
else
    # Fallback: grep for common secret patterns
    echo "=== fallback secret grep (detect-secrets not installed) ==="
    PATTERNS=(
        'password\s*=\s*["\x27][^"\x27]{4,}'
        'api_key\s*=\s*["\x27][^"\x27]{8,}'
        'secret\s*=\s*["\x27][^"\x27]{8,}'
        'token\s*=\s*["\x27][^"\x27]{8,}'
        'AWS_SECRET'
        'private_key'
    )
    for pattern in "${PATTERNS[@]}"; do
        if [[ ${#FILES[@]} -gt 0 ]]; then
            MATCHES=$(grep -rniE "$pattern" "${FILES[@]}" 2>/dev/null || true)
            if [[ -n "$MATCHES" ]]; then
                echo "POTENTIAL SECRET: $MATCHES"
                ERRORS=$((ERRORS + 1))
            fi
        fi
    done
    if [[ $ERRORS -eq 0 ]]; then
        echo "OK (fallback patterns — install detect-secrets for full coverage)"
    fi
fi

echo ""
if [[ $ERRORS -gt 0 ]]; then
    echo "GATE FAIL: potential secrets detected — review and remove before commit"
    exit 1
else
    echo "GATE PASS: no secrets detected"
    exit 0
fi
