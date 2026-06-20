#!/usr/bin/env bash
# run_gates.sh — central gate runner (SPEC-375 / REQ-GATE-NN-16).
#
# Runs every gate script in scripts/oversight/gates/ against the changed files,
# records a JSON result record per gate in .claudetmp/oversight/validators/gate-results.json,
# and exits 0 only when all non-suspended gates pass.
#
# Usage:
#   ./scripts/oversight/run_gates.sh [file ...]
#   ./scripts/oversight/run_gates.sh --all
#
# Output:
#   .claudetmp/oversight/validators/gate-results.json
#       Array of objects: {"gate","exit_code","suspended","script","ts"}
#
# Each gate's own suspension check (check_suspension.sh) is authoritative for
# that gate's exit code; this script additionally queries suspension_manager.py
# to populate the 'suspended' field in the result record so the oversight-evaluator
# can distinguish suspended-but-failed from genuinely-failed.
#
# Part of the deterministic gate non-override invariant (DECISIONS.md §D-SPEC-375).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATES_DIR="$SCRIPT_DIR/gates"
OUT_DIR=".claudetmp/oversight/validators"
OUT_FILE="$OUT_DIR/gate-results.json"
SUSPENSION_MANAGER="$SCRIPT_DIR/suspension_manager.py"

# Resolve python — prefer the oversight venv if present.
PYTHON=""
if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
else
    echo "run_gates: ERROR: python not found — run: ./bootstrap/hos_bootstrap.sh" >&2
    exit 1
fi

# ── Argument forwarding ───────────────────────────────────────────────────────
# All arguments are forwarded verbatim to each gate script.
# Each gate decides independently how to interpret them.
# bash 3.2: "${arr[@]}" on empty array triggers unbound under set -u; use safe expansion
GATE_ARGS=("$@")

# ── Helpers ───────────────────────────────────────────────────────────────────
_ts() {
    date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date +"%Y-%m-%dT%H:%M:%SZ"
}

_is_suspended() {
    local gate="$1"
    "$PYTHON" "$SUSPENSION_MANAGER" --is-suspended "$gate" &>/dev/null
    return $?
}

_escape_json_string() {
    # Minimal JSON string escaping for the script path field.
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

# ── Setup output directory ────────────────────────────────────────────────────
mkdir -p "$OUT_DIR"

# Collect gate scripts — sorted for determinism.
# Note: sort -z (null-terminated) is not available on macOS BSD sort.
# Gate script names never contain whitespace, so newline-delimited sort is safe.
GATE_SCRIPTS=()
while IFS= read -r f; do
    [[ -n "$f" ]] && GATE_SCRIPTS+=("$f")
done < <(find "$GATES_DIR" -maxdepth 1 -name "*.sh" -not -name "check_suspension.sh" | sort)

if [[ ${#GATE_SCRIPTS[@]} -eq 0 ]]; then
    echo "run_gates: no gate scripts found in $GATES_DIR" >&2
    printf '[]' > "$OUT_FILE"
    exit 0
fi

echo "=== Gate runner: ${#GATE_SCRIPTS[@]} gate(s) ==="
echo "Output: $OUT_FILE"
echo ""

# ── Run gates ─────────────────────────────────────────────────────────────────
RESULTS_JSON="["
FIRST=1
OVERALL_RC=0

for script in "${GATE_SCRIPTS[@]}"; do
    gate_name="$(basename "$script" .sh)"
    ts="$(_ts)"

    # Check suspension status before running so we can record it accurately.
    suspended=false
    if _is_suspended "$gate_name"; then
        suspended=true
    fi

    # Run the gate; capture exit code without aborting the runner.
    gate_rc=0
    bash "$script" ${GATE_ARGS[@]+"${GATE_ARGS[@]}"} || gate_rc=$?

    # A failed non-suspended gate causes the overall runner to fail.
    if [[ $gate_rc -ne 0 && "$suspended" == "false" ]]; then
        OVERALL_RC=1
        printf '  FAIL (exit %d): %s\n' "$gate_rc" "$gate_name"
    elif [[ $gate_rc -ne 0 && "$suspended" == "true" ]]; then
        printf '  SKIP (suspended): %s\n' "$gate_name"
    else
        printf '  PASS: %s\n' "$gate_name"
    fi

    escaped_script="$(_escape_json_string "$script")"
    record=$(printf '{"gate":"%s","exit_code":%d,"suspended":%s,"script":"%s","ts":"%s"}' \
        "$gate_name" "$gate_rc" "$suspended" "$escaped_script" "$ts")

    if [[ $FIRST -eq 1 ]]; then
        RESULTS_JSON="${RESULTS_JSON}${record}"
        FIRST=0
    else
        RESULTS_JSON="${RESULTS_JSON},${record}"
    fi
done

RESULTS_JSON="${RESULTS_JSON}]"

# Write the JSON array — one element per gate, order matches execution order.
printf '%s\n' "$RESULTS_JSON" > "$OUT_FILE"

echo ""
if [[ $OVERALL_RC -eq 0 ]]; then
    echo "GATE PASS: all non-suspended gates passed"
else
    echo "GATE FAIL: one or more non-suspended gates failed — see above"
fi

exit $OVERALL_RC
