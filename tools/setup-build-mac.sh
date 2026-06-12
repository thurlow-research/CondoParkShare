#!/usr/bin/env bash
# setup-build-mac.sh — Install everything needed to BUILD CondoParkShare on macOS.
# Installs: Homebrew, Xcode CLT, Docker Desktop, Python 3.12, libpq.
# Safe to re-run — all steps are idempotent.
# Run as your normal user (NOT sudo).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [[ "$(uname)" != "Darwin" ]]; then
    echo "This script is for macOS only." >&2
    exit 1
fi

echo "==> [1/5] Xcode Command Line Tools"
if ! xcode-select -p &>/dev/null; then
    echo "  Installing Xcode CLT — follow the prompt that appears..."
    xcode-select --install
    echo "  Re-run this script once the Xcode CLT install finishes."
    exit 0
else
    echo "  Already installed at $(xcode-select -p)"
fi

echo "==> [2/5] Homebrew"
if ! command -v brew &>/dev/null; then
    echo "  Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for the rest of this script
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    else
        eval "$(/usr/local/bin/brew shellenv)"
    fi
else
    echo "  Already installed: $(brew --version | head -1)"
    brew update --quiet
fi

echo "==> [3/5] Docker Desktop + Python 3.12 + libpq"
brew install --quiet python@3.12 libpq
brew install --quiet --cask docker 2>/dev/null || echo "  Docker Desktop already installed."

# Ensure docker CLI is reachable (Docker Desktop puts it in /usr/local/bin)
if ! command -v docker &>/dev/null; then
    echo ""
    echo "  !! Open Docker Desktop at least once to complete installation, then re-run this script."
    open -a Docker 2>/dev/null || true
    exit 0
fi

# Docker Desktop must be running to build
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

echo "==> [4/5] Python virtual environment"
PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
VENV="$PROJECT_ROOT/.venv"

if [[ ! -d "$VENV" ]]; then
    "$PYTHON" -m venv "$VENV"
fi

"$VENV/bin/pip" install --upgrade pip wheel

# libpq is keg-only on Homebrew — point the compiler at it
LIBPQ_PREFIX="$(brew --prefix libpq)"
export LDFLAGS="-L$LIBPQ_PREFIX/lib"
export CPPFLAGS="-I$LIBPQ_PREFIX/include"

"$VENV/bin/pip" install --no-cache-dir -r "$PROJECT_ROOT/requirements.txt"
echo "  Virtual environment ready at $VENV"
echo "  Activate with: source $VENV/bin/activate"

echo "==> [5/5] Building Docker image"
cd "$PROJECT_ROOT"
docker build -t condoparkshare:build .

echo ""
echo "Build setup complete."
echo "  Docker image: condoparkshare:build"
echo "  Python venv:  $VENV"
