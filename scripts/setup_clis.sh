#!/usr/bin/env bash
# setup_clis.sh — Repo-independent machine bootstrap for the AI-oversight agent CLIs.
#
# Drop this single file into any repo (or run it standalone), run it, and it
# "just works": installs Node 22 + the agent CLIs, drives interactive browser
# sign-in for each, and smoke-tests that each one actually responds.
#
# REPO-INDEPENDENT: it reads nothing from the host repo and writes nothing into
# it. It only touches the machine — Node (via nvm), global CLIs, and each tool's
# own auth state under $HOME. Companion to setup_oversight.sh, which bootstraps
# the oversight *protocol* into a repo and assumes these CLIs already exist.
#
# SCOPE — OVERSIGHT TOOLING ONLY. This bootstrap installs ONLY what the oversight
# system itself needs: the agent CLIs, gh, and the Node runtime those CLIs run on.
# It deliberately does NOT install project frameworks, libraries, or run a
# project's dependency install (e.g. `npm install`, Astro, Tailwind, PHP, etc.).
# Project-specific tooling is out of scope and handled by each project's own setup.
# Node here is the runtime for the agent CLIs — not the project's dependencies.
#
# Usage:
#   ./setup_clis.sh            # full bootstrap: install -> auth -> smoke -> doctor
#   ./setup_clis.sh install    # install tools only
#   ./setup_clis.sh auth       # interactive (browser) sign-in only
#   ./setup_clis.sh smoke      # smoke-test each authed CLI (makes a tiny model call)
#   ./setup_clis.sh doctor     # status table only — changes nothing
#   ./setup_clis.sh --help
#
# Tools (subscription / browser auth — NOT API keys):
#   node@22  via nvm                       (runtime for the npm CLIs)
#   claude   @anthropic-ai/claude-code     (Claude Max)
#   codex    @openai/codex                 (ChatGPT Pro)
#   agy      Antigravity CLI (Go binary)   (Gemini Pro / Google) [replaces gemini-cli]
#   gh       GitHub CLI                    (PR ops + Copilot PR review)
#
# Idempotent: re-running skips anything already installed/authenticated.
# NOTE: codex/agy login UX varies by CLI version; this script launches each
# tool's sign-in flow in the foreground and treats the smoke test as the real
# proof of working auth. Adjust the marked login lines if your version differs.
# Antigravity CLI (agy) replaces the deprecated @google/gemini-cli (Gemini CLI
# consumer shutoff: 2026-06-18). Its install uses Google's official curl|bash
# installer (a supply-chain trust decision) and ships a native Go binary (no Node).

set -euo pipefail

# ── Colours / log helpers (match setup_oversight.sh) ──────────────────────────
GREEN="\033[32m"; YELLOW="\033[33m"; CYAN="\033[36m"
RED="\033[31m"; BOLD="\033[1m"; RESET="\033[0m"
ok()   { echo -e "  ${GREEN}✔${RESET}  $*"; }
skip() { echo -e "  ${YELLOW}–${RESET}  $*"; }
info() { echo -e "  ${CYAN}→${RESET}  $*"; }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $*"; }
err()  { echo -e "  ${RED}✘${RESET}  $*"; }

# ── Args ──────────────────────────────────────────────────────────────────────
MODE="all"
while [[ $# -gt 0 ]]; do
  case "$1" in
    install|auth|smoke|doctor|all) MODE="$1"; shift ;;
    --help|-h) sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1  (try --help)"; exit 1 ;;
  esac
done

# ── OS detection ──────────────────────────────────────────────────────────────
detect_os() {
  case "$(uname -s)" in
    Darwin) echo "mac" ;;
    Linux)  if command -v apt-get >/dev/null 2>&1; then echo "ubuntu"; else echo "linux"; fi ;;
    *)      echo "unknown" ;;
  esac
}
OS="$(detect_os)"

