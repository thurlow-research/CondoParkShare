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
ufw allow 80/tcp   comment 'HTTP (Caddy)'
ufw allow 443/tcp  comment 'HTTPS (Caddy)'
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
    echo "       CF_API_TOKEN        — from Cloudflare (needed for DNS-01 TLS on kumajyo.com)"
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

echo "  Waiting 10 s for the database to be ready..."
sleep 10

docker compose exec -T web python manage.py migrate --no-input
docker compose exec -T web python manage.py collectstatic --no-input --clear

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
echo "  Caddy logs:      docker compose -f $INSTALL_DIR/docker-compose.yml logs -f caddy"
echo "  Restart on boot: systemctl status parkshare"
