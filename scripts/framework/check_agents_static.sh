#!/usr/bin/env bash
# check_agents_static.sh — fast static consistency checker for the agent pipeline.
#
# No AI calls. Checks structural correctness deterministically:
#   1. Every agent named in docs/AGENTS.md has a .claude/agents/*.md file
#   2. Every file path referenced in agent files exists on disk
#   3. Every agent named in an "escalates to" / "invoked by" line resolves to a known agent
#   4. The project-start doc outputs referenced in agent files share a consistent path
#
# Exit codes:
#   0 — clean
#   1 — one or more findings (blocking)
#   2 — usage error or missing required files
#
# Usage:
#   ./scripts/framework/check_agents_static.sh
#   ./scripts/framework/check_agents_static.sh --agents-dir .claude/agents --docs docs/AGENTS.md
#   ./scripts/framework/check_agents_static.sh --quiet   # suppress per-finding output; only print summary

set -euo pipefail

AGENTS_DIR=".claude/agents"
DOCS_AGENTS="docs/AGENTS.md"
QUIET=false
FINDINGS=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --agents-dir) AGENTS_DIR="$2"; shift 2 ;;
        --docs)       DOCS_AGENTS="$2"; shift 2 ;;
        --quiet)      QUIET=true; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

fail() { echo "  FAIL: $1"; FINDINGS=$(( FINDINGS + 1 )); }
warn() { $QUIET || echo "  WARN: $1"; }
ok()   { $QUIET || echo "  OK:   $1"; }
section() { $QUIET || echo ""; $QUIET || echo "── $1 ──────────────────────────────────────────"; }

if [[ ! -d "$AGENTS_DIR" ]]; then
    echo "ERROR: agents directory not found: $AGENTS_DIR" >&2; exit 2
fi

# ── 1. Build canonical agent name list from .claude/agents/ ─────────────────
section "1. Agent file inventory"

# Collect names into a newline-separated string (bash 3.2 compatible — no -A)
KNOWN_AGENTS=""
while IFS= read -r -d '' f; do
    name=$(grep -m1 '^name:' "$f" | sed 's/^name:[[:space:]]*//' | tr -d '[:space:]')
    if [[ -n "$name" ]]; then
        KNOWN_AGENTS="${KNOWN_AGENTS}${name}
"
        ok "$name → $f"
    else
        warn "No 'name:' frontmatter in $f"
    fi
done < <(find "$AGENTS_DIR" -name '*.md' -print0)

# ── 2. Every agent named in docs/AGENTS.md has a file ───────────────────────
section "2. docs/AGENTS.md agent name coverage"

if [[ -f "$DOCS_AGENTS" ]]; then
    # Extract backtick-quoted names on lines starting with ### N. `name`
    while IFS= read -r line; do
        agent=$(echo "$line" | grep -oE '\`[a-z][a-z0-9_-]+\`' | head -1 | tr -d '`')
        [[ -z "$agent" ]] && continue
        if echo "$KNOWN_AGENTS" | grep -qx "$agent"; then
            ok "$agent referenced in docs and has agent file"
        else
            fail "$agent referenced in $DOCS_AGENTS but no .claude/agents/$agent.md found"
        fi
    done < <(grep '^### [0-9]' "$DOCS_AGENTS")
else
    warn "$DOCS_AGENTS not found — skipping doc coverage check"
fi

# ── 3. File paths in agent system prompts exist on disk ─────────────────────
section "3. File path references in agent files"

# Only check paths that contain a directory separator — bare filenames like
# `tokens.css` or `TECHNICAL-DESIGN.md` are prose shorthand, not path claims.
# Output documents produced at project-start don't exist yet — exempt them.
OUTPUT_DOCS="docs/pm/CONFIRMED-REQUIREMENTS.md
docs/design/UX-DESIGN-READINESS.md
docs/architecture/ADR-001-pilot.md
docs/design/TECHNICAL-DESIGN.md"

