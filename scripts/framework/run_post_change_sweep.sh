#!/usr/bin/env bash
# run_post_change_sweep.sh — shell entrypoint for the post-change sweep.
#
# Categorizes changed files by domain and prints the list of agents to invoke,
# along with the files each agent should receive. Use this as a quick pre-check
# or to generate the routing context before invoking the post-change-sweep agent.
#
# In a Claude Code session, the post-change-sweep AGENT does the actual invoking.
# This script handles the categorization and reporting that a human or CI job needs
# to kick off the right set of reviews.
#
# Usage:
#   ./scripts/framework/run_post_change_sweep.sh               # diff vs HEAD
#   ./scripts/framework/run_post_change_sweep.sh HEAD~1        # diff vs HEAD~1
#   ./scripts/framework/run_post_change_sweep.sh --staged      # staged files only
#   ./scripts/framework/run_post_change_sweep.sh file1 file2   # explicit files
#   ./scripts/framework/run_post_change_sweep.sh --framework-only  # framework track only
#
# Exit codes:
#   0 — routing complete (does not indicate review pass/fail)
#   1 — no changed files detected
#   2 — usage error

set -euo pipefail

FRAMEWORK_ONLY=false
BASE_REF=""
EXPLICIT_FILES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --framework-only) FRAMEWORK_ONLY=true; shift ;;
        --staged)         BASE_REF="--cached";  shift ;;
        HEAD*)            BASE_REF="$1";        shift ;;
        -*)               echo "Unknown option: $1" >&2; exit 2 ;;
        *)                EXPLICIT_FILES+=("$1"); shift ;;
    esac
done