# ── Node 22 via nvm (skip if a system node >= 22 already exists) ───────────────
NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
load_nvm() { [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" || true; }
node_major() { command -v node >/dev/null 2>&1 && node -v | sed 's/^v\([0-9]*\).*/\1/' || echo 0; }

install_node() {
  if [[ "$(node_major)" -ge 22 ]]; then
    skip "node $(node -v) already >= 22"
    return
  fi
  load_nvm
  if ! command -v nvm >/dev/null 2>&1; then
    info "installing nvm..."
    curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
    load_nvm
  fi
  info "installing Node 22 via nvm..."
  nvm install 22 >/dev/null
  nvm use 22 >/dev/null
  nvm alias default 22 >/dev/null
  ok "node $(node -v)"
}

# ── npm-based CLIs ────────────────────────────────────────────────────────────
install_npm_tool() {
  local cmd="$1" pkg="$2" label="$3"
  if command -v "$cmd" >/dev/null 2>&1; then
    skip "$label present ($("$cmd" --version 2>/dev/null | head -1 | tr -d '\n'))"
    return
  fi
  info "installing $label ($pkg)..."
  if npm install -g "$pkg" >/dev/null 2>&1; then ok "$label installed"
  else err "failed to install $label — check npm/network"; fi
}

# ── GitHub CLI (not on npm) ───────────────────────────────────────────────────
install_gh() {
  if command -v gh >/dev/null 2>&1; then
    skip "gh present ($(gh --version | head -1))"
    return
  fi
  case "$OS" in
    mac)
      info "installing gh via Homebrew..."
      brew install gh && ok "gh installed" ;;
    ubuntu)
      info "installing gh via apt..."
      curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
      sudo apt update && sudo apt install -y gh && ok "gh installed" ;;
    *)
      err "unsupported OS ($OS) for gh auto-install — install gh manually" ;;
  esac
}

# ── Antigravity CLI (agy) — official Go-binary installer, replaces gemini-cli ──
install_agy() {
  if command -v agy >/dev/null 2>&1; then
    skip "agy (Antigravity) present ($(agy --version 2>/dev/null | head -1 | tr -d '\n'))"
    return
  fi
  info "installing Antigravity CLI (agy) via Google's official installer..."
  # NOTE: official installer is a curl|bash — a supply-chain trust decision.
  # Native Go binary (no Node). Replaces deprecated @google/gemini-cli.
  if curl -fsSL https://antigravity.google/cli/install.sh | bash; then ok "agy installed"
  else err "failed to install agy — see https://antigravity.google/docs/cli-getting-started"; fi
  command -v agy >/dev/null 2>&1 || warn "agy not on PATH yet — open a new shell, then: $0 auth"
}

# ── Smoke tests (a tiny real call that proves auth end-to-end) ─────────────────
smoke_claude() { claude -p "Reply with exactly: OK" 2>/dev/null | grep -qi "ok"; }
smoke_codex()  { codex exec "Reply with exactly: OK" 2>/dev/null | grep -qi "ok"; }
# verified: agy -p/--print/--prompt runs a single prompt non-interactively and prints it.
smoke_agy()    { agy -p "Reply with exactly: OK" 2>/dev/null | grep -qi "ok"; }
smoke_gh()     { gh auth status >/dev/null 2>&1; }

# ── Cheap auth-state checks (no model call — used by doctor/auth) ──────────────
authed_claude() { claude auth status >/dev/null 2>&1; }
authed_codex()  { codex login status >/dev/null 2>&1; }
authed_gh()     { gh auth status >/dev/null 2>&1; }

