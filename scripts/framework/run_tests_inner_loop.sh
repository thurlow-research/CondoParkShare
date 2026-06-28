#!/usr/bin/env bash
# run_tests_inner_loop.sh — Run the inner-loop test suite (required for PR approval).
#
# Skips tests marked @pytest.mark.slow or @pytest.mark.integration.
# These are the tests that must pass before opening or merging a PR.
# Expected runtime: < 60s.
#
# Usage:
#   ./scripts/framework/run_tests_inner_loop.sh            # run inner-loop tests
#   ./scripts/framework/run_tests_inner_loop.sh --help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

CYAN="\033[36m"
RED="\033[31m"; BOLD="\033[1m"; RESET="\033[0m"

case "${1:-}" in
  --help|-h)
    echo "Usage: $0 [pytest-args...]"
    echo ""
    echo "Runs the inner-loop test suite — all tests except @pytest.mark.slow"
    echo "and @pytest.mark.integration. Required to pass before PR approval."
    echo ""
    echo "For the full release suite: ./scripts/framework/run_tests_release.sh"
    exit 0
    ;;
esac

echo -e "${BOLD}HOS Inner-Loop Tests${RESET} (PR-required tier)"
echo -e "  ${CYAN}→${RESET}  Skipping: @slow, @integration"
echo -e "  ${CYAN}→${RESET}  Repo: $REPO_ROOT"
echo ""

# Activate the oversight venv (where pytest lives)
VENV="$REPO_ROOT/scripts/oversight/.venv"
if [ -f "$VENV/bin/python" ]; then
  PYTHON="$VENV/bin/python"
elif command -v python3 &>/dev/null; then
  PYTHON="python3"
else
  echo -e "  ${RED}✘${RESET}  python not found — run: ./bootstrap/hos_bootstrap.sh"
  exit 1
fi

cd "$REPO_ROOT"
"$PYTHON" -m pytest -m "not slow and not integration" "$@"
