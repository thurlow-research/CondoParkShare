#!/usr/bin/env bash
# provision_agent_account.sh — Configure a checkout to operate as an HOS machine account.
#
# Applies to the current directory's git repo. Takes a bot CLASS (worker or overseer)
# and a PAT, then configures git identity and gh auth so all git/GitHub operations in
# this checkout are attributed to the bot, not the human operator.
#
# ACCOUNT CREATION IS MANUAL (see docs/MACHINE-ACCOUNTS-SETUP.md §3).
# This script only configures credentials that already exist.
#
# Consumer-facing: works for any project that installs HOS, parameterised by
# class and credentials (AGENT-IDENTITY.md §10a).
#
# Usage:
#   ./provision_agent_account.sh worker  --pat <PAT>            # configure as worker bot
#   ./provision_agent_account.sh overseer --pat <PAT>            # configure as overseer bot
#   ./provision_agent_account.sh doctor                          # show current state only
#   ./provision_agent_account.sh --help
#
# Subcommands:
#   worker   Configure this checkout to run as the worker machine account.
#   overseer Configure this checkout to run as the overseer machine account.
#   doctor   Show current git identity + gh auth state; no changes made.
#
# Options:
#   --pat <token>   GitHub PAT for the bot account (required for worker/overseer).
#                   Treat as a secret: pass via env var or a secrets manager, not a
#                   shell history entry. Example: --pat "$BOT_PAT"
#   --repo <o/r>    Owner/repo to verify collaborator access against (optional;
#                   defaults to the current checkout's origin remote).
#   --global        Write git config at --global scope instead of --local (default).
#   --help          Show this message.
#
# Idempotent: re-running with the same inputs applies the same configuration.
# The doctor subcommand changes nothing and is always safe to run.
#
# After provisioning, verify with:   ./provision_agent_account.sh doctor
# Apply branch protection rules with: ./scripts/framework/setup_branch_protection.sh

set -euo pipefail

# ── Colours / log helpers (setup_clis.sh convention) ──────────────────────────
GREEN="\033[32m"; YELLOW="\033[33m"; CYAN="\033[36m"
RED="\033[31m"; BOLD="\033[1m"; RESET="\033[0m"
ok()   { echo -e "  ${GREEN}✔${RESET}  $*"; }
skip() { echo -e "  ${YELLOW}–${RESET}  $*"; }
info() { echo -e "  ${CYAN}→${RESET}  $*"; }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $*"; }
err()  { echo -e "  ${RED}✘${RESET}  $*" >&2; }
die()  { err "$*"; exit 1; }
header() { echo -e "\n${BOLD}$*${RESET}"; }

# ── Locate machine-accounts.env ───────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/machine-accounts.env"
[ -f "$ENV_FILE" ] || die "machine-accounts.env not found at $ENV_FILE — is this an HOS repo?"
# shellcheck source=./machine-accounts.env
source "$ENV_FILE"

# ── Args ──────────────────────────────────────────────────────────────────────
CLASS=""
PAT=""
REPO_SLUG=""
GIT_SCOPE="--local"

while [[ $# -gt 0 ]]; do
  case "$1" in
    worker|overseer|doctor) CLASS="$1"; shift ;;
    --pat)     shift; PAT="${1:-}"; shift ;;
    --repo)    shift; REPO_SLUG="${1:-}"; shift ;;
    --global)  GIT_SCOPE="--global"; shift ;;
    --help|-h) sed -n '2,35p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown option: $1  (try --help)" ;;
  esac
done

[ -n "$CLASS" ] || die "CLASS required: worker | overseer | doctor  (try --help)"

# ── Resolve bot identity from machine-accounts.env ────────────────────────────
resolve_bot_identity() {
  local class="$1"
  case "$class" in
    worker)
      BOT_USERNAME="$BOT_WORKER_USERNAME"
      BOT_EMAIL="${BOT_WORKER_EMAIL:-${BOT_WORKER_USERNAME}@users.noreply.github.com}"
      ;;
    overseer)
      BOT_USERNAME="$BOT_OVERSEER_USERNAME"
      BOT_EMAIL="${BOT_OVERSEER_EMAIL:-${BOT_OVERSEER_USERNAME}@users.noreply.github.com}"
      ;;
    *) die "Unknown class: $class" ;;
  esac
}

# ── Detect current repo remote ────────────────────────────────────────────────
detect_repo_slug() {
  if [ -n "$REPO_SLUG" ]; then return; fi
  local remote_url
  remote_url="$(git remote get-url origin 2>/dev/null)" || die "No git remote 'origin' found — run from inside the repo."
  # Normalise HTTPS and SSH remotes → owner/repo
  REPO_SLUG="$(printf '%s' "$remote_url" \
    | sed -E 's|https://github\.com/||; s|git@github\.com:||; s|\.git$||')"
}

