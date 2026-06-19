#!/usr/bin/env bash
# ensure_venv.sh — Create the oversight pip venv if it does not exist.
#
# Source this from other oversight scripts to export VENV, VENV_BIN, and
# OVERSIGHT_PYTHON without activating the venv globally (no PATH pollution).
#
#   source "$(dirname "${BASH_SOURCE[0]}")/ensure_venv.sh"        # from scripts/oversight/
#   source "$GATES_DIR/../ensure_venv.sh"                          # from scripts/oversight/gates/
#
# Or run standalone to create / verify the venv:
#   ./scripts/oversight/ensure_venv.sh
#
# The venv lives at scripts/oversight/.venv and is git-ignored (.venv/).
# On first use it is created from scripts/oversight/requirements.txt.
# Ubuntu 24.04+ (PEP 668 / EXTERNALLY-MANAGED): system and --user pip are
# blocked; this venv is the intended install path.

_ENSURE_VENV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$_ENSURE_VENV_DIR/.venv"
VENV_BIN="$VENV/bin"
OVERSIGHT_PYTHON="$VENV_BIN/python3"

GREEN="\033[32m"; CYAN="\033[36m"; RED="\033[31m"; YELLOW="\033[33m"; RESET="\033[0m"

warn() { echo -e "  ${YELLOW}⚠${RESET}  $*" >&2; }

# Detect a stale venv: shebangs embed the absolute venv path at creation time.
# After a repo move the shebang points to the old location and tools break with
# "bad interpreter: No such file or directory".  If the pip shebang doesn't
# start with the current VENV path, the venv was built elsewhere — delete it so
# the creation block below rebuilds it in the right place.
_check_venv_stale() {
  local pip="$VENV/bin/pip"
  [[ -f "$pip" ]] || return 0  # doesn't exist yet, not stale
  local shebang
  shebang=$(head -1 "$pip" 2>/dev/null)
  if [[ "$shebang" != "#!${VENV}/"* ]]; then
    return 1  # stale
  fi
  return 0
}

if ! _check_venv_stale; then
  warn "Venv shebang is stale (repo may have moved) — rebuilding venv"
  rm -rf "$VENV"
fi

if [[ ! -x "$OVERSIGHT_PYTHON" ]]; then
    echo -e "  ${CYAN}→${RESET}  oversight venv not found — creating at $VENV"
    if ! python3 -m venv "$VENV"; then
        echo -e "  ${RED}✘${RESET}  python3 -m venv failed — is python3 (>= 3.8) installed?"
        exit 1
    fi
    echo -e "  ${CYAN}→${RESET}  installing oversight dependencies (this runs once) ..."
    "$VENV_BIN/pip" install --quiet --upgrade pip
    "$VENV_BIN/pip" install --quiet -r "$_ENSURE_VENV_DIR/requirements.txt"
    echo -e "  ${GREEN}✔${RESET}  oversight venv ready"
fi

export VENV VENV_BIN OVERSIGHT_PYTHON

# When executed directly (not sourced): print venv status and exit.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "Oversight venv : $VENV"
    echo "Python         : $("$OVERSIGHT_PYTHON" --version 2>&1)"
    echo ""
    "$VENV_BIN/pip" list --format=columns 2>/dev/null
fi