# ── Interactive sign-in (fires up a browser session) ──────────────────────────
auth_claude() {
  if authed_claude; then ok "claude already authenticated"; return; fi
  warn "claude: launching browser sign-in (Claude Max)..."
  claude auth login || true
  authed_claude && ok "claude authenticated" || err "claude auth incomplete — re-run: $0 auth"
}
auth_gh() {
  if authed_gh; then ok "gh already authenticated"; return; fi
  warn "gh: launching browser sign-in (GitHub)..."
  gh auth login --hostname github.com --git-protocol https --web || true
  authed_gh && ok "gh authenticated" || err "gh auth incomplete — re-run: $0 auth"
}
auth_codex() {
  command -v codex >/dev/null 2>&1 || { err "codex not installed — run: $0 install"; return; }
  if authed_codex; then ok "codex already authenticated"; return; fi
  warn "codex: launching 'Sign in with ChatGPT' (ChatGPT Pro)..."
  codex login || true            # verified: codex 0.135 — 'codex login' / 'codex login status'
  authed_codex && ok "codex authenticated" || err "codex auth incomplete — re-run: $0 auth"
}
auth_agy() {
  command -v agy >/dev/null 2>&1 || { err "agy not installed — run: $0 install"; return; }
  # No print-mode precheck here: when unauthed, `agy -p` starts the device-code flow
  # but the suppressed stderr (2>/dev/null) HIDES the "paste this code" prompt. Launch
  # agy on a full TTY instead so the code prompt is visible. (agy has no auth subcommand
  # and no auth-status command, so we can't cheaply skip-if-authed — hence /quit guidance.)
  warn "agy: launching Antigravity for Google sign-in."
  warn "     A browser opens showing a CODE — paste that code at agy's prompt and hit enter."
  warn "     If you're already signed in, just type /quit to continue."
  agy || true                    # interactive, full TTY → device-code paste prompt is visible
  smoke_agy && ok "agy authenticated" || err "agy auth incomplete — re-run: $0 auth"
}

# ── Phases ────────────────────────────────────────────────────────────────────
phase_install() {
  echo -e "${BOLD}Install — Node 22 + agent CLIs${RESET}"
  install_node
  install_npm_tool claude "@anthropic-ai/claude-code" "claude (Claude Max)"
  install_npm_tool codex  "@openai/codex"             "codex (ChatGPT Pro)"
  install_agy
  install_gh
}
phase_auth() {
  echo ""
  echo -e "${BOLD}Auth — interactive browser sign-in${RESET}"
  auth_claude
  auth_codex
  auth_agy
  auth_gh
}
phase_smoke() {
  echo ""
  echo -e "${BOLD}Smoke test — one tiny model call each${RESET}"
  smoke_claude && ok "claude responds" || err "claude smoke failed"
  smoke_codex  && ok "codex responds"  || err "codex smoke failed"
  smoke_agy    && ok "agy responds"    || err "agy smoke failed"
  smoke_gh     && ok "gh authenticated" || err "gh not authenticated"
}

# ── Doctor (status table, no changes, no model calls) ─────────────────────────
status_line() {
  local cmd="$1" label="$2" auth_state="$3"
  if command -v "$cmd" >/dev/null 2>&1; then
    printf "  %-9s ${GREEN}installed${RESET}  %-22s  %b\n" "$label" "$("$cmd" --version 2>/dev/null | head -1 | tr -d '\n')" "$auth_state"
  else
    printf "  %-9s ${RED}missing${RESET}\n" "$label"
  fi
}
doctor() {
  echo ""
  echo -e "${BOLD}Doctor — environment status (OS: $OS)${RESET}"
  status_line node   "node"   ""
  if command -v claude >/dev/null 2>&1; then
    authed_claude && A="${GREEN}authed${RESET}" || A="${YELLOW}no auth${RESET}"; else A=""; fi
  status_line claude "claude" "$A"
  if command -v codex >/dev/null 2>&1; then
    authed_codex && A="${GREEN}authed${RESET}" || A="${YELLOW}no auth${RESET}"; else A=""; fi
  status_line codex  "codex"  "$A"
  status_line agy    "agy"    "${YELLOW}run smoke to verify${RESET}"
  if command -v gh >/dev/null 2>&1; then
    authed_gh && A="${GREEN}authed${RESET}" || A="${YELLOW}no auth${RESET}"; else A=""; fi
  status_line gh     "gh"     "$A"
}

# ── Main ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}AI Oversight — CLI machine bootstrap${RESET}  (mode: ${MODE}, os: ${OS})"
echo ""
case "$MODE" in
  install) phase_install ;;
  auth)    phase_auth ;;
  smoke)   phase_smoke ;;
  doctor)  doctor ;;
  all)     phase_install; phase_auth; phase_smoke; doctor ;;
esac
echo ""
echo -e "${GREEN}${BOLD}Done (${MODE}).${RESET}"
[[ "$MODE" == "all" || "$MODE" == "install" ]] && \
  echo "  Next: ./setup_clis.sh auth   (then)   ./setup_clis.sh smoke"
echo ""