# ── doctor — report current state ─────────────────────────────────────────────
cmd_doctor() {
  header "HOS Agent Account — Doctor"
  echo ""

  # git identity
  local git_name git_email
  git_name="$(git config --local user.name 2>/dev/null || git config --global user.name 2>/dev/null || echo "(unset)")"
  git_email="$(git config --local user.email 2>/dev/null || git config --global user.email 2>/dev/null || echo "(unset)")"
  echo -e "  ${CYAN}git identity${RESET}"
  echo "    user.name  = $git_name"
  echo "    user.email = $git_email"
  echo ""

  # gh auth
  echo -e "  ${CYAN}gh auth${RESET}"
  if command -v gh &>/dev/null; then
    gh auth status 2>&1 | sed 's/^/    /' || true
  else
    warn "gh not found on PATH"
  fi
  echo ""

  # Known bot handles
  echo -e "  ${CYAN}machine-accounts.env${RESET}"
  echo "    BOT_WORKER_USERNAME   = ${BOT_WORKER_USERNAME:-"(unset)"}"
  echo "    BOT_OVERSEER_USERNAME = ${BOT_OVERSEER_USERNAME:-"(unset)"}"
  echo "    OVERSEER_CEILING      = ${OVERSEER_CEILING:-"(unset)"}"
  echo ""

  # Is current identity a known bot?
  if [ "$git_name" = "${BOT_WORKER_USERNAME:-}" ] || [ "$git_name" = "${BOT_OVERSEER_USERNAME:-}" ]; then
    ok "This checkout is configured as a bot account."
  else
    warn "This checkout is NOT configured as a bot account (git identity is '$git_name')."
  fi
}

# ── provision worker / overseer ───────────────────────────────────────────────
cmd_provision() {
  local class="$1"
  [ -n "$PAT" ] || die "--pat <token> required for '$class' provisioning"

  resolve_bot_identity "$class"
  detect_repo_slug

  header "Provisioning HOS $class account"
  echo ""
  info "Bot username : $BOT_USERNAME"
  info "Bot email    : $BOT_EMAIL"
  info "Repo         : $REPO_SLUG"
  info "git scope    : $GIT_SCOPE"
  echo ""

  # 1. Set git identity
  header "Step 1 — git identity"
  git config "$GIT_SCOPE" user.name  "$BOT_USERNAME"
  git config "$GIT_SCOPE" user.email "$BOT_EMAIL"
  ok "git config user.name  → $BOT_USERNAME"
  ok "git config user.email → $BOT_EMAIL"

  # 2. Authenticate gh with the bot PAT
  header "Step 2 — gh auth"
  if command -v gh &>/dev/null; then
    printf '%s' "$PAT" | gh auth login --with-token 2>&1 | sed 's/^/  /' || \
      die "gh auth login failed — verify the PAT has 'repo' + 'read:org' scopes"
    ok "gh authenticated as $BOT_USERNAME"
  else
    die "gh not found on PATH — install with: brew install gh"
  fi

  # 3. Verify gh auth resolves to the correct account
  header "Step 3 — verify account identity"
  local authed_user
  authed_user="$(gh api user --jq .login 2>/dev/null)" || die "Could not query gh API — check PAT validity"
  if [ "$authed_user" = "$BOT_USERNAME" ]; then
    ok "gh resolves to expected bot: $authed_user"
  else
    warn "gh resolves to '$authed_user' but expected '$BOT_USERNAME'"
    warn "If intentional (multiple accounts), verify the active account is the bot."
  fi

  # 4. Verify collaborator access on the target repo
  header "Step 4 — collaborator access"
  local collab_status
  collab_status="$(gh api "repos/${REPO_SLUG}/collaborators/${BOT_USERNAME}/permission" \
    --jq .permission 2>/dev/null)" || {
    warn "Could not verify collaborator access for $BOT_USERNAME on $REPO_SLUG"
    warn "Ensure the bot is added as a collaborator (see docs/MACHINE-ACCOUNTS-SETUP.md §4)."
    collab_status=""
  }
  if [ -n "$collab_status" ]; then
    ok "Collaborator permission: $collab_status"
    # Worker should be write (not admin/maintain); overseer write or maintain.
    case "$class-$collab_status" in
      worker-admin|worker-maintain)
        warn "Worker has elevated permission '$collab_status' — recommend downgrading to 'write' (cannot approve PRs)"
        ;;
      overseer-write|overseer-maintain|overseer-admin)
        ok "Overseer permission '$collab_status' is sufficient for PR approval"
        ;;
    esac
  fi

  # 5. Remind about what is NOT configured here (AI-CLI auth stays human)
  echo ""
  header "Summary"
  ok "git identity configured ($GIT_SCOPE): $BOT_USERNAME <$BOT_EMAIL>"
  ok "gh authenticated as: $BOT_USERNAME"
  echo ""
  info "AI-CLI auth (claude/codex/agy) remains under your personal subscription."
  info "Only git push / gh PR ops will be attributed to the bot."
  info ""
  info "Next steps:"
  info "  ./scripts/framework/setup_branch_protection.sh $REPO_SLUG"
  info "  ./provision_agent_account.sh doctor"
  info ""
  info "To revert to your personal account:"
  info "  git config ${GIT_SCOPE} user.name  '<your name>'"
  info "  git config ${GIT_SCOPE} user.email '<your email>'"
  info "  gh auth login   # re-auth as yourself"
}

# ── Main ──────────────────────────────────────────────────────────────────────
case "$CLASS" in
  doctor)            cmd_doctor ;;
  worker|overseer)   cmd_provision "$CLASS" ;;
esac
