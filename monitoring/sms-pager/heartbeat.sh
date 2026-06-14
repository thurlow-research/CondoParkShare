#!/usr/bin/env bash
# heartbeat.sh — daily synthetic SMS via the relay.
#
# Invoked by sms-pager-heartbeat.service (fired by sms-pager-heartbeat.timer).
# Posts a minimal "alive" alert payload to the relay so the full chain
# (relay → Twilio → phone) is exercised once a day.
#
# All config is read from the environment loaded by the systemd EnvironmentFile.
# This script must produce a non-zero exit code on failure so systemd marks
# the unit as failed and the failure appears in `systemctl status`.

set -euo pipefail

RELAY_BIND="${RELAY_BIND:-127.0.0.1}"
RELAY_PORT="${RELAY_PORT:-9876}"
WEBHOOK_SHARED_SECRET="${WEBHOOK_SHARED_SECRET:?WEBHOOK_SHARED_SECRET must be set}"

RELAY_URL="http://${RELAY_BIND}:${RELAY_PORT}/alert"

PAYLOAD=$(cat <<'EOF'
{
  "title": "SMS pager heartbeat",
  "state": "alerting",
  "message": "Daily synthetic heartbeat — if you receive this, the pager chain is healthy.",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "SmsPagerHeartbeat",
        "severity": "info",
        "pager": "sms-heartbeat"
      },
      "annotations": {
        "summary": "Daily SMS pager liveness check"
      }
    }
  ]
}
EOF
)

# Write the secret header to a temp file so it never appears on the command
# line (readable by any local user via /proc/<pid>/cmdline).
SECRET_HDR=$(mktemp)
chmod 600 "${SECRET_HDR}"
trap 'rm -f "${SECRET_HDR}"' EXIT
printf 'X-Relay-Secret: %s' "${WEBHOOK_SHARED_SECRET}" > "${SECRET_HDR}"

HTTP_STATUS=$(curl \
    --silent \
    --output /dev/null \
    --write-out "%{http_code}" \
    --max-time 15 \
    --retry 1 \
    --retry-delay 3 \
    --header "Content-Type: application/json" \
    --header "@${SECRET_HDR}" \
    --data "${PAYLOAD}" \
    "${RELAY_URL}")

if [ "${HTTP_STATUS}" -ne 200 ]; then
    echo "heartbeat failed: relay returned HTTP ${HTTP_STATUS}" >&2
    exit 1
fi

echo "heartbeat ok: relay returned HTTP ${HTTP_STATUS}"
