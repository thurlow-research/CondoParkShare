#!/usr/bin/env bash
# deploy.sh — manual deploy entrypoint for CondoParkShare.
#
# Refuses to deploy unless the full validation suite has signed off on the
# current checkout (scripts/oversight/signoff_gate.py --all). The gate uses git
# commit timestamps, so a checkout with any unsigned or newer-than-sign-off file
# is blocked here, before anything touches the running stack.
#
# Usage:
#   scripts/deploy.sh <ppe|prod> [--skip-gate] [--yes] [--no-build]
#
#   ppe   → faberix  (pre-prod / staging)
#   prod  → opus     (production)
#
# Options:
#   --skip-gate   Deploy WITHOUT the sign-off gate. Refused for prod. Emits a
#                 loud warning for ppe. Intended only for break-glass debugging.
#   --yes         Skip the interactive confirmation prompt.
#   --no-build    docker compose up without rebuilding images.
#
# Host overrides (env): PPE_HOST (default faberix), PROD_HOST (default opus).
# Hostname guard: the local short hostname must match the expected host exactly
# so a prod deploy cannot be run on the wrong box. Override with --force-host.
#
# Exit 0 on success, non-zero on gate failure / wrong host / usage error.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PPE_HOST="${PPE_HOST:-faberix}"
PROD_HOST="${PROD_HOST:-opus}"

usage() {
    sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-2}"
}

[[ $# -ge 1 ]] || usage 2
ENV_NAME="$1"; shift
[[ "$ENV_NAME" == "-h" || "$ENV_NAME" == "--help" ]] && usage 0

SKIP_GATE=false
ASSUME_YES=false
DO_BUILD=true
FORCE_HOST=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-gate)  SKIP_GATE=true; shift ;;
        --yes|-y)     ASSUME_YES=true; shift ;;
        --no-build)   DO_BUILD=false; shift ;;
        --force-host) FORCE_HOST=true; shift ;;
        -h|--help)    usage 0 ;;
        *) echo "deploy: unknown argument: $1" >&2; usage 2 ;;
    esac
done

case "$ENV_NAME" in
    ppe)  TARGET_HOST="$PPE_HOST";  ENV_LABEL="PPE (pre-prod)" ;;
    prod) TARGET_HOST="$PROD_HOST"; ENV_LABEL="PRODUCTION" ;;
    *) echo "deploy: environment must be 'ppe' or 'prod', got '$ENV_NAME'" >&2; usage 2 ;;
esac

echo "=========================================="
echo " CondoParkShare deploy"
echo "   environment: $ENV_LABEL"
echo "   target host: $TARGET_HOST"
echo "=========================================="

# ── Hostname guard ───────────────────────────────────────────────────────────
LOCAL_HOST="$(hostname -s 2>/dev/null || hostname)"
if [[ "$LOCAL_HOST" != "$TARGET_HOST" ]]; then
    if $FORCE_HOST; then
        echo "WARNING: local host '$LOCAL_HOST' != expected '$TARGET_HOST' (overridden by --force-host)."
    else
        echo "deploy: refusing — local host '$LOCAL_HOST' is not '$TARGET_HOST'." >&2
        echo "        Run this on $TARGET_HOST, or pass --force-host if you know better." >&2
        exit 3
    fi
fi

# ── Sign-off gate ────────────────────────────────────────────────────────────
if $SKIP_GATE; then
    if [[ "$ENV_NAME" == "prod" ]]; then
        echo "deploy: --skip-gate is NOT permitted for prod. Aborting." >&2
        exit 4
    fi
    echo ""
    echo "!!!! WARNING: deploying to $ENV_LABEL with the sign-off gate SKIPPED !!!!"
    echo ""
else
    echo ""
    echo "==> Running sign-off gate (validation suite must have signed off)..."
    if ! python3 scripts/oversight/signoff_gate.py --all; then
        echo "" >&2
        echo "deploy: sign-off gate FAILED — not deploying to $ENV_LABEL." >&2
        echo "        Run the validation suite and commit the stamps, then retry." >&2
        exit 1
    fi
    echo "==> Sign-off gate passed."
fi

# ── Confirmation ─────────────────────────────────────────────────────────────
if ! $ASSUME_YES; then
    echo ""
    read -r -p "Deploy commit $(git rev-parse --short HEAD) to $ENV_LABEL ($TARGET_HOST)? [y/N] " reply
    case "$reply" in
        y|Y|yes|YES) ;;
        *) echo "deploy: aborted by user."; exit 0 ;;
    esac
fi

# ── Bring up the stack ───────────────────────────────────────────────────────
# Both ppe (faberix) and prod (opus) run the base compose file with the
# Dockerfile's production settings (no dev overlay).
echo ""
echo "==> Starting stack on $TARGET_HOST..."
if $DO_BUILD; then
    docker compose -f docker-compose.yml up -d --build
else
    docker compose -f docker-compose.yml up -d
fi

echo "==> Applying migrations..."
docker compose -f docker-compose.yml exec -T web python manage.py migrate --no-input

echo "==> Collecting static files..."
docker compose -f docker-compose.yml exec -T web python manage.py collectstatic --no-input --clear

# ── Firewall: open monitrix scrape + Loki paths ───────────────────────────────
# setup_firewall.sh is idempotent and safe to re-run on every deploy.
# It requires root; deploy.sh itself does not require root — sudo is invoked
# only for this step.  Operators who run deploy.sh as root can omit sudo by
# setting FIREWALL_SUDO="" in the environment.
echo ""
echo "==> Applying host firewall rules (monitrix integration)..."
# FIREWALL_SUDO defaults to "sudo". Set to "" when deploy.sh already runs as root.
FIREWALL_SUDO="${FIREWALL_SUDO:-sudo}"
_fw_cmd=("$SCRIPT_DIR/setup_firewall.sh")
[[ -n "$FIREWALL_SUDO" ]] && _fw_cmd=("$FIREWALL_SUDO" "${_fw_cmd[@]}")
if "${_fw_cmd[@]}"; then
    echo "==> Firewall rules applied."
else
    echo ""
    echo "WARNING: setup_firewall.sh exited non-zero — firewall rules may be incomplete." >&2
    echo "         Run 'sudo scripts/setup_firewall.sh' manually and verify 'ufw status'." >&2
    echo "         Continuing deploy (stack is up); fix firewall before exposing metrics." >&2
fi

echo ""
echo "Deploy to $ENV_LABEL ($TARGET_HOST) complete — commit $(git rev-parse --short HEAD)."
echo "  Stack status: docker compose -f $REPO_ROOT/docker-compose.yml ps"
