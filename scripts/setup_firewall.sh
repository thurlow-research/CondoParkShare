#!/usr/bin/env bash
# setup_firewall.sh — idempotent UFW rules for monitrix↔app-host integration.
#
# Opens the audit-metrics exporter port (inbound, from the monitrix LAN subnet
# only) and the outbound Loki push path (skipped with a warning if LOKI_PORT is
# not yet set — it is TBD at time of writing).
#
# MUST be run as root (or via sudo) on opus (prod) or faberix (ppe).
# Safe to re-run: ufw deduplicates rules by content.
#
# Constraints enforced:
#   - The metrics exporter must bind on a host-internal interface only; it must
#     NOT be routed through the public Caddy reverse-proxy.
#   - The Postgres port is not touched by this script.
#   - The audit-recovery JSONL log path must never be publicly accessible.
#   - Rules are never applied without an explicit source (no "allow to any port").
#
# Usage (on the target host, after sourcing the correct .env):
#   sudo scripts/setup_firewall.sh
#
# Or let deploy.sh call it (see docs/deploy/FIREWALL.md).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Load .env ────────────────────────────────────────────────────────────────
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
else
    echo "setup_firewall: WARNING — .env not found at $ENV_FILE; relying on exported env vars." >&2
fi

# ── Validate required vars ───────────────────────────────────────────────────
_require_var() {
    local var="$1"
    if [[ -z "${!var:-}" ]]; then
        echo "setup_firewall: ERROR — required variable '$var' is not set." >&2
        echo "                Set it in .env or export it before running this script." >&2
        exit 1
    fi
}

_require_var AUDIT_METRICS_PORT
_require_var MONITRIX_SCRAPE_SRC_V4
_require_var MONITRIX_SCRAPE_SRC_V6

# LOKI_PORT is TBD — skip outbound rule with a clear warning if unset.
LOKI_PORT="${LOKI_PORT:-}"

echo "=========================================="
echo " CondoParkShare — firewall setup"
echo "   metrics port (inbound):  $AUDIT_METRICS_PORT"
echo "   inbound allow from (v4): $MONITRIX_SCRAPE_SRC_V4"
echo "   inbound allow from (v6): $MONITRIX_SCRAPE_SRC_V6"
if [[ -n "$LOKI_PORT" ]]; then
    echo "   Loki outbound port:      $LOKI_PORT"
else
    echo "   Loki outbound port:      (not set — outbound rule will be SKIPPED)"
fi
echo "=========================================="

# ── Guard: ufw must be present ───────────────────────────────────────────────
if ! command -v ufw &>/dev/null; then
    echo "setup_firewall: ERROR — ufw is not installed on this host." >&2
    exit 1
fi

# ── Guard: must run as root ───────────────────────────────────────────────────
if [[ "$EUID" -ne 0 ]]; then
    echo "setup_firewall: ERROR — must be run as root (or via sudo)." >&2
    exit 1
fi

# ── Inbound: allow monitrix subnet → metrics exporter port ───────────────────
echo ""
echo "==> Applying inbound rules (metrics scrape from monitrix subnet)..."

ufw allow in \
    from "$MONITRIX_SCRAPE_SRC_V4" \
    to any \
    port "$AUDIT_METRICS_PORT" \
    proto tcp \
    comment "monitrix-scrape: audit-metrics exporter (v4 LAN subnet)"

echo "    OK: allow in from $MONITRIX_SCRAPE_SRC_V4 to port $AUDIT_METRICS_PORT/tcp"

ufw allow in \
    from "$MONITRIX_SCRAPE_SRC_V6" \
    to any \
    port "$AUDIT_METRICS_PORT" \
    proto tcp \
    comment "monitrix-scrape: audit-metrics exporter (v6 LAN subnet)"

echo "    OK: allow in from $MONITRIX_SCRAPE_SRC_V6 to port $AUDIT_METRICS_PORT/tcp"

# ── Outbound: allow app-host → monitrix Loki (skip if LOKI_PORT unset) ───────
echo ""
if [[ -z "$LOKI_PORT" ]]; then
    echo "==> SKIPPING outbound Loki rule — LOKI_PORT is not set (TBD)."
    echo "    Once the Loki push port is confirmed, set LOKI_PORT in .env and"
    echo "    re-run this script."
else
    echo "==> Applying outbound rule (Loki log push to monitrix)..."

    MONITRIX_HOST="${MONITRIX_HOST:-}"
    if [[ -z "$MONITRIX_HOST" ]]; then
        echo "setup_firewall: ERROR — LOKI_PORT is set but MONITRIX_HOST is not." >&2
        echo "                Set MONITRIX_HOST in .env (hostname or IP of monitrix)." >&2
        exit 1
    fi

    ufw allow out \
        to "$MONITRIX_HOST" \
        port "$LOKI_PORT" \
        proto tcp \
        comment "monitrix-loki: app→monitrix Loki push"

    echo "    OK: allow out to $MONITRIX_HOST port $LOKI_PORT/tcp"
fi

echo ""
echo "==> Firewall rules applied. Current UFW status:"
ufw status verbose

echo ""
echo "setup_firewall: done."
echo ""
echo "Reminders:"
echo "  - Ensure the audit-metrics exporter binds on a host-internal interface"
echo "    (not 0.0.0.0) so it is never reachable via the public Caddy route."
echo "  - The Postgres port (5432) is NOT opened by this script and must remain"
echo "    unpublished (container-internal only)."
echo "  - The audit-recovery JSONL log (/app/logs/audit-recovery.jsonl) must"
echo "    never be served via any public route."