while IFS= read -r -d '' f; do
    agent_name=$(grep -m1 '^name:' "$f" | sed 's/^name:[[:space:]]*//' | tr -d '[:space:]')
    # Require at least one slash to count as a path claim; strip #anchor fragments
    while IFS= read -r ref; do
        ref_clean=$(echo "$ref" | tr -d '`"' | sed 's/#.*//' | xargs)
        [[ "$ref_clean" == http* ]] && continue
        [[ -z "$ref_clean" ]] && continue
        [[ "$ref_clean" != */* ]] && continue   # skip bare filenames
        # Exempt project-start output docs — they are written during the build, not beforehand
        if echo "$OUTPUT_DOCS" | grep -qx "$ref_clean"; then
            ok "[$agent_name] $ref_clean (output doc — existence not required)"
            continue
        fi
        if [[ -e "$ref_clean" ]]; then
            ok "[$agent_name] $ref_clean"
        else
            fail "[$agent_name] referenced path not found: $ref_clean"
        fi
    done < <(grep -oE '`[A-Za-z][A-Za-z0-9_./-]+/[A-Za-z0-9_./-]+\.(md|yaml|html|css|sh|py|json)[^`]*`' "$f" \
             | tr -d '`' \
             | grep -v '^http' \
             || true)
done < <(find "$AGENTS_DIR" -name '*.md' -print0)

# ── 4. Escalation targets resolve to known agents ────────────────────────────
section "4. Escalation target resolution"

# Patterns to catch: → agent-name, escalate to agent-name, invoked by agent-name
# We look for backtick-quoted names that follow escalation keywords.
ESCALATION_RE='(escalates?[[:space:]]+to|invoke|receives[[:space:]]+from|invoked[[:space:]]+by|notify|notif[yi]es)[^`]*`([a-z][a-z0-9_-]+)`'

# Generic tokens that appear in escalation-like phrases but are never agent names.
# Do NOT add project-specific hostnames or service names here — those belong in
# scripts/framework/config.sh as PROJECT_NON_AGENT_TOKENS.
NON_AGENT_TOKENS="human|you|main|build|prod|staging|ci|github|pr"
# Load project-specific overrides if config exists
[[ -f "scripts/framework/config.sh" ]] && source scripts/framework/config.sh
NON_AGENT_TOKENS="${NON_AGENT_TOKENS}${PROJECT_NON_AGENT_TOKENS:+|$PROJECT_NON_AGENT_TOKENS}"

while IFS= read -r -d '' f; do
    agent_name=$(grep -m1 '^name:' "$f" | sed 's/^name:[[:space:]]*//' | tr -d '[:space:]')
    # Use python for multiline regex since bash ERE can't span lines
    while IFS= read -r target; do
        [[ -z "$target" ]] && continue
        # Skip known non-agent tokens (hostnames, placeholders, env terms)
        if echo "$target" | grep -qE "^($NON_AGENT_TOKENS)$"; then
            continue
        fi
        # Agent names either contain a hyphen (e.g. code-reviewer, pm-agent)
        # OR are single known short names. Skip library names, types, and status values.
        KNOWN_SHORT_AGENTS="architect|coder|human"
        if ! echo "$target" | grep -qE "^($KNOWN_SHORT_AGENTS)$" && \
           ! echo "$target" | grep -q '-'; then
            continue
        fi
        if echo "$KNOWN_AGENTS" | grep -qx "$target"; then
            ok "[$agent_name] → $target resolves"
        else
            fail "[$agent_name] escalates/notifies '$target' but no agent file found for it"
        fi
    done < <(python3 -c "
import re, sys
text = open('$f').read()
pattern = r'(?:escalat\w+\s+to|invok\w+|receives?\s+from|notif\w+)[^\`]*\`([a-z][a-z0-9_-]+)\`'
for m in re.findall(pattern, text, re.IGNORECASE):
    print(m)
" 2>/dev/null || true)
done < <(find "$AGENTS_DIR" -name '*.md' -print0)

# ── 5. Project-start output doc paths are consistent ────────────────────────
section "5. Project-start output document path consistency"

# The four expected project-start output docs (bash 3.2: plain arrays, not associative)
DOC_CANONICALS=(
    "docs/pm/CONFIRMED-REQUIREMENTS.md"
    "docs/design/UX-DESIGN-READINESS.md"
    "docs/architecture/ADR-001-pilot.md"
    "docs/design/TECHNICAL-DESIGN.md"
)

for canonical in "${DOC_CANONICALS[@]}"; do
    basename_only=$(basename "$canonical")
    # Only check agent files — docs use shorthand in prose legitimately.
    # Only flag backtick-quoted references (navigation claims), not prose mentions.
    all_refs=$(grep -rl "$basename_only" "$AGENTS_DIR" 2>/dev/null || true)
    while IFS= read -r ref_file; do
        [[ -z "$ref_file" ]] && continue
        agent_self=$(grep -m1 '^name:' "$ref_file" | sed 's/^name:[[:space:]]*//' | tr -d '[:space:]')
        # Find lines with backtick-quoted bare basename (no directory component before it)
        wrong=$(grep -n "\`${basename_only}\`" "$ref_file" \
                | grep -v "$canonical" \
                | grep -v "^\s*#\|^\s*<!--" \
                || true)
        if [[ -n "$wrong" ]]; then
            fail "[$ref_file] uses bare \`$basename_only\` — should be \`$canonical\`:"
            echo "$wrong" | head -3
        else
            ok "[$ref_file] $basename_only path consistent"
        fi
    done <<< "$all_refs"
done

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
if [[ $FINDINGS -eq 0 ]]; then
    echo "  PASS — agent static checks clean (0 findings)"
    echo "═══════════════════════════════════════════════════════"
    exit 0
else
    echo "  FAIL — $FINDINGS finding(s) require attention"
    echo "═══════════════════════════════════════════════════════"
    exit 1
fi
