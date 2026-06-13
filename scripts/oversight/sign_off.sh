#!/usr/bin/env bash
# sign_off.sh — write a validation-suite sign-off stamp.
#
# Each agent in the validation suite runs this when it approves a change. It
# writes signoffs/<role>.stamp. The stamp's *git commit timestamp* is what the
# gate checks (scripts/oversight/signoff_gate.py), so the caller must commit the
# stamp together with — or after — the changes it signs off on.
#
# Usage:
#   scripts/oversight/sign_off.sh <role> [--status STATUS] [--agent NAME] [--note "text"]
#
#   <role>      One of the role keys in contract/step-manifest.yaml role_mappings
#               (code-review, security, privacy, test-unit, test-system, process,
#                infra, ui, a11y).
#   --status    APPROVED (default) | CONDITIONAL | NOT_APPLICABLE
#   --agent     Override the agent name (defaults to the manifest mapping).
#   --note      Free-text note recorded in the stamp.
#
# The stamp is written but NOT committed — commit it yourself so the timestamp
# is authoritative:
#
#   git add signoffs/<role>.stamp && git commit
#
# Exit 0 on success, 2 on usage/role error.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
MANIFEST="$REPO_ROOT/contract/step-manifest.yaml"
SIGNOFFS_DIR="$REPO_ROOT/signoffs"

# Use the oversight venv's Python (has PyYAML) rather than bare python3.
# On macOS Homebrew Python 3.14+ and Ubuntu 24.04+ (PEP 668), the system
# Python has no user packages — import yaml would crash.  ensure_venv.sh
# exports $OVERSIGHT_PYTHON which points to the venv.
_ENSURE="$SCRIPT_DIR/ensure_venv.sh"
if [[ -f "$_ENSURE" ]]; then
    # shellcheck source=scripts/oversight/ensure_venv.sh
    source "$_ENSURE"
    _YAML_PYTHON="$OVERSIGHT_PYTHON"
else
    _YAML_PYTHON="python3"
fi

usage() {
    sed -n '2,33p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-2}"
}

[[ $# -ge 1 ]] || usage 2
ROLE="$1"; shift
[[ "$ROLE" == "-h" || "$ROLE" == "--help" ]] && usage 0

STATUS="APPROVED"
AGENT=""
NOTE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --status) STATUS="${2:?--status needs a value}"; shift 2 ;;
        --agent)  AGENT="${2:?--agent needs a value}"; shift 2 ;;
        --note)   NOTE="${2:?--note needs a value}"; shift 2 ;;
        -h|--help) usage 0 ;;
        *) echo "sign_off: unknown argument: $1" >&2; usage 2 ;;
    esac
done

STATUS="$(echo "$STATUS" | tr '[:lower:]' '[:upper:]')"
case "$STATUS" in
    APPROVED|CONDITIONAL|NOT_APPLICABLE) ;;
    NA) STATUS="NOT_APPLICABLE" ;;
    *) echo "sign_off: invalid --status '$STATUS' (APPROVED|CONDITIONAL|NOT_APPLICABLE)" >&2; exit 2 ;;
esac

# Resolve the role -> agent mapping (and validate the role exists) via the manifest.
RESOLVED_AGENT="$(
    PYTHONSAFEPATH=1 "$_YAML_PYTHON" - "$MANIFEST" "$ROLE" <<'PY'
import sys, yaml
manifest_path, role = sys.argv[1], sys.argv[2]
m = yaml.safe_load(open(manifest_path))
mapping = m.get("role_mappings", {}) or {}
if role not in mapping:
    sys.stderr.write(
        "sign_off: unknown role '%s'. Valid roles: %s\n"
        % (role, ", ".join(sorted(mapping)))
    )
    sys.exit(2)
print(mapping[role])
PY
)"
[[ -n "$AGENT" ]] || AGENT="$RESOLVED_AGENT"

mkdir -p "$SIGNOFFS_DIR"
STAMP="$SIGNOFFS_DIR/${ROLE}.stamp"
SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo "uncommitted")"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

{
    echo "role: $ROLE"
    echo "agent: $AGENT"
    echo "status: $STATUS"
    echo "signed_at: $NOW          # informational — the gate uses the git commit time"
    echo "head_at_signing: $SHA"
    [[ -n "$NOTE" ]] && echo "note: $NOTE"
} > "$STAMP"

REL="${STAMP#"$REPO_ROOT"/}"
echo "Wrote $REL ($STATUS, agent=$AGENT)."
echo "Commit it so the timestamp is authoritative:"
echo "  git add $REL && git commit -m 'sign-off: $ROLE $STATUS'"
