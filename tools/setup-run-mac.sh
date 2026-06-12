#!/usr/bin/env bash
# setup-run-mac.sh — Set up and run the CondoParkShare stack locally on macOS.
# Installs: Homebrew, Docker Desktop. Starts the full Compose stack in place.
# Creates .env from .env.example if one does not already exist.
# Safe to re-run — all steps are idempotent.
# Run as your normal user (NOT sudo).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [[ "$(uname)" != "Darwin" ]]; then
    echo "This script is for macOS only." >&2
    exit 1
fi

echo "==> [1/4] Homebrew + Docker Desktop"
if ! command -v brew &>/dev/null; then
    echo "  Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    else
        eval "$(/usr/local/bin/brew shellenv)"
    fi
else
    brew update --quiet
fi

brew install --quiet --cask docker 2>/dev/null || echo "  Docker Desktop already installed."

if ! command -v docker &>/dev/null; then
    echo ""
    echo "  !! Open Docker Desktop at least once to complete installation, then re-run this script."
    open -a Docker 2>/dev/null || true
    exit 0
fi

if ! docker info &>/dev/null; then
    echo "  Starting Docker Desktop..."
    open -a Docker
    echo "  Waiting for Docker to be ready (up to 60 s)..."
    for i in $(seq 1 30); do
        docker info &>/dev/null && break
        sleep 2
    done
    docker info &>/dev/null || { echo "  Docker did not start in time. Open Docker Desktop manually and re-run."; exit 1; }
fi

echo "==> [2/4] Environment file"
ENV_FILE="$PROJECT_ROOT/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    cp "$PROJECT_ROOT/.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo ""
    echo "  !! ACTION REQUIRED: Edit $ENV_FILE and replace all placeholder values."
    echo "     Key fields for local dev:"
    echo "       SECRET_KEY          — generate with: python3 -c \"import secrets; print(secrets.token_urlsafe(50))\""
    echo "       POSTGRES_PASSWORD   — any local password"
    echo "       PII_ENCRYPTION_KEY  — generate with: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
    echo "       VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY — generate with: python3 -m pywebpush generate-keys"
    echo "       BREVO_API_KEY / CF_API_TOKEN — can be set to dummy values for local dev"
    echo ""
    read -r -p "  Press ENTER once you have saved $ENV_FILE, or Ctrl-C to abort and edit first. "
else
    echo "  .env already exists — skipping template copy."
fi

echo "==> [3/4] Building and starting the stack"
cd "$PROJECT_ROOT"
docker compose build
docker compose up -d

echo "  Waiting 10 s for the database to be ready..."
sleep 10

docker compose exec -T web python manage.py migrate --no-input
docker compose exec -T web python manage.py collectstatic --no-input --clear

echo "==> [4/4] Done"
echo ""
echo "  App:       http://localhost (via Caddy) or http://localhost:8000 (direct)"
echo "  Status:    docker compose ps"
echo "  Logs:      docker compose logs -f web"
echo "  Stop:      docker compose down"
echo ""
echo "  To enable Docker Desktop auto-start on login:"
echo "    Open Docker Desktop → Settings → General → Start Docker Desktop when you log in"
