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
PYTHON="${PYTHON:-python3}"

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
    python3 -c "
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

run_validator() {
    local name="$1"
    local script="$2"
    shift 2
    local args=("$@")

    printf "  %-30s" "$name"
    local outfile="$OUT_DIR/${name}.json"

    if [[ ! -f "$script" ]]; then
        echo "SKIP (not found)"
        return
    fi

    if OUTPUT=$("$PYTHON" "$script" "${args[@]}" 2>/dev/null); then
        echo "$OUTPUT" > "$outfile"
        SCORE=$(echo "$OUTPUT" | python3 -c \
            "import json,sys; d=json.load(sys.stdin); print(f\"{d.get('score',0):.2f}\")" 2>/dev/null || echo "?")
        echo "score=$SCORE"
        RESULTS+=("$name")
    else
        echo "ERROR"
        echo '{"dimension":"'"$name"'","score":0,"error":"validator failed"}' > "$outfile"
    fi
}

# Run all validators
if [[ ${#PY_FILES[@]} -gt 0 ]]; then
    run_validator "risk_number"         "$VALIDATORS_DIR/rn_calculator.py"       "${PY_FILES[@]}"
    run_validator "complexity"          "$VALIDATORS_DIR/complexity_metrics.py"  "${PY_FILES[@]}"
    run_validator "function_metrics"    "$VALIDATORS_DIR/function_metrics.py"    "${PY_FILES[@]}"
    run_validator "n1_queries"          "$VALIDATORS_DIR/n1_detector.py"         "${PY_FILES[@]}"
    run_validator "static_analysis"     "$VALIDATORS_DIR/static_analysis.py"     "${PY_FILES[@]}"
    run_validator "hallucination"       "$VALIDATORS_DIR/hallucination_surface.py" "${PY_FILES[@]}"
fi

# Migration scorer — all files (not just .py filter applied above, though it checks internally)
run_validator "migration_risk"      "$VALIDATORS_DIR/migration_scorer.py"    "${ALL_FILES[@]}"

# Historical density — uses git + gh, works on any file type
run_validator "historical_density"  "$VALIDATORS_DIR/issue_query.py"         "${ALL_FILES[@]}"

# IP / provenance — license gate + prompt clean-room (all file types; levels 1-2 active)
run_validator "ip_check"            "$VALIDATORS_DIR/ip_check.py" \
    --prompts-dir "prompts"         "${ALL_FILES[@]}"

# Prompt audit — ambiguity score + fidelity surface (Python files + prompt artifacts)
if [[ ${#PY_FILES[@]} -gt 0 ]]; then
    run_validator "prompt_ambiguity"    "$VALIDATORS_DIR/prompt_audit_risk.py" \
        --prompts-dir "prompts" --step "${STEP:-}"  "${PY_FILES[@]}"
fi

echo ""

# Aggregate into summary.json
"$PYTHON" - <<'EOF'
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
