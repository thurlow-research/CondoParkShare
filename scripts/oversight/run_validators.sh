#!/usr/bin/env bash
# run_validators.sh — orchestrate all risk assessment validators for a file set.
#
# Runs every validator in scripts/oversight/validators/ on the provided files,
# collects JSON output, and writes results to .claudetmp/oversight/validators/.
# The risk assessor agent reads those files to synthesize the composite score
# and inspection brief.
#
# Usage:
#   ./scripts/oversight/run_validators.sh file.py [file2.py ...]
#   ./scripts/oversight/run_validators.sh --step 3    (reads step 3 changed files from git)
#   ./scripts/oversight/run_validators.sh --diff HEAD~1
#
# Output:
#   .claudetmp/oversight/validators/<dimension>.json   per-validator results
#   .claudetmp/oversight/validators/summary.json       all results + composite score
#
# The risk assessor agent invokes this, reads the summary, and produces
# the inspection brief + final risk tier.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALIDATORS_DIR="$SCRIPT_DIR/validators"
OUT_DIR=".claudetmp/oversight/validators"

# shellcheck source=scripts/oversight/ensure_venv.sh
source "$SCRIPT_DIR/ensure_venv.sh"
PYTHON="${PYTHON:-$OVERSIGHT_PYTHON}"

# shellcheck source=scripts/oversight/run_with_retry.sh
source "$SCRIPT_DIR/run_with_retry.sh"

# Configurable defaults (override via env)
VALIDATOR_TIMEOUT="${VALIDATOR_TIMEOUT:-60}"       # seconds per attempt
VALIDATOR_RETRIES="${VALIDATOR_RETRIES:-2}"        # retries after first attempt
NETWORK_TIMEOUT="${NETWORK_TIMEOUT:-30}"           # shorter for network-dependent validators

FILES=()
STEP=""

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --step)
            STEP="$2"; shift 2
            ;;
        --diff)
            DIFF_REF="$2"; shift 2
            mapfile -t FILES < <(git diff --name-only "$DIFF_REF" 2>/dev/null | grep '\.py$' || true)
            ;;
        *)
            FILES+=("$1"); shift
            ;;
    esac
done

# Filter to existing Python files
PY_FILES=()
ALL_FILES=()
for f in "${FILES[@]}"; do
    ALL_FILES+=("$f")
    [[ "$f" == *.py && -f "$f" ]] && PY_FILES+=("$f")
done

