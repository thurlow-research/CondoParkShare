#!/usr/bin/env bash
# postinstall-restore-frontmatter.sh — re-apply CondoParkShare's agent front-matter
# overrides after an HOS upgrade. HOS upgrades take agent front-matter from the
# framework template (description/tools/model are NOT preserved), so the overrides
# recorded in docs/hos-overrides.md must be re-applied here.
#
# Idempotent: safe to re-run. Exits non-zero if an override could not be applied
# OR if a required override is missing after this run (the "silent re-restriction"
# guard). Run after every `hos_install.sh`.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
A11Y="$ROOT/.claude/agents/a11y-reviewer.md"

fail() { echo "  ✘ $*" >&2; exit 1; }
ok()   { echo "  ✔ $*"; }

# ── Override 1: a11y-reviewer — restore the all-tools grant (Chrome DevTools MCP) ──
# HOS v0.3.x restricts a11y-reviewer to Read/Grep/Glob/Bash. CPS grants all tools
# (no `tools:` block) so the agent can reach the Chrome DevTools MCP for live
# audits. Remove any `tools:` block from the front-matter, preserving everything
# else (name/description/model/dispatches).
[[ -f "$A11Y" ]] || fail "a11y-reviewer.md not found at $A11Y"

python3 - "$A11Y" <<'PY'
import sys, re
p = sys.argv[1]
text = open(p).read()
m = re.match(r'^(---\n)(.*?)(\n---\n)(.*)$', text, re.S)
if not m:
    sys.exit("a11y-reviewer.md: front-matter not found")
head, fm, close, body = m.groups()
# Drop a `tools:` key and its indented list items (restores all-tools default).
lines = fm.split('\n')
out, i = [], 0
removed = False
while i < len(lines):
    if re.match(r'^tools:\s*$', lines[i]) or re.match(r'^tools:\s*\[', lines[i]):
        removed = True
        i += 1
        while i < len(lines) and re.match(r'^\s+-\s', lines[i]):
            i += 1
        continue
    out.append(lines[i]); i += 1
new = head + '\n'.join(out) + close + body
if new != text:
    open(p, 'w').write(new)
print("removed" if removed else "already-granted")
PY
ok "a11y-reviewer: all-tools grant restored (tools: restriction removed)"

# ── Assertions: fail loudly if an override silently reverted ──
if grep -qE '^tools:' "$A11Y"; then
  fail "a11y-reviewer still has a restrictive 'tools:' block — re-grant failed (silent re-restriction?)"
fi
ok "assertion: a11y-reviewer has no tools: restriction (all tools granted)"

echo "postinstall-restore-frontmatter: OK (see docs/hos-overrides.md)"
