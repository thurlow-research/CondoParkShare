#!/usr/bin/env bash
# oversight-env.sh — source before running the HOS oversight pipeline on this repo.
#   source scripts/oversight-env.sh
#
# Why this exists: CondoParkShare ships a Django app named `operator/`, which
# shadows Python's stdlib `operator` module whenever python runs with the project
# root on sys.path (the inline `python -c`/`-m` calls inside the oversight
# scripts). PYTHONSAFEPATH=1 drops cwd from sys.path; PYTHONPATH re-adds the
# validators dir so their sibling `schema` import still resolves.
_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$_ROOT/.venv/bin:$HOME/.local/bin:$PATH"
export PYTHON="$_ROOT/.venv/bin/python"
export PYTHONSAFEPATH=1
export PYTHONPATH="$_ROOT/scripts/oversight/validators${PYTHONPATH:+:$PYTHONPATH}"
