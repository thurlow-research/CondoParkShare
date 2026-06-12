#!/usr/bin/env bash
# setup-build.sh — Install everything needed to BUILD the CondoParkShare Docker image on Ubuntu.
# Installs: Docker Engine + Compose plugin, Python 3.12 dev tools, libpq-dev (for psycopg2).
# Safe to re-run — all steps are idempotent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "==> [1/4] Installing system packages"
apt-get update -q
apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    gnupg \
    lsb-release \
    python3.12 \
    python3.12-venv \
    python3.12-dev \
    python3-pip \
    libpq-dev \
    gcc \
    make

echo "==> [2/4] Installing Docker Engine + Compose plugin"
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

# Allow the invoking user (if not root) to run docker without sudo
if [[ -n "${SUDO_USER:-}" ]]; then
    usermod -aG docker "$SUDO_USER"
    echo "    NOTE: log out and back in for docker group membership to take effect."
fi

echo "==> [3/4] Setting up Python virtual environment for local dev / tests"
VENV="$PROJECT_ROOT/.venv"
if [[ ! -d "$VENV" ]]; then
    python3.12 -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip wheel
"$VENV/bin/pip" install --no-cache-dir -r "$PROJECT_ROOT/requirements.txt"
echo "    Virtual environment ready at $VENV"
echo "    Activate with: source $VENV/bin/activate"

echo "==> [4/4] Building Docker image"
cd "$PROJECT_ROOT"
docker build -t condoparkshare:build .

echo ""
echo "Build setup complete."
echo "  Docker image: condoparkshare:build"
echo "  Python venv:  $VENV"
