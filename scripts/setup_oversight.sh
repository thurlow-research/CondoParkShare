#!/usr/bin/env bash
# setup_oversight.sh — Bootstrap AI oversight protocol into a target repo
#
# Usage:
#   ./scripts/setup_oversight.sh                    # target = current directory
#   ./scripts/setup_oversight.sh /path/to/repo      # target = specified path
#   ./scripts/setup_oversight.sh --dry-run          # preview only, no writes
#   ./scripts/setup_oversight.sh --force            # overwrite existing files
#   ./scripts/setup_oversight.sh --update-agents    # re-copy AGENTS.md only
#   ./scripts/setup_oversight.sh --skip-commit      # stage but don't commit
#   ./scripts/setup_oversight.sh --skip-github      # skip GitHub API steps
#
# What it does:
#   1. Copies AGENTS.md to repo root (stable governance protocol)
#   2. Copies capture_prompt.sh and prompt_audit.sh to scripts/
#   3. Creates prompts/ directory with README.md
#   4. Installs CODEOWNERS requiring owner approval on all files
#   5. Installs PR template with oversight checklist
#   6. Adds oversight entries to .gitignore
#   7. Applies GitHub branch protection on main via API (requires GH_TOKEN)
#   8. Creates git commit
#
# GitHub branch protection requires:
#   export GH_TOKEN=<your-github-token>
#   Token needs: repo scope (or fine-grained: pull_requests + administration)
#
# Idempotent: safe to re-run. Existing files are skipped unless --force.

set -euo pipefail

# ── Resolve paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOOTSTRAP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATES_DIR="$BOOTSTRAP_ROOT/templates"

# ── Defaults ──────────────────────────────────────────────────────────────────
TARGET_REPO="$(pwd)"
DRY_RUN=false
FORCE=false
UPDATE_AGENTS_ONLY=false
SKIP_COMMIT=false
SKIP_GITHUB=false

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)       DRY_RUN=true; shift ;;
    --force)         FORCE=true; shift ;;
    --update-agents) UPDATE_AGENTS_ONLY=true; FORCE=true; shift ;;
    --skip-commit)   SKIP_COMMIT=true; shift ;;
    --skip-github)   SKIP_GITHUB=true; shift ;;
    --help|-h)
      sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    -*)
      echo "Unknown option: $1  (try --help)"; exit 1 ;;
    *)
      TARGET_REPO="$1"; shift ;;
  esac
done

TARGET_REPO="$(cd "$TARGET_REPO" && pwd)"

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN="\033[32m"; YELLOW="\033[33m"; CYAN="\033[36m"
RED="\033[31m"; BOLD="\033[1m"; RESET="\033[0m"
ok()   { echo -e "  ${GREEN}✔${RESET}  $*"; }
skip() { echo -e "  ${YELLOW}–${RESET}  $*"; }
info() { echo -e "  ${CYAN}→${RESET}  $*"; }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $*"; }
err()  { echo -e "  ${RED}✘${RESET}  $*"; }
dry()  { echo -e "  ${YELLOW}[dry]${RESET} $*"; }

run() {
  if $DRY_RUN; then dry "$*"
  else eval "$@"
  fi
}

# ── Validation ────────────────────────────────────────────────────────────────
if [[ ! -d "$TARGET_REPO" ]]; then
  echo "ERROR: Target directory not found: $TARGET_REPO"; exit 1
fi
if [[ ! -d "$TARGET_REPO/.git" ]]; then
  echo "ERROR: $TARGET_REPO is not a git repository"; exit 1
fi
if [[ ! -d "$TEMPLATES_DIR" ]]; then
  echo "ERROR: templates/ not found at $TEMPLATES_DIR"; exit 1
fi

# ── Helper: copy file ─────────────────────────────────────────────────────────
install_file() {
  local src="$1" dst="$2" label="$3" executable="${4:-false}"
  if [[ ! -f "$src" ]]; then err "Template not found: $src"; return 1; fi
  if [[ -f "$dst" ]] && ! $FORCE; then
    skip "$label already exists — skipping (--force to overwrite)"; return 0
  fi
  run "mkdir -p '$(dirname "$dst")'"
  run "cp '$src' '$dst'"
  $executable && run "chmod +x '$dst'"
  $FORCE && ! $DRY_RUN && ok "$label (updated)" || ok "$label"
}

# ── Helper: create dir ────────────────────────────────────────────────────────
install_dir() {
  local dir="$1" label="$2"
  if [[ -d "$dir" ]]; then skip "$label already exists"
  else run "mkdir -p '$dir'"; ok "$label created"
  fi
}

