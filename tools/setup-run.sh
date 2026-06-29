#!/usr/bin/env bash
# setup-run.sh — Install and configure everything needed to RUN CondoParkShare on Ubuntu.
# Installs: Docker Engine + Compose plugin, UFW rules, systemd service.
# Creates .env from .env.example if one does not already exist.
# Safe to re-run — all steps are idempotent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
INSTALL_DIR="${INSTALL_DIR:-/opt/parkshare}"

echo "==> [1/6] Installing system packages"
apt-get update -q
apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    ufw

echo "==> [2/6] Installing Docker Engine + Compose plugin"
if ! command -v docker &>/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list

    apt-get update -q
fi

apt-get install -y --no-install-recommends \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin

systemctl enable --now docker

echo "==> [3/6] Configuring UFW firewall"
# Reset to known state without disabling existing ssh rule
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp   comment 'SSH'
# No 80/443 here: TLS terminates on Nexus and is reverse-proxied to :8001;
# there is no local Caddy in docker-compose.yml, so opening 80/443 advertises
# services that do not exist on this host (ADR-002 Decision E).
ufw --force enable
ufw status verbose

echo "==> [4/6] Deploying application files to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
rsync -a --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
    "$PROJECT_ROOT/" "$INSTALL_DIR/"

# Create .env from example if it does not exist
ENV_FILE="$INSTALL_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    cp "$INSTALL_DIR/.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo ""
    echo "  !! ACTION REQUIRED: Edit $ENV_FILE and replace all placeholder values."
    echo "     Key fields:"
    echo "       SECRET_KEY          — generate with: python3 -c \"import secrets; print(secrets.token_urlsafe(50))\""
    echo "       POSTGRES_PASSWORD   — strong random password"
    echo "       PII_ENCRYPTION_KEY  — generate with: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
    echo "       VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY — generate with: python3 -m pywebpush generate-keys"
    echo "       BREVO_API_KEY       — from your Brevo account"
    echo ""
    read -r -p "  Press ENTER once you have saved $ENV_FILE, or Ctrl-C to abort and edit first. "
else
    echo "  .env already exists — skipping template copy."
fi

echo "==> [5/6] Pulling images and starting stack"
cd "$INSTALL_DIR"
docker compose pull --quiet
docker compose build
docker compose up -d

echo "  Waiting for the database to accept connections..."
# Poll the db container's pg_isready instead of a blind sleep (CPS#170/#165).
# compose already gates `web` on the db healthcheck, but the migrate exec below
# can still race a DB that only just became reachable.
for i in $(seq 1 30); do
    if docker compose exec -T db pg_isready -q; then
        echo "  Database is ready."
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "  !! Database did not become ready within ~60s." >&2
        exit 1
    fi
    sleep 2
done

docker compose exec -T web python manage.py migrate --no-input
docker compose exec -T web python manage.py collectstatic --no-input --clear

# Optional first-run admin bootstrap (issue #172).
# No-op unless ADMIN_EMAIL, ORG_NAME and ORG_HOSTNAME are all set in the
# environment. When set, this creates the first operator admin (org + superuser
# + confirmed TOTP device) idempotently — safe to re-run, it does nothing once
# the admin exists. The generated/used password and the otpauth:// 2FA URI are
# printed ONCE to the output below; capture them now.
#   ADMIN_PASSWORD is read from the env by the command (--password-from-env);
#   if unset/empty the command errors, so set it or drop --password-from-env to
#   have a strong password generated and printed.
# Note: django_ratelimit may raise system check E003 on this host (#147); if the
# command aborts on checks, add --skip-checks to the invocation below.
if [[ -n "${ADMIN_EMAIL:-}" && -n "${ORG_NAME:-}" && -n "${ORG_HOSTNAME:-}" ]]; then
    echo "==> Bootstrapping first-run admin ($ADMIN_EMAIL)"
    # Forward ADMIN_PASSWORD by NAME only (`-e ADMIN_PASSWORD`, no value) so the
    # cleartext password never appears in the host `docker compose` argv — which
    # is readable by any local user via `ps -ww`. The name-only form tells
    # compose to read the value from this script's environment and inject it
    # into the container, keeping it off the command line. We export it
    # (defaulting to empty if unset) so it is present for that forwarding; the
    # command itself errors out cleanly when the value is empty.
    export ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
    docker compose exec -T \
        -e ADMIN_PASSWORD \
        web python manage.py bootstrap_admin \
        --email "$ADMIN_EMAIL" \
        --org-name "$ORG_NAME" \
        --org-hostname "$ORG_HOSTNAME" \
        ${ORG_SUPPORT_EMAIL:+--org-support-email "$ORG_SUPPORT_EMAIL"} \
        --password-from-env ADMIN_PASSWORD
else
    echo "  Skipping admin bootstrap (ADMIN_EMAIL/ORG_NAME/ORG_HOSTNAME not all set)."
fi

echo "==> [6/6] Installing systemd service (parkshare.service)"
cat > /etc/systemd/system/parkshare.service <<EOF
[Unit]
Description=CondoParkShare Docker Compose stack
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable parkshare.service

echo ""
echo "Run setup complete."
echo "  Stack status:    docker compose -f $INSTALL_DIR/docker-compose.yml ps"
echo "  App logs:        docker compose -f $INSTALL_DIR/docker-compose.yml logs -f web"
echo "  Restart on boot: systemctl status parkshare"
