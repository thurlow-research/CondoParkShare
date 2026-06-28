#!/usr/bin/env bash
# bash_check.sh — shell-portability invariant gate (blocking).
#
# Enforces two invariants for every *.sh file in the framework:
#
#   1. SHEBANG — must be #!/usr/bin/env bash or #!/bin/bash.
#      #!/bin/sh is rejected (POSIX sh, not bash). A leading
#      "# shellcheck shell=bash" with no shebang is allowed for
#      scripts intended to be sourced rather than executed.
#
#   2. BASH-3.2 UNSAFE — rejects known constructs that run on
#      Bash 4+ only and silently misbehave on macOS's default
#      Bash 3.2:
#        declare -A        associative arrays (Bash 4+)
#        mapfile           built-in (Bash 4+)
#        readarray         alias for mapfile (Bash 4+)
#        ${var^^}          case-upper (Bash 4+)
#        ${var,,}          case-lower (Bash 4+)
#      Comments that *mention* these constructs are exempt
#      (e.g. "no mapfile — bash 3.2" is a portability note).
#
# Exit 0 = all checks pass. Exit 1 = one or more findings.
#
# Usage: ./bash_check.sh file.sh [file2.sh ...]
#        ./bash_check.sh --all        (check all *.sh in the repo)

set -euo pipefail

_GATES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/oversight/gates/check_suspension.sh
source "$_GATES_DIR/check_suspension.sh"
is_suspended "bash_check" && { print_suspended "bash_check"; exit 0; }

PASS=0
FAIL=1

FILES=()
CHECK_ALL=false

for arg in "$@"; do
    if [[ "$arg" == "--all" ]]; then
        CHECK_ALL=true
    else
        FILES+=("$arg")
    fi
done