# ── Helper: write file from heredoc if not exists ────────────────────────────
install_content() {
  local dst="$1" label="$2" content="$3"
  if [[ -f "$dst" ]] && ! $FORCE; then
    skip "$label already exists — skipping"; return 0
  fi
  if $DRY_RUN; then dry "Would write: $dst"; return 0; fi
  mkdir -p "$(dirname "$dst")"
  printf '%s\n' "$content" > "$dst"
  ok "$label"
}

# ── Detect GitHub remote ──────────────────────────────────────────────────────
detect_github_remote() {
  cd "$TARGET_REPO"
  local remote_url
  remote_url=$(git remote get-url origin 2>/dev/null || echo "")
  if [[ -z "$remote_url" ]]; then echo ""; return; fi

  # Handle both SSH and HTTPS formats
  # git@github.com:owner/repo.git
  # https://github.com/owner/repo.git
  local slug=""
  if [[ "$remote_url" =~ github\.com[:/]([^/]+/[^/]+?)(\.git)?$ ]]; then
    slug="${BASH_REMATCH[1]}"
  fi
  echo "$slug"
}

# ── GitHub API call ───────────────────────────────────────────────────────────
gh_api() {
  local method="$1" path="$2" data="${3:-}"
  local token="${GH_TOKEN:-}"

  if [[ -z "$token" ]]; then
    # Try gh CLI token as fallback
    token=$(gh auth token 2>/dev/null || echo "")
  fi

  if [[ -z "$token" ]]; then
    warn "No GitHub token found. Set GH_TOKEN or run: gh auth login"
    return 1
  fi

  local curl_args=(
    -s -X "$method"
    -H "Authorization: Bearer $token"
    -H "Accept: application/vnd.github+json"
    -H "X-GitHub-Api-Version: 2022-11-28"
    "https://api.github.com${path}"
  )
  if [[ -n "$data" ]]; then
    curl_args+=(-H "Content-Type: application/json" -d "$data")
  fi

  curl "${curl_args[@]}"
}

# ── Header ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}AI Oversight Protocol Bootstrap${RESET}"
echo "  Bootstrap repo : $BOOTSTRAP_ROOT"
echo "  Target repo    : $TARGET_REPO"
$DRY_RUN && echo -e "  ${YELLOW}[DRY RUN — no changes will be made]${RESET}"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — AGENTS.md
# ─────────────────────────────────────────────────────────────────────────────
echo -e "${BOLD}Step 1 — AGENTS.md (governance protocol)${RESET}"
install_file "$TEMPLATES_DIR/AGENTS.md" "$TARGET_REPO/AGENTS.md" "AGENTS.md"
if [[ -f "$TARGET_REPO/CLAUDE.md" ]]; then
  info "CLAUDE.md detected — keeping separate (AGENTS.md = stable, CLAUDE.md = evolving)"
fi

if $UPDATE_AGENTS_ONLY; then
  echo ""; echo "Update complete (--update-agents mode)."; exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — capture / audit scripts
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Step 2 — scripts/capture_prompt.sh and prompt_audit.sh${RESET}"
install_file "$TEMPLATES_DIR/capture_prompt.sh" \
  "$TARGET_REPO/scripts/capture_prompt.sh" "scripts/capture_prompt.sh" true
install_file "$TEMPLATES_DIR/prompt_audit.sh" \
  "$TARGET_REPO/scripts/prompt_audit.sh"   "scripts/prompt_audit.sh"   true

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — prompts/ directory
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Step 3 — prompts/ directory${RESET}"
install_dir "$TARGET_REPO/prompts" "prompts/"
install_content "$TARGET_REPO/prompts/README.md" "prompts/README.md" \
"# Prompt Artifacts