if [[ ${#ALL_FILES[@]} -eq 0 ]]; then
    echo "run_validators: no files specified"
    echo "Usage: $0 file.py [file2.py ...]"
    # Write a durable CRITICAL summary so downstream agents have an artifact to read
    mkdir -p "$OUT_DIR"
    PYTHONSAFEPATH=1 "$PYTHON" -c "
import json; from pathlib import Path
summary = {'composite_score': 1.0, 'tier': 'CRITICAL', 'validator_count': 0,
           'successful_validators': 0,
           'error': 'No files provided to run_validators.sh — defaulting to CRITICAL (fail-closed)'}
Path('$OUT_DIR/summary.json').write_text(json.dumps(summary, indent=2))
print('CRITICAL summary written to $OUT_DIR/summary.json')
" 2>/dev/null || true
    exit 1
fi

mkdir -p "$OUT_DIR"
# Clear stale results from prior runs — old JSON files would contaminate the score
rm -f "$OUT_DIR"/*.json

echo "=== Oversight validators: ${#ALL_FILES[@]} file(s) ==="
echo "Output: $OUT_DIR/"
echo ""

RESULTS=()

# Counters for summary line
VALIDATOR_SUCCEEDED=0
VALIDATOR_SKIPPED=0
VALIDATOR_FAILED=0

run_validator() {
    local name="$1"
    local script="$2"
    local timeout="${3:-$VALIDATOR_TIMEOUT}"
    local required="${4:-false}"
    shift 4
    local args=("$@")

    local outfile="$OUT_DIR/${name}.json"

    if [[ ! -f "$script" ]]; then
        printf "  \033[33m⏸\033[0m  %-28s SKIP (script not found)\n" "$name"
        VALIDATOR_SKIPPED=$(( VALIDATOR_SKIPPED + 1 ))
        return
    fi

    local tmpout
    tmpout=$(mktemp /tmp/validator_XXXXXX)

    # Unit of work — one attempt. Sees name/script/args/timeout/tmpout via bash
    # dynamic scope (it is called from run_with_retry, which we call from here).
    # PYTHONPATH includes the validators dir so `from schema import` works.
    # with_timeout (from run_with_retry.sh) returns 124 on timeout.
    _validator_unit() {
        PYTHONPATH="$VALIDATORS_DIR${PYTHONPATH:+:$PYTHONPATH}" \
            with_timeout "$timeout" "$PYTHON" "$script" "${args[@]}" > "$tmpout" 2>/dev/null
    }

    local rc=0
    run_with_retry "$name" "$VALIDATOR_RETRIES" "$required" _validator_unit && rc=0 || rc=$?

    if [[ $rc -eq 0 ]]; then
        local OUTPUT
        OUTPUT=$(cat "$tmpout")
        echo "$OUTPUT" > "$outfile"
        local SCORE
        SCORE=$(echo "$OUTPUT" | PYTHONSAFEPATH=1 "$PYTHON" -c \
            "import json,sys; d=json.load(sys.stdin); print(f\"{d.get('score',0):.2f}\")" 2>/dev/null || echo "?")
        printf "  %-30s score=%s\n" "$name" "$SCORE"
        RESULTS+=("$name")
        VALIDATOR_SUCCEEDED=$(( VALIDATOR_SUCCEEDED + 1 ))
    else
        # run_with_retry already printed the ✘/⏸ line and emitted the audit event.
        echo '{"dimension":"'"$name"'","score":0,"error":"validator exhausted retries"}' > "$outfile"
        if [[ $rc -eq 1 ]]; then
            VALIDATOR_FAILED=$(( VALIDATOR_FAILED + 1 ))
        else
            VALIDATOR_SKIPPED=$(( VALIDATOR_SKIPPED + 1 ))
        fi
    fi
    rm -f "$tmpout"
}

# Run all validators
#   Signature: run_validator NAME SCRIPT TIMEOUT_SEC REQUIRED [args...]
#   REQUIRED=false: timeout/crash → SKIP (optional); all validators are optional individually
#   Network-dependent validators get shorter timeout; heavy ones get longer

if [[ ${#PY_FILES[@]} -gt 0 ]]; then
    run_validator "risk_number"      "$VALIDATORS_DIR/rn_calculator.py"         60 false "${PY_FILES[@]}"
    run_validator "complexity"       "$VALIDATORS_DIR/complexity_metrics.py"    60 false "${PY_FILES[@]}"
    run_validator "function_metrics" "$VALIDATORS_DIR/function_metrics.py"      60 false "${PY_FILES[@]}"
    run_validator "n1_queries"       "$VALIDATORS_DIR/n1_detector.py"           60 false "${PY_FILES[@]}"
    run_validator "static_analysis"  "$VALIDATORS_DIR/static_analysis.py"      120 false "${PY_FILES[@]}"
    run_validator "hallucination"    "$VALIDATORS_DIR/hallucination_surface.py" 60 false "${PY_FILES[@]}"
fi

# Migration scorer — all files
run_validator "migration_risk"   "$VALIDATORS_DIR/migration_scorer.py"         60 false "${ALL_FILES[@]}"

# Historical density — network-dependent (calls gh + git); shorter timeout
run_validator "historical_density" "$VALIDATORS_DIR/issue_query.py"   \
    "$NETWORK_TIMEOUT" false "${ALL_FILES[@]}"

# IP / provenance — calls ScanCode/PyPI; heavy, longer timeout
run_validator "ip_check"         "$VALIDATORS_DIR/ip_check.py"                120 false \
    --prompts-dir "prompts" "${ALL_FILES[@]}"

# Portability check
if [[ ${#PY_FILES[@]} -gt 0 ]]; then
    run_validator "portability"    "$VALIDATORS_DIR/portability_check.py"       60 false "${PY_FILES[@]}"
fi

# Prompt audit — network-dependent (calls gh for spec-gap count)
if [[ ${#PY_FILES[@]} -gt 0 ]]; then
    run_validator "prompt_ambiguity" "$VALIDATORS_DIR/prompt_audit_risk.py"  \
        "$NETWORK_TIMEOUT" false \
        --prompts-dir "prompts" --step "${STEP:-}" "${PY_FILES[@]}"
fi

echo ""
echo "  Validators: ${VALIDATOR_SUCCEEDED} succeeded, ${VALIDATOR_SKIPPED} skipped (optional), ${VALIDATOR_FAILED} failed (required)"
if [[ $VALIDATOR_FAILED -gt 0 ]]; then
    echo "  ✘ Required validator(s) failed — composite score set to CRITICAL (fail-closed)"
fi
echo ""

# Aggregate into summary.json
PYTHONSAFEPATH=1 "$PYTHON" - <<'EOF'
import json, os, sys
from pathlib import Path

out_dir = Path(".claudetmp/oversight/validators")
results = []
for f in sorted(out_dir.glob("*.json")):
    if f.name == "summary.json":
        continue
    try:
        results.append(json.loads(f.read_text()))
    except Exception:
        pass

# Composite score: weighted average
total_w, weighted_sum = 0.0, 0.0
for r in results:
    if r.get("error"):
        continue
    w = r.get("weight", 1.0)
    weighted_sum += r.get("score", 0.0) * w
    total_w += w

# Fail-closed: if no validators produced usable output, treat as CRITICAL.
# Defaulting to LOW on total validator failure would silently pass broken code.
successful = [r for r in results if not r.get("error")]
if total_w == 0 or not successful:
    composite = 1.0
    tier = "CRITICAL"
    summary = {
        "composite_score": composite,
        "tier": tier,
        "validator_count": len(results),
        "successful_validators": 0,
        "error": "All validators failed or produced no output — defaulting to CRITICAL (fail-closed)",
        "results": results,
    }
    out = out_dir / "summary.json"
    out.write_text(json.dumps(summary, indent=2))
    print(f"ERROR: no validators succeeded → tier: CRITICAL (fail-closed)")
    print(f"Summary: {out}")
    sys.exit(1)

composite = round(weighted_sum / total_w, 4)

TIERS = [("LOW", 0.30), ("MEDIUM", 0.55), ("HIGH", 0.78), ("CRITICAL", 1.01)]
tier = next(t for t, hi in TIERS if composite < hi)

summary = {
    "composite_score": composite,
    "tier": tier,
    "validator_count": len(results),
    "successful_validators": len(successful),
    "results": results,
}

out = out_dir / "summary.json"
out.write_text(json.dumps(summary, indent=2))
print(f"Composite score: {composite:.4f}  →  tier: {tier}")
print(f"Summary: {out}")
EOF
