#!/usr/bin/env bash
# capture_prompt.sh — scaffold a prompt artifact in the prompts/ directory
#
# Usage:
#   ./scripts/capture_prompt.sh <source-file> "<description>"
#
# Examples:
#   ./scripts/capture_prompt.sh src/auth/middleware.ts "JWT validation with refresh rotation"
#   ./scripts/capture_prompt.sh src/components/LoginForm.tsx "Login form with validation"
#
# What it does:
#   1. Creates prompts/<path>/<basename>.md (mirroring the source file path)
#   2. Scaffolds the prompt artifact template with metadata pre-filled
#   3. Prints the git commit trailer block to copy into your commit message
#
# After running, open the generated .md file and fill in:
#   - The exact prompt text you used
#   - Any refinement iterations
#   - Human review status

set -euo pipefail

# ── Args ──────────────────────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <source-file> [\"<description>\"]"
  echo "  source-file:  path to the generated file (e.g. src/auth/middleware.ts)"
  echo "  description:  one-line description (optional, defaults to filename)"
  exit 1
fi

SOURCE_FILE="$1"
DESCRIPTION="${2:-$(basename "$SOURCE_FILE")}"
DATE=$(date -u +"%Y-%m-%d")
MODEL="${AI_MODEL:-claude-sonnet-4-6}"   # override with: AI_MODEL=claude-opus-4-6 ./scripts/capture_prompt.sh ...

# ── Derive artifact path ───────────────────────────────────────────────────────
# Strip leading ./ if present
SOURCE_FILE="${SOURCE_FILE#./}"

# Build the prompts/ mirror path
ARTIFACT_DIR="prompts/$(dirname "$SOURCE_FILE")"
BASENAME=$(basename "$SOURCE_FILE")
# Remove extension and add .md
BASENAME_NO_EXT="${BASENAME%.*}"
ARTIFACT_PATH="${ARTIFACT_DIR}/${BASENAME_NO_EXT}.md"

# Handle versioning: if artifact already exists, increment version
if [[ -f "$ARTIFACT_PATH" ]]; then
  V=2
  while [[ -f "${ARTIFACT_DIR}/${BASENAME_NO_EXT}.v${V}.md" ]]; do
    V=$((V + 1))
  done
  # Rename existing to .v1.md if it has no version suffix yet
  if [[ ! -f "${ARTIFACT_DIR}/${BASENAME_NO_EXT}.v1.md" ]]; then
    mv "$ARTIFACT_PATH" "${ARTIFACT_DIR}/${BASENAME_NO_EXT}.v1.md"
    echo "  Renamed existing artifact → ${ARTIFACT_DIR}/${BASENAME_NO_EXT}.v1.md"
  fi
  ARTIFACT_PATH="${ARTIFACT_DIR}/${BASENAME_NO_EXT}.v${V}.md"
  echo "  New version: ${ARTIFACT_PATH}"
fi

# ── Create directory ───────────────────────────────────────────────────────────
mkdir -p "$ARTIFACT_DIR"

# ── Detect risk level from any open AGENTS.md risk declaration ────────────────
# Looks for "RISK: HIGH" etc in recent shell output — best-effort only.
# Override with: AI_RISK=HIGH ./scripts/capture_prompt.sh ...
RISK="${AI_RISK:-MEDIUM}"

# ── Write artifact template ───────────────────────────────────────────────────
cat > "$ARTIFACT_PATH" << EOF
# Prompt Artifact — ${BASENAME}

| Field | Value |
|---|---|
| **Generated file** | \`${SOURCE_FILE}\` |
| **Description** | ${DESCRIPTION} |
| **Date** | ${DATE} |
| **Model** | ${MODEL} |
| **Risk level** | ${RISK} |
| **Human review status** | ⬜ Pending |

---

## Prompt

\`\`\`
[PASTE THE EXACT PROMPT TEXT HERE]
\`\`\`

## Constraints Specified

<!-- List the explicit constraints you gave in the prompt:
     - Framework/version:
     - Runtime/browser targets:
     - Security constraints:
     - Data types/shapes:
     - What it must NOT do:
-->

## Refinement History

<!-- If you iterated across multiple turns to get the right output, document the key changes:

v1: [initial prompt — what was wrong or missing]
v2: [what you changed and why]
vFinal: [what made it work]

Delete this section if first attempt worked.
-->

## Human Review Notes

<!-- After human review, record findings here:
     - Reviewed by: [initials or role]
     - Date reviewed:
     - Findings: [what was caught, what was confirmed correct]
     - Status: APPROVED / APPROVED WITH CHANGES / REJECTED
-->

---

## Reproducibility Check

To verify this prompt still produces equivalent output in a new session:
1. Open a fresh Claude Code session
2. Paste the prompt above verbatim
3. Compare key logic paths against \`${SOURCE_FILE}\`
4. Note any drift in a new version artifact (\`${BASENAME_NO_EXT}.v$(($(ls "${ARTIFACT_DIR}/${BASENAME_NO_EXT}".v*.md 2>/dev/null | wc -l) + 1)).md\`)
EOF

echo ""
echo "✔  Prompt artifact created: ${ARTIFACT_PATH}"
echo ""
echo "Next steps:"
echo "  1. Open ${ARTIFACT_PATH} and fill in the prompt text"
echo "  2. Add to git: git add ${ARTIFACT_PATH}"
echo ""
echo "── Git commit trailer block (copy into your commit message) ──────────────"
echo ""
echo "Prompt-Artifact: ${ARTIFACT_PATH}"
echo "AI-Model: ${MODEL}"
echo "AI-Risk: ${RISK}"
echo ""
echo "──────────────────────────────────────────────────────────────────────────"
