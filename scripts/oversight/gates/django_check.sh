#!/usr/bin/env bash
# django_check.sh — Django system check gate (blocking on errors).
#
# Runs `manage.py check` to verify the Django app can import and start.
# This catches stdlib-shadowing app names, missing settings, and broken
# INSTALLED_APPS entries before any reviewer sees the code.
#
# Skips gracefully if manage.py is not present (non-Django projects).
#
# Python resolution order:
#   1. $DJANGO_PYTHON env var (explicit override)
#   2. .venv/bin/python  (project venv at repo root)
#   3. venv/bin/python
#   4. python3 (system — last resort, may lack project deps)
#
# Exit 0 = check passed or no manage.py. Exit 1 = Django errors or Python unavailable.
#
# Usage: ./django_check.sh
#        DJANGO_PYTHON=/path/to/python ./django_check.sh

set -euo pipefail

_GATES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/oversight/gates/check_suspension.sh
source "$_GATES_DIR/check_suspension.sh"
is_suspended "django" && { print_suspended "django"; exit 0; }

echo "=== django manage.py check ==="

if [[ ! -f "manage.py" ]]; then
    echo "SKIP: manage.py not found (not a Django project)"
    exit 0
fi

# Resolve Python — prefer the project's own venv so Django and all deps are present.
if [[ -n "${DJANGO_PYTHON:-}" ]]; then
    PY="$DJANGO_PYTHON"
elif [[ -x ".venv/bin/python" ]]; then
    PY=".venv/bin/python"
elif [[ -x "venv/bin/python" ]]; then
    PY="venv/bin/python"
elif command -v python3 &>/dev/null; then
    PY="python3"
else
    echo "GATE FAIL: no Python interpreter found — manage.py check cannot run (#764)"
    echo "  Install python3 (or set DJANGO_PYTHON) and re-run."
    exit 1
fi

echo "Python: $PY"

if "$PY" manage.py check 2>&1; then
    echo "GATE PASS: Django system check clean"
    exit 0
else
    echo ""
    echo "GATE FAIL: Django reported check errors — the app cannot start"
    echo "  Common causes:"
    echo "    - App name shadows a Python stdlib module (rename the app)"
    echo "    - Missing or misconfigured INSTALLED_APPS entry"
    echo "    - Import error in models.py or apps.py"
    exit 1
fi
