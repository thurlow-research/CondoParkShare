#!/usr/bin/env bash
# template_refs_check.sh — Django template-reference existence gate (blocking).
#
# Asserts every template referenced by a view actually exists on disk.
# Static: no running app, no DB, no imports. No-op when manage.py is absent.
#
# Catches "view renders a template that was never created" — HOS issue #8 defect 5.
# Verified against CondoParkShare build-as-shipped: flags all 18 missing templates.
#
# Scans for:
#   render(), render_to_string(), render_to_response()
#   get_template(), select_template()
#   TemplateResponse()
#   CBV template_name = "..."
#
# PYTHONSAFEPATH=1 prevents stdlib-shadowing packages in the project root
# from crashing the inline python (HOS issue #8 hardening).
#
# Exit 0 = all referenced templates exist (or no manage.py). Exit 1 = missing.
#
# Usage: ./template_refs_check.sh file.py [file2.py ...]
#        ./template_refs_check.sh --all

set -euo pipefail

_GATES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/oversight/gates/check_suspension.sh
source "$_GATES_DIR/check_suspension.sh"
is_suspended "template-refs" && { print_suspended "template-refs"; exit 0; }

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

if [[ ! -f "manage.py" ]]; then
    echo "template_refs_check: not a Django project — skipping"
    exit $PASS
fi

if $CHECK_ALL || [[ ${#FILES[@]} -eq 0 ]]; then
    while IFS= read -r line; do FILES+=("$line"); done < <(find . -name "*.py" \
        -not -path "./.venv/*" -not -path "./scripts/oversight/.venv/*" \
        -not -path "./.git/*" -not -path "./node_modules/*")
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "template_refs_check: no Python files to check"
    exit $PASS
fi

echo "=== template reference existence check (${#FILES[@]} files) ==="

PYTHONSAFEPATH=1 python3 - "${FILES[@]}" <<'PY'
import os, re, sys
from pathlib import Path

files = sys.argv[1:]

# Build set of all templates on disk (relative to their templates/ root)
existing = set()
for tdir in Path('.').rglob('templates'):
    if any(p in tdir.parts for p in ('.venv', '.git', 'node_modules')) or not tdir.is_dir():
        continue
    for f in tdir.rglob('*'):
        if f.is_file():
            existing.add(str(f.relative_to(tdir)).replace(os.sep, '/'))

EXTS = r'(?:html|htm|txt|xml|json|svg)'
pats = [
    re.compile(r'''\brender(?:_to_string|_to_response)?\s*\(\s*(?:[^,]+,\s*)?['"]([^'"]+\.''' + EXTS + r''')['"]'''),
    re.compile(r'''\b(?:get_template|select_template)\s*\(\s*\[?\s*['"]([^'"]+\.''' + EXTS + r''')['"]'''),
    re.compile(r'''\bTemplateResponse\s*\(\s*[^,]+,\s*['"]([^'"]+\.''' + EXTS + r''')['"]'''),
    re.compile(r'''\btemplate_name\s*=\s*['"]([^'"]+\.''' + EXTS + r''')['"]'''),
]

missing = {}
for fp in files:
    try:
        text = Path(fp).read_text(encoding='utf-8', errors='ignore')
    except OSError:
        continue
    for i, line in enumerate(text.splitlines(), 1):
        for pat in pats:
            for m in pat.finditer(line):
                tref = m.group(1)
                if tref not in existing:
                    missing.setdefault(tref, []).append(f"{fp}:{i}")

if missing:
    print(f"GATE FAIL: {len(missing)} referenced template(s) not found on disk\n")
    for tref in sorted(missing):
        print(f"  {tref}")
        for ref in missing[tref]:
            print(f"      referenced at {ref}")
    sys.exit(1)

print(f"GATE PASS: all referenced templates exist ({len(existing)} templates found on disk)")
PY
