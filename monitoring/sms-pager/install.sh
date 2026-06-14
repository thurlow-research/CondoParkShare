#!/usr/bin/env bash
# install.sh — idempotent installer for the SMS pager relay.
#
# Must be run as root (uses useradd, systemctl, chmod, chown).
#
# What it does:
#   1. Creates the dedicated unprivileged user monitrix-pager (if absent).
#   2. Creates /usr/local/lib/monitrix/sms-pager/ and installs relay + scripts.
#   3. Creates /etc/monitrix/ and places sms-pager.env (from example if absent).
#   4. Sets ownership and permissions (chmod 600 on the env file).
#   5. Installs and enables the systemd units.
#   6. Refuses to start the service if the env file still has placeholder values
#      or if WEBHOOK_SHARED_SECRET is shorter than 32 characters.
#
# Idempotent: re-running is safe.  Files are copied each time (to pick up
# updates), but the env file is never overwritten if it already exists.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SERVICE_USER="monitrix-pager"
INSTALL_DIR="/usr/local/lib/monitrix/sms-pager"
ETC_DIR="/etc/monitrix"
ENV_FILE="${ETC_DIR}/sms-pager.env"
SYSTEMD_DIR="/etc/systemd/system"

# ── helpers ────────────────────────────────────────────────────────────────────

log()  { echo "[install] $*"; }
fail() { echo "[install] ERROR: $*" >&2; exit 1; }

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        fail "This installer must be run as root (use sudo)."
    fi
}

# ── step 1: dedicated user ─────────────────────────────────────────────────────

create_user() {
    if id "${SERVICE_USER}" &>/dev/null; then
        log "User ${SERVICE_USER} already exists — skipping creation."
    else
        useradd \
            --system \
            --no-create-home \
            --shell /usr/sbin/nologin \
            --comment "Monitrix SMS pager relay" \
            "${SERVICE_USER}"
        log "Created user ${SERVICE_USER}."
    fi
}

# ── step 2: install relay files ────────────────────────────────────────────────

install_relay() {
    mkdir -p "${INSTALL_DIR}"

    # Install (or upgrade) the relay script.
    install -m 0755 "${SCRIPT_DIR}/sms_relay.py" "${INSTALL_DIR}/sms_relay.py"

    # Install the heartbeat script.
    install -m 0755 "${SCRIPT_DIR}/heartbeat.sh" "${INSTALL_DIR}/heartbeat.sh"

    # Install the test script alongside the other installed scripts so the
    # runbook path (${INSTALL_DIR}/test_send.sh) resolves correctly.
    install -m 0755 "${SCRIPT_DIR}/test_send.sh" "${INSTALL_DIR}/test_send.sh"

    # Always upgrade the venv so it survives a Python interpreter upgrade.
    # --upgrade is a no-op when the venv is already current, making this safe
    # to run on every install.
    python3 -m venv --upgrade "${INSTALL_DIR}/venv"
    log "Venv at ${INSTALL_DIR}/venv is current."

    chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"
    log "Installed relay files to ${INSTALL_DIR}."
}

# ── step 3: env file ───────────────────────────────────────────────────────────

install_env_file() {
    mkdir -p "${ETC_DIR}"
    # root:monitrix-pager + 750: root owns, service user can traverse for
    # diagnostics (e.g. `sudo -u monitrix-pager cat ...`), world cannot read.
    chown root:"${SERVICE_USER}" "${ETC_DIR}"
    chmod 750 "${ETC_DIR}"

    if [ -f "${ENV_FILE}" ]; then
        log "${ENV_FILE} already exists — not overwriting."
    else
        install -m 0600 -o "${SERVICE_USER}" -g "${SERVICE_USER}" \
            "${SCRIPT_DIR}/sms-pager.env.example" "${ENV_FILE}"
        log "Placed example env file at ${ENV_FILE}."
        log ""
        log "  !! ACTION REQUIRED: fill in ${ENV_FILE} before starting the service."
        log "     Replace all placeholder values (see comments in the file)."
        log "     SMTP credentials go in /etc/grafana/grafana.ini [smtp], NOT here."
        log "     See monitoring/sms-pager/grafana-server.env.example for Grafana vars."
        log ""
    fi

    # Always enforce correct permissions regardless of how the file was created.
    chown "${SERVICE_USER}:${SERVICE_USER}" "${ENV_FILE}"
    chmod 600 "${ENV_FILE}"
}

# ── step 4: detect placeholder values and weak secrets ─────────────────────────

check_placeholders() {
    local placeholder_patterns=(
        "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        "your_auth_token_here"
        "+1XXXXXXXXXX"
        "change_me_generate_with_secrets_token_urlsafe_32"
        "+1YYYYYYYYYY"
    )

    local found_placeholder=0
    for pattern in "${placeholder_patterns[@]}"; do
        if grep -qF "${pattern}" "${ENV_FILE}" 2>/dev/null; then
            log "PLACEHOLDER DETECTED in ${ENV_FILE}: ${pattern}"
            found_placeholder=1
        fi
    done

    if [ "${found_placeholder}" -eq 1 ]; then
        fail "The env file ${ENV_FILE} still contains placeholder values.
       Fill in all required secrets before running the installer again.
       The service will NOT be started."
    fi

    # Enforce minimum secret length — same bar the relay enforces at runtime.
    local secret
    secret="$(grep -E '^WEBHOOK_SHARED_SECRET=' "${ENV_FILE}" | cut -d= -f2- | tr -d '[:space:]')"
    if [ "${#secret}" -lt 32 ]; then
        fail "WEBHOOK_SHARED_SECRET in ${ENV_FILE} is shorter than 32 characters (got ${#secret}).
       Generate a strong secret: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"
       The service will NOT be started."
    fi
}

# ── step 5: install systemd units ─────────────────────────────────────────────

install_units() {
    local units=(
        "sms-pager.service"
        "sms-pager-heartbeat.service"
        "sms-pager-heartbeat.timer"
    )

    for unit in "${units[@]}"; do
        install -m 0644 "${SCRIPT_DIR}/${unit}" "${SYSTEMD_DIR}/${unit}"
        log "Installed ${SYSTEMD_DIR}/${unit}."
    done

    systemctl daemon-reload
    log "Reloaded systemd."
}

# ── step 6: enable and start ───────────────────────────────────────────────────

enable_services() {
    systemctl enable --now sms-pager.service
    log "Enabled and started sms-pager.service."

    systemctl enable --now sms-pager-heartbeat.timer
    log "Enabled sms-pager-heartbeat.timer."

    systemctl status sms-pager.service --no-pager || true
}

# ── main ───────────────────────────────────────────────────────────────────────

main() {
    require_root
    log "Starting idempotent install of SMS pager relay…"

    create_user
    install_relay
    install_env_file
    check_placeholders
    install_units
    enable_services

    log ""
    log "Installation complete."
    log ""
    log "Next steps:"
    log "  1. Verify the relay is running:    systemctl status sms-pager"
    log "  2. Run the end-to-end test (sources env automatically):"
    log "       sudo -u ${SERVICE_USER} bash -c \\"
    log "         'set -a; source ${ENV_FILE}; set +a; bash ${INSTALL_DIR}/test_send.sh'"
    log "  3. Configure Grafana contact points per the runbook:"
    log "     docs/runbooks/MONITRIX-SMS-PAGER-SETUP.md"
    log "  4. Provision Grafana secrets via a 0600 EnvironmentFile (NOT inline Environment=):"
    log "     see monitoring/sms-pager/grafana-server.env.example for the full procedure"
    log ""
}

main "$@"