# ── Collect changed files ────────────────────────────────────────────────────
if [[ ${#EXPLICIT_FILES[@]} -gt 0 ]]; then
    CHANGED=$(printf '%s\n' "${EXPLICIT_FILES[@]}")
elif [[ "$BASE_REF" == "--cached" ]]; then
    CHANGED=$(git diff --cached --name-only 2>/dev/null || true)
elif [[ -n "$BASE_REF" ]]; then
    CHANGED=$(git diff --name-only "$BASE_REF" 2>/dev/null || true)
else
    # Uncommitted (staged + unstaged) + last commit
    CHANGED=$(git diff --name-only HEAD 2>/dev/null || true)
    [[ -z "$CHANGED" ]] && CHANGED=$(git diff --name-only HEAD~1 2>/dev/null || true)
fi

if [[ -z "$CHANGED" ]]; then
    echo "No changed files detected — nothing to route."
    exit 1
fi

echo "Changed files:"
echo "$CHANGED" | sed 's/^/  /'
echo ""

# ── Categorize by domain ─────────────────────────────────────────────────────
categorize() {
    local file="$1"
    local domains=""

    # Framework
    if echo "$file" | grep -qE '^\.claude/agents/|^docs/AGENTS\.md$|^docs/OVERSIGHT-RUNBOOK\.md$|^scripts/framework/'; then
        domains="$domains framework"
    fi

    if $FRAMEWORK_ONLY; then
        echo "$domains"; return
    fi

    # Application code (Python, excluding tests, migrations, and framework/oversight scripts)
    if echo "$file" | grep -qE '\.py$' && \
       ! echo "$file" | grep -qE '^tests/|/test_|/migrations/|conftest\.py|^scripts/'; then
        domains="$domains application-code"
    fi

    # Migrations
    if echo "$file" | grep -qE '/migrations/.*\.py$'; then
        domains="$domains migrations"
    fi

    # Templates
    if echo "$file" | grep -qE '/templates/.*\.html$'; then
        domains="$domains templates"
    fi

    # Tests
    if echo "$file" | grep -qE '^tests/|/test_.*\.py$|conftest\.py$'; then
        domains="$domains tests"
    fi

    # Infrastructure
    if echo "$file" | grep -qE '^docker-compose\.yml$|^Caddyfile$|\.env\.example$|^scripts/backup\.sh$'; then
        domains="$domains infrastructure"
    fi

    # Design pack
    if echo "$file" | grep -qE '^Specs/.*design.pack/'; then
        domains="$domains design-pack"
    fi

    # Spec (non-design)
    if echo "$file" | grep -qE '^Specs/.*\.md$' && \
       ! echo "$file" | grep -qE 'design.pack'; then
        domains="$domains spec"
    fi

    echo "${domains# }"
}

# ── Build per-domain file lists ──────────────────────────────────────────────
declare_domain() { eval "${1}_FILES=''"; }
add_to_domain()  { eval "${1}_FILES=\"\${${1}_FILES}\${2}
\""; }
get_domain()     { eval "echo \"\${${1}_FILES}\"" | grep -v '^$' || true; }

for d in framework application_code migrations templates tests infrastructure design_pack spec; do
    declare_domain "$d"
done

while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    domains=$(categorize "$file")
    for d in $domains; do
        key=$(echo "$d" | tr '-' '_')
        add_to_domain "$key" "$file"
    done
done <<< "$CHANGED"

# ── Print routing plan ───────────────────────────────────────────────────────
print_domain() {
    local label="$1"
    local key=$(echo "$label" | tr '-' '_')
    local files
    files=$(get_domain "$key")
    if [[ -n "$files" ]]; then
        printf "  %-20s %s\n" "${label}:" "$(echo "$files" | tr '\n' ' ')"
    fi
}

echo "Domain routing:"
print_domain "framework"
print_domain "application-code"
print_domain "migrations"
print_domain "templates"
print_domain "tests"
print_domain "infrastructure"
print_domain "design-pack"
print_domain "spec"
echo ""

# ── Print agent invocation plan ──────────────────────────────────────────────
echo "Agents to invoke:"

HAS_FRAMEWORK=$(get_domain "framework")
HAS_CODE=$(get_domain "application_code")
HAS_MIGRATIONS=$(get_domain "migrations")
HAS_TEMPLATES=$(get_domain "templates")
HAS_TESTS=$(get_domain "tests")
HAS_INFRA=$(get_domain "infrastructure")
HAS_DESIGN=$(get_domain "design_pack")
HAS_SPEC=$(get_domain "spec")

if [[ -n "$HAS_FRAMEWORK" ]]; then
    echo "  Track 1 — Framework (independent):"
    echo "    framework-validator"
fi

if ! $FRAMEWORK_ONLY; then
    if [[ -n "$HAS_CODE" || -n "$HAS_MIGRATIONS" ]]; then
        echo "  Track 2 — Code review (sequential then parallel):"
        echo "    1. code-reviewer"
        echo "    2. (parallel, after code-reviewer approves):"
        echo "       security-reviewer"
        [[ -n "$HAS_CODE" ]] && grep -qE 'accounts|booking|erasure|pii' \
            <(echo "${HAS_CODE}${HAS_MIGRATIONS}" | tr '[:upper:]' '[:lower:]') 2>/dev/null \
            && echo "       privacy-reviewer (PII-relevant files detected)"  \
            || echo "       privacy-reviewer (check if PII-relevant)"
        [[ -n "$HAS_TEMPLATES" ]] && echo "       ui-reviewer"
        [[ -n "$HAS_TEMPLATES" ]] && echo "       a11y-reviewer"
        [[ -n "$HAS_INFRA"     ]] && echo "       infra-reviewer"
    fi

    [[ -n "$HAS_TESTS"   ]] && echo "  Track 3 — Tests (independent): unit-test"
    [[ -n "$HAS_DESIGN"  ]] && echo "  Track 4 — Design pack (independent): ux-designer → ui-reviewer"
    [[ -n "$HAS_SPEC"    ]] && echo "  Track 5 — Spec (independent): pm-agent"

    if [[ -z "$HAS_CODE$HAS_MIGRATIONS$HAS_TEMPLATES$HAS_TESTS$HAS_INFRA$HAS_DESIGN$HAS_SPEC" ]]; then
        echo "  (no application/infra/spec changes — framework track only)"
    fi
fi

echo ""
echo "To run: invoke the post-change-sweep agent in Claude Code."
echo "  'Run post-change sweep' — it will read this categorization and invoke all listed agents."