if $CHECK_ALL || [[ ${#FILES[@]} -eq 0 ]]; then
    FILES=()
    while IFS= read -r _f; do
        [[ -n "$_f" ]] && FILES+=("$_f")
    done < <(find . -type f -name '*.sh' \
        -not -path "./.git/*" \
        -not -path "./.venv/*" \
        -not -path "./scripts/oversight/.venv/*" \
        -not -path "./node_modules/*")
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "bash_check: no .sh files to check"
    exit $PASS
fi

echo "=== bash portability check (shebang + Bash-3.2 safety) ==="

ERRORS=0
SHEBANG_FAILS=()
UNSAFE_HITS=()

for f in "${FILES[@]}"; do
    [[ "${f##*.}" != "sh" ]] && continue
    [[ -f "$f" ]] || continue

    # ── 1. Shebang check ──────────────────────────────────────────────────────
    first_line="$(head -1 "$f" 2>/dev/null || true)"
    case "$first_line" in
        "#!/usr/bin/env bash"|"#!/bin/bash")
            # correct shebang — ok
            ;;
        "# shellcheck shell=bash")
            # sourced-only script with shellcheck directive — ok
            ;;
        "#!/"*)
            # some other shebang (#!/bin/sh, #!/usr/bin/env zsh, etc.)
            SHEBANG_FAILS+=("$f: non-bash shebang: $first_line")
            ERRORS=$((ERRORS + 1))
            ;;
        "")
            # empty first line or empty file — no shebang
            SHEBANG_FAILS+=("$f: no shebang (use #!/usr/bin/env bash or # shellcheck shell=bash for sourced scripts)")
            ERRORS=$((ERRORS + 1))
            ;;
        *)
            # comment or other non-shebang first line (no #!/) — warn
            SHEBANG_FAILS+=("$f: no shebang on line 1 (use #!/usr/bin/env bash or # shellcheck shell=bash for sourced scripts)")
            ERRORS=$((ERRORS + 1))
            ;;
    esac

    # ── 2. Bash-3.2 unsafe-construct check ───────────────────────────────────
    # Use Python for reliable regex + line-number reporting (portable; grep -nP
    # is not available on BSD without GNU grep).
    # The scanner strips heredoc content so patterns in bash heredocs (e.g.
    # Python strings inside << 'PYEOF' blocks) are not flagged as bash constructs.
    hits=$(python3 - "$f" << 'PYSCAN'
import re, sys
path = sys.argv[1]

# Bash-4+-only constructs — name and description.
UNSAFE_DECL_A  = re.compile(r'^\s*declare\s+-[a-zA-Z]*A[a-zA-Z]*\b')
UNSAFE_MAPFILE = re.compile(r'(?<![#\w])(?:mapfile|readarray)\b')
UNSAFE_UPPER   = re.compile(r'\$\{[a-zA-Z_][a-zA-Z0-9_]*\^\^')
UNSAFE_LOWER   = re.compile(r'\$\{[a-zA-Z_][a-zA-Z0-9_]*,,')

# Heredoc-open: <<[-] ['"]?MARKER or << MARKER (quoted marker = no substitution).
HEREDOC_START  = re.compile(r'''<<-?\s*(['"]?)([A-Za-z_][A-Za-z0-9_]*)''')

def check_line(line):
    stripped = line.lstrip()
    if stripped.startswith('#'):
        return None  # comment — skip
    if UNSAFE_DECL_A.search(line):
        return 'declare -A (associative arrays require Bash 4+; use sorted lists or file-based lookup)'
    m = UNSAFE_MAPFILE.search(line)
    if m:
        kw = m.group()
        return f'{kw} (Bash 4+; use portable read loop)'
    if UNSAFE_UPPER.search(line):
        return '${var^^} case-upper (Bash 4+; use tr or awk)'
    if UNSAFE_LOWER.search(line):
        return '${var,,} case-lower (Bash 4+; use tr or awk)'
    return None

found = []
try:
    heredoc_end = None   # set to the closing marker when inside a heredoc
    in_dquote = False    # True when inside a multi-line double-quoted string
    for i, raw in enumerate(open(path, errors='replace'), 1):
        line = raw.rstrip()
        # Heredoc takes priority: skip content between << MARKER and MARKER lines.
        if heredoc_end is not None:
            if line.strip() == heredoc_end:
                heredoc_end = None
            continue
        # Multi-line double-quote tracking: if a line has an odd number of
        # unescaped '"' chars, we are toggling in/out of a string context.
        # Skip bash-4 checks when we are inside an open multi-line string.
        # (Simple heuristic; handles the common LENS="...\n...\n..." pattern.)
        #
        # Comments are excluded from dquote tracking: a comment like
        # '# 5" diameter pipe' must not toggle in_dquote and cause the
        # very next real code line to be silently skipped.
        stripped = line.lstrip()
        if not stripped.startswith('#'):
            dquote_count = line.count('"') - line.count('\\"')
            if in_dquote:
                if dquote_count % 2 == 1:
                    in_dquote = False  # closing quote on this line
                continue  # inside multi-line string — skip check
            if dquote_count % 2 == 1:
                in_dquote = True   # opening of multi-line string; still check THIS line
        elif in_dquote:
            continue  # comment inside an open multi-line string — still skip
        # After toggling, detect heredoc opens on this same line.
        if not in_dquote:
            m = HEREDOC_START.search(line)
            if m:
                heredoc_end = m.group(2)
        msg = check_line(line)
        if msg:
            found.append(f'  {path}:{i}: {msg}')
            found.append(f'    {line}')
except Exception:
    pass
print('\n'.join(found))
PYSCAN
)
    if [[ -n "$hits" ]]; then
        UNSAFE_HITS+=("$hits")
        ERRORS=$((ERRORS + 1))
    fi
done

if [[ ${#SHEBANG_FAILS[@]} -gt 0 ]]; then
    echo ""
    echo "SHEBANG FINDINGS:"
    for msg in "${SHEBANG_FAILS[@]}"; do
        echo "  $msg"
    done
fi

if [[ ${#UNSAFE_HITS[@]} -gt 0 ]]; then
    echo ""
    echo "BASH-3.2 UNSAFE CONSTRUCTS:"
    for hit in "${UNSAFE_HITS[@]}"; do
        echo "$hit"
    done
    echo ""
    echo "See docs/SHELL-PORTABILITY.md for portable replacements."
fi

echo ""
if [[ $ERRORS -gt 0 ]]; then
    echo "GATE FAIL: $ERRORS file(s) with portability issue(s)"
    exit $FAIL
else
    echo "GATE PASS: all shell scripts use bash shebang and Bash-3.2-safe constructs"
    exit $PASS
fi
