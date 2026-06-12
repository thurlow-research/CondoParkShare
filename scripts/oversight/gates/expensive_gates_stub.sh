#!/usr/bin/env bash
# expensive_gates_stub.sh — placeholder + static container-start check.
#
# This stage runs after the inner loop and before the cross-vendor panel.
# Project-specific expensive gates (e2e tests, coverage thresholds, etc.)
# belong here — copy to your project and replace the stub section.
#
# What this stub includes:
#   - Static container-start check: verifies every executable declared in
#     Dockerfile CMD/ENTRYPOINT or docker-compose command: appears in
#     requirements*.txt or is a known standard binary. Catches the class of
#     defect where a process manager (gunicorn, uvicorn, celery) is referenced
#     in container config but absent from requirements (HOS issue #8 defect 4).
#     No Docker daemon required — purely static.
#
# STATUS: static check is ACTIVE. Project-specific expensive gates are 🔧 planned.
#
# To add project-specific gates:
#   1. Copy this file to your project's scripts/oversight/gates/
#   2. Add your gate commands in the "PROJECT-SPECIFIC" section below
#   3. Exit 0 on pass, exit 1 on failure (blocks pipeline)
#
# Example project-specific gates:
#   - pytest tests/e2e/ --timeout=120
#   - coverage report --fail-under=90
#   - docker compose run --rm web python manage.py check --deploy

set -euo pipefail

ERRORS=0

# ── Static container-start requirements check ────────────────────────────────
# Scans Dockerfile*/docker-compose*.yml for declared executables in CMD,
# ENTRYPOINT, and compose command: entries. Verifies each appears in
# requirements*.txt or is a known base-image binary (python3, sh, bash, etc.).
# Catches "container declares gunicorn but requirements.txt doesn't list it."

KNOWN_BASE_BINARIES=(python python3 sh bash env tini dumb-init gunicorn uvicorn celery)

_check_binary_in_requirements() {
    local bin="$1"
    # Skip binaries that are always present in base images
    for known in "${KNOWN_BASE_BINARIES[@]}"; do
        [[ "$bin" == "$known" ]] && return 0
    done
    # Check requirements*.txt files
    local found=false
    for req in requirements*.txt requirements/*.txt 2>/dev/null; do
        [[ -f "$req" ]] || continue
        if grep -qi "^${bin}" "$req" 2>/dev/null; then
            found=true
            break
        fi
    done
    $found || return 1
    return 0
}

echo "=== static container-start requirements check ==="

CONTAINER_FILES=()
for f in Dockerfile Dockerfile.* docker-compose.yml docker-compose.yaml docker-compose.*.yml; do
    [[ -f "$f" ]] && CONTAINER_FILES+=("$f")
done

if [[ ${#CONTAINER_FILES[@]} -eq 0 ]]; then
    echo "SKIP: no Dockerfile or docker-compose files found"
else
    MISSING_BINS=()
    for cf in "${CONTAINER_FILES[@]}"; do
        # Extract CMD/ENTRYPOINT/command values — grab the first token (the binary)
        while IFS= read -r line; do
            # Dockerfile CMD/ENTRYPOINT: ["gunicorn", ...] or gunicorn ...
            if [[ "$line" =~ ^(CMD|ENTRYPOINT)[[:space:]]+\[?\"?([a-zA-Z0-9_/-]+) ]]; then
                bin="${BASH_REMATCH[2]}"
                bin="$(basename "$bin")"
                _check_binary_in_requirements "$bin" || MISSING_BINS+=("$bin (declared in $cf)")
            fi
            # docker-compose command: gunicorn ...
            if [[ "$line" =~ ^[[:space:]]*command:[[:space:]]+\"?([a-zA-Z0-9_/-]+) ]]; then
                bin="${BASH_REMATCH[1]}"
                bin="$(basename "$bin")"
                _check_binary_in_requirements "$bin" || MISSING_BINS+=("$bin (declared in $cf)")
            fi
        done < "$cf"
    done

    if [[ ${#MISSING_BINS[@]} -gt 0 ]]; then
        echo "GATE FAIL: container declares binaries not found in requirements*.txt:"
        for m in "${MISSING_BINS[@]}"; do echo "  $m"; done
        echo "  Add the package to requirements.txt or requirements/base.txt"
        ERRORS=$((ERRORS + 1))
    else
        echo "GATE PASS: all container-declared binaries accounted for"
    fi
fi

# ── PROJECT-SPECIFIC EXPENSIVE GATES ─────────────────────────────────────────
# Add project-specific gates here (e2e tests, coverage threshold, etc.)
# Example:
#   pytest tests/e2e/ --timeout=120 || ERRORS=$((ERRORS + 1))

# ─────────────────────────────────────────────────────────────────────────────
if [[ $ERRORS -gt 0 ]]; then
    echo ""
    echo "GATE FAIL: $ERRORS expensive gate check(s) failed"
    exit 1
fi

echo ""
echo "GATE PASS: all expensive gate checks passed"
exit 0