One \`.md\` file per AI-generated code artifact at MEDIUM risk or above,
mirroring the \`src/\` directory structure.

\`\`\`
src/auth/middleware.ts       ← generated file
prompts/auth/middleware.md   ← prompt artifact
\`\`\`

Generate with: \`./scripts/capture_prompt.sh <source-file> \"<description>\"\`

See \`AGENTS.md\` for the full governance protocol."

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — CODEOWNERS
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Step 4 — CODEOWNERS${RESET}"

# Detect GitHub owner from remote or fall back to placeholder
GITHUB_SLUG=$(detect_github_remote)
if [[ -n "$GITHUB_SLUG" ]]; then
  OWNER_HANDLE="@${GITHUB_SLUG%%/*}"
  info "Detected GitHub owner: $OWNER_HANDLE"
else
  OWNER_HANDLE="@OWNER"
  warn "Could not detect GitHub remote — using placeholder @OWNER in CODEOWNERS"
  warn "  Edit .github/CODEOWNERS after setup to set the correct handle"
fi

install_content "$TARGET_REPO/.github/CODEOWNERS" ".github/CODEOWNERS" \
"# CODEOWNERS — Every file requires owner approval before merge
# Auto-generated by ai-oversight-bootstrap
#
# All files: owner must approve every PR
* ${OWNER_HANDLE}

# Governance files: double-locked — owner approval always required
AGENTS.md            ${OWNER_HANDLE}
.github/CODEOWNERS   ${OWNER_HANDLE}
scripts/             ${OWNER_HANDLE}
prompts/             ${OWNER_HANDLE}"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — PR template
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Step 5 — Pull request template${RESET}"
install_content "$TARGET_REPO/.github/pull_request_template.md" \
  ".github/pull_request_template.md" \
"## Summary

<!-- What does this PR do? One paragraph. -->

## AI Assistance

- [ ] No AI-generated code in this PR
- [ ] AI-generated code present — risk level: **LOW / MEDIUM / HIGH / CRITICAL** *(delete as applicable)*

## Prompt Artifacts

<!-- For MEDIUM+ AI-generated code: list prompt artifact files or write 'N/A' -->

| File | Prompt artifact | Risk |
|---|---|---|
| \`src/...\` | \`prompts/...\` | MEDIUM |

## Human Review Checklist

<!-- Work through any Human Review Required flags from the Claude Code session -->

- [ ] All CRITICAL and HIGH risk items reviewed line-by-line
- [ ] Hallucination surface warnings verified (⚠️ VERIFY comments in code)
- [ ] Blast radius assessed for any destructive operations
- [ ] Open review items from prior sessions addressed (check \`./scripts/prompt_audit.sh --pending\`)

## Confidence

<!-- Paste the CONFIDENCE declaration from the Claude Code session, or write your own -->

> CONFIDENCE: __%
> Basis: ___

## Testing

- [ ] Existing tests pass
- [ ] New tests added for new logic
- [ ] Manually tested: ___"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — .claude/settings.json (risk-tiered permission policy)
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Step 6 — .claude/settings.json (permission policy)${RESET}"
install_file \
  "$TEMPLATES_DIR/claude-settings.json" \
  "$TARGET_REPO/.claude/settings.json" \
  ".claude/settings.json"
info "settings.json: auto-allows reads, writes to src/styles/public, git commits,"
info "  gh pr create. Prompts for: git push, file deletes, config files."
info "  Blocks: git push --force, gh pr merge, rm -rf, .env writes, sudo."
info ""
info "For machine-local overrides (e.g. dangerouslySkipPermissions),"
info "  create .claude/settings.local.json — it is gitignored."

# ─────────────────────────────────────────────────────────────────────────────
# STEP 7 — .gitignore
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Step 7 — .gitignore entries${RESET}"
GITIGNORE="$TARGET_REPO/.gitignore"
OVERSIGHT_MARKER="# AI oversight protocol"
if [[ -f "$GITIGNORE" ]] && grep -q "$OVERSIGHT_MARKER" "$GITIGNORE" 2>/dev/null; then
  skip ".gitignore already has oversight entries"
else
  if $DRY_RUN; then
    dry "Would append oversight block to .gitignore"
  else
    printf '\n%s\n%s\n%s\n%s\n' \
      "$OVERSIGHT_MARKER" \
      "# prompts/ and AGENTS.md are always committed — never ignore them" \
      ".ai-local/" \
      ".claude/settings.local.json" >> "$GITIGNORE"
    ok ".gitignore updated"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 8 — GitHub branch protection
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Step 8 — GitHub branch protection (main)${RESET}"

if $SKIP_GITHUB; then
  skip "Skipped (--skip-github)"
elif $DRY_RUN; then
  dry "Would apply branch protection on main via GitHub API"
  dry "  Required reviewers: 1"
  dry "  Require review from CODEOWNERS: true"
  dry "  Dismiss stale reviews on new commits: true"
  dry "  Block direct pushes to main: true"
  dry "  Require branches to be up to date: true"
else
  GITHUB_SLUG=$(detect_github_remote)
  if [[ -z "$GITHUB_SLUG" ]]; then
    warn "No GitHub remote detected — skipping branch protection"
    warn "  Push to GitHub first, then re-run: setup_oversight.sh --skip-commit"
  else
    OWNER="${GITHUB_SLUG%%/*}"
    REPO="${GITHUB_SLUG##*/}"
    info "Applying branch protection on: ${GITHUB_SLUG} / main"

    BP_PAYLOAD=$(cat << EOF
{
  "required_status_checks": null,
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 1,
    "require_last_push_approval": true
  },
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true
}
EOF
)

    RESPONSE=$(gh_api PUT "/repos/${OWNER}/${REPO}/branches/main/protection" "$BP_PAYLOAD" 2>&1 || true)

    if echo "$RESPONSE" | grep -q '"url"'; then
      ok "Branch protection applied on main"
      info "  Required reviews: 1 (CODEOWNERS)"
      info "  Stale review dismissal: enabled"
      info "  Last-push approval required: enabled"
      info "  Force pushes: blocked"
      info "  Conversation resolution: required"
    elif echo "$RESPONSE" | grep -q '"message"'; then
      MSG=$(echo "$RESPONSE" | grep -o '"message":"[^"]*"' | head -1 | cut -d'"' -f4)
      err "Branch protection failed: $MSG"
      warn "  Common causes:"
      warn "    - GH_TOKEN lacks 'repo' scope or 'administration' fine-grained permission"
      warn "    - main branch doesn't exist yet (push a commit first)"
      warn "    - Token belongs to a user without admin rights on this repo"
      warn "  Re-run after fixing: setup_oversight.sh --skip-commit"
    else
      warn "Unexpected API response — check GH_TOKEN and try again"
    fi
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 9 — Git commit
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Step 9 — Git commit${RESET}"

if $DRY_RUN; then
  dry "Would stage and commit all oversight files"
  echo ""
  echo -e "${YELLOW}Dry run complete. Re-run without --dry-run to apply.${RESET}"
  exit 0
fi

cd "$TARGET_REPO"
CHANGED=$(git status --porcelain \
  AGENTS.md \
  .claude/settings.json \
  scripts/capture_prompt.sh scripts/prompt_audit.sh \
  prompts/ \
  .github/CODEOWNERS .github/pull_request_template.md \
  .gitignore 2>/dev/null || true)

if [[ -z "$CHANGED" ]]; then
  skip "Nothing new to commit — repo already up to date"
else
  git add \
    AGENTS.md \
    .claude/settings.json \
    scripts/capture_prompt.sh scripts/prompt_audit.sh \
    prompts/ \
    .github/CODEOWNERS .github/pull_request_template.md \
    .gitignore 2>/dev/null || true

  if $SKIP_COMMIT; then
    ok "Files staged (--skip-commit)"
    info "Run: git commit -m 'Add AI oversight protocol'"
  else
    git commit -m "Add AI oversight protocol (AGENTS.md + GitHub policies)

- AGENTS.md: risk-tiered review, human review flags, confidence
  declarations, blast radius assessment, prompt artifact discipline
- .claude/settings.json: risk-tiered permission policy (auto-allow
  reads/writes/commits, prompt for push, block force-push/rm-rf/.env)
- CODEOWNERS: owner approval required on all files
- PR template: oversight checklist tied to AGENTS.md protocol
- scripts/capture_prompt.sh: prompt artifact scaffolding
- scripts/prompt_audit.sh: git-based audit trail queries
- Branch protection applied on main (1 CODEOWNER review required)

Prompt-Artifact: none (governance scaffolding)
AI-Model: bootstrap
AI-Risk: LOW" && ok "Git commit created" || {
      info "Nothing to commit or commit failed"
      info "Run: git commit -m 'Add AI oversight protocol'"
    }
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}Bootstrap complete.${RESET}"
echo ""
echo "  Files installed:"
echo "    AGENTS.md                            ← governance protocol"
echo "    .claude/settings.json                ← risk-tiered permission policy"
echo "    .github/CODEOWNERS                   ← owner approval on all files"
echo "    .github/pull_request_template.md     ← PR oversight checklist"
echo "    scripts/capture_prompt.sh            ← prompt artifact capture"
echo "    scripts/prompt_audit.sh              ← audit trail queries"
echo "    prompts/README.md                    ← artifact directory"
echo ""
echo "  GitHub policies:"
echo "    main branch: 1 CODEOWNER review required, force pushes blocked"
echo ""
echo "  Permission policy (auto-approved):"
echo "    reads, src/styles/public writes, git add/commit, gh pr create"
echo "  Permission policy (prompts):"
echo "    git push, config file writes, file moves/copies"
echo "  Permission policy (blocked):"
echo "    git push --force, gh pr merge, rm -rf, .env writes, sudo"
echo ""
echo "  For machine-local overrides (e.g. dangerouslySkipPermissions):"
echo "    create .claude/settings.local.json — it is gitignored"
echo ""
echo "  Next steps:"
echo "    1. Start a Claude Code session — AGENTS.md activates automatically"
echo "    2. After MEDIUM+ generations: ./scripts/capture_prompt.sh <file> \"<desc>\""
echo "    3. Audit trail: ./scripts/prompt_audit.sh --stats"
