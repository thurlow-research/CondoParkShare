#!/usr/bin/env bash
# test_send.sh — end-to-end test: post a test alert to the relay and verify
# that SMS (via Twilio) arrives.  Email fallback is verified separately via
# Grafana's contact-point test UI (Grafana → Alerting → Contact points →
# email-fallback → Test), since it depends on Grafana-side SMTP config.
#
# Usage (recommended — sources the env automatically):
#   sudo -u monitrix-pager bash -c \
#     'set -a; source /etc/monitrix/sms-pager.env; set +a
#      bash /usr/local/lib/monitrix/sms-pager/test_send.sh'
#
# Or, if you have already sourced the env file in your shell:
#   bash test_send.sh
#
# The script requires RELAY_BIND, RELAY_PORT, and WEBHOOK_SHARED_SECRET to be
# set in the environment (from the EnvironmentFile or sourced manually).

set -euo pipefail

RELAY_BIND="${RELAY_BIND:-127.0.0.1}"
RELAY_PORT="${RELAY_PORT:-9876}"
WEBHOOK_SHARED_SECRET="${WEBHOOK_SHARED_SECRET:?WEBHOOK_SHARED_SECRET must be set (source /etc/monitrix/sms-pager.env first)}"

RELAY_URL="http://${RELAY_BIND}:${RELAY_PORT}/alert"

echo "Sending test alert to relay at ${RELAY_URL} …"

PAYLOAD=$(cat <<'EOF'
{
  "title": "TEST — SMS pager end-to-end check",
  "state": "alerting",
  "message": "This is a manual end-to-end test. SMS should arrive on all recipient phones. Check the email fallback separately via Grafana contact-point test.",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "SmsPagerTest",
        "severity": "critical",
        "pager": "sms",
        "team": "oncall"
      },
      "annotations": {
        "summary": "Manual end-to-end pager test",
        "description": "Triggered by test_send.sh on monitrix. Expect SMS on all recipients."
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

HTTP_RESPONSE=$(curl \
    --silent \
    --include \
    --max-time 30 \
    --header "Content-Type: application/json" \
    --header "@${SECRET_HDR}" \
    --data "${PAYLOAD}" \
    "${RELAY_URL}")

HTTP_STATUS=$(echo "${HTTP_RESPONSE}" | grep -E "^HTTP/" | tail -1 | awk '{print $2}')

echo "Relay HTTP status: ${HTTP_STATUS}"

if [ "${HTTP_STATUS}" -ne 200 ]; then
    echo ""
    echo "FAIL: relay returned HTTP ${HTTP_STATUS}" >&2
    echo "Full response:"
    echo "${HTTP_RESPONSE}"
    echo ""
    echo "Troubleshooting:"
    echo "  - Check relay is running:  systemctl status sms-pager"
    echo "  - Check relay logs:        journalctl -u sms-pager -n 50"
    echo "  - Check WEBHOOK_SHARED_SECRET matches /etc/monitrix/sms-pager.env"
    exit 1
fi

echo ""
echo "OK: relay accepted the test alert."
echo ""
echo "Now verify manually:"
echo "  1. SMS arrives on ALL numbers in SMS_RECIPIENTS — check every phone."
echo "  2. Relay logs show action=dispatch_ok:"
echo "       journalctl -u sms-pager -n 20"
echo "  3. Email fallback: Grafana → Alerting → Contact points → email-fallback → Test"
echo ""
echo "If SMS arrives but email does not (or vice versa), the other contact"
echo "point in Grafana is misconfigured — check Grafana → Alerting → Contact Points."
