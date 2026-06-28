#!/usr/bin/env bash
# bootstrap/get_app_token.sh — generate a GitHub App installation token for HOS bot identities
#
# Usage (source into current shell so GH_TOKEN + HOS_BOT_LOGIN are exported):
#   source <(./bootstrap/get_app_token.sh --app worker)
#   source <(./bootstrap/get_app_token.sh --app overseer)
#
# After sourcing, `gh api` calls in the same shell use the App installation token.
# HOS_BOT_LOGIN is set to the App's bot identity (e.g. "hos-worker-hos[bot]") so
# identity guards don't need a `gh api user` call (which fails for App tokens).
#
# Token lifetime: 1 hour. Re-source before long sessions.
#
# Reads: ~/.config/hos/apps.env  (App IDs, PEM paths — never committed to git)
# Requires: openssl, curl, python3 (all present on macOS by default)

set -euo pipefail

GREEN="\033[32m"; YELLOW="\033[33m"; CYAN="\033[36m"
RED="\033[31m"; RESET="\033[0m"
ok()   { echo -e "  ${GREEN}✔${RESET}  $*" >&2; }
info() { echo -e "  ${CYAN}→${RESET}  $*" >&2; }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $*" >&2; }
err()  { echo -e "  ${RED}✘${RESET}  $*" >&2; exit 1; }

# ── Args ──────────────────────────────────────────────────────────────────────
APP_ROLE=""
# #697: HOS_CONFIG_DIR allows project-level config (e.g. ~/Code/CPS/.config/hos)
APPS_ENV="${HOS_CONFIG_DIR:-${HOME}/.config/hos}/apps.env"

while [[ $# -gt 0 ]]; do
    case $1 in
        --app) APP_ROLE="$2"; shift 2 ;;
        *)     echo "Usage: $0 --app [worker|overseer]" >&2; exit 1 ;;
    esac
done

[[ -n "$APP_ROLE" ]] || err "--app required (worker or overseer)"
[[ -f "$APPS_ENV" ]] || err "apps.env not found at $APPS_ENV — run hos_bootstrap.sh or set HOS_CONFIG_DIR"

# ── #633: verify apps.env permissions before sourcing ─────────────────────────
# #645: fail-closed — if stat fails (no stat available), error rather than allow unverified permissions
_env_mode=$(stat -c "%a" "$APPS_ENV" 2>/dev/null || stat -f "%OLp" "$APPS_ENV" 2>/dev/null)     || err "Cannot verify apps.env permissions — stat unavailable. Manually confirm: chmod 600 $APPS_ENV"
if [[ "$_env_mode" != "600" && "$_env_mode" != "400" ]]; then
    err "apps.env has permissions $_env_mode (expected 600). Run: chmod 600 $APPS_ENV"
fi

# shellcheck source=/dev/null
source "$APPS_ENV"

# DECLARED_BOT_LOGIN is the operator's *expected* identity for this role, taken
# from apps.env. It is deliberately a SEPARATE source from the API-authoritative
# slug derived below (#631) so the identity guard compares two independently-set
# values rather than one variable against itself (#703).
case "$APP_ROLE" in
    worker)
        APP_ID="$HOS_WORKER_APP_ID"
        PEM_PATH="$HOS_WORKER_PEM"
        DECLARED_BOT_LOGIN="${HOS_WORKER_BOT_LOGIN:-}"
        ;;
    overseer)
        APP_ID="$HOS_OVERSEER_APP_ID"
        PEM_PATH="$HOS_OVERSEER_PEM"
        DECLARED_BOT_LOGIN="${HOS_OVERSEER_BOT_LOGIN:-}"
        ;;
    *)  err "--app must be 'worker' or 'overseer'" ;;
esac

# ── Input validation (#545, #548) ─────────────────────────────────────────────
[[ -n "${HOS_REPO_OWNER:-}" ]] || err "HOS_REPO_OWNER not set in apps.env (e.g. HOS_REPO_OWNER=thurlow-research)"
[[ "$APP_ID" =~ ^[0-9]+$ ]]   || err "APP_ID must be numeric, got: $APP_ID"
# #703: the expected-identity declaration is load-bearing for the identity guard.
# Fail closed if apps.env omits it rather than exporting an empty expected value
# (an empty expected vs the API slug would fail the guard with a confusing message).
# Bash 3.2 safe: uppercase via tr, never ${var^^} (binding 7).
_role_uc="$(printf '%s' "$APP_ROLE" | tr '[:lower:]' '[:upper:]')"
[[ -n "$DECLARED_BOT_LOGIN" ]] || err "HOS_${_role_uc}_BOT_LOGIN not set in apps.env — required to verify the ${APP_ROLE} bot identity (#703). Add it (e.g. HOS_${_role_uc}_BOT_LOGIN='<appname>[bot]')."
# #633: resolve symlinks before prefix check to block path traversal
# #644: fail-closed — if python3 unavailable we cannot safely resolve symlinks
_resolved_pem=$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$PEM_PATH")     || err "python3 required to resolve PEM_PATH symlinks safely (CWE-59). Install python3 or verify $PEM_PATH is not a symlink."
# #697: validate against the same base as APPS_ENV (project-level or global)
_config_base="${HOS_CONFIG_DIR:-${HOME}/.config/hos}"
[[ "$_resolved_pem" == "${_config_base}/"* ]] || err "PEM_PATH must resolve under ${_config_base}/, got: $_resolved_pem"
[[ -f "$PEM_PATH" ]] || err "PEM not found: $PEM_PATH"

# ── JWT generation (RS256 via openssl — no Python crypto dep) ─────────────────
generate_jwt() {
    local app_id="$1" pem_path="$2"
    local now; now=$(date +%s)
    local header payload signing_input signature

    header=$(printf '{"alg":"RS256","typ":"JWT"}' \
        | openssl base64 -A | tr '+/' '-_' | tr -d '=')
    # GitHub requires iss as a string, not an integer (#546)
    payload=$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' \
        $((now - 60)) $((now + 600)) "$app_id" \
        | openssl base64 -A | tr '+/' '-_' | tr -d '=')

    signing_input="${header}.${payload}"
    signature=$(printf '%s' "$signing_input" \
        | openssl dgst -sha256 -sign "$pem_path" \
        | openssl base64 -A | tr '+/' '-_' | tr -d '=')

    printf '%s.%s' "$signing_input" "$signature"
}

info "Generating JWT for ${APP_ROLE} (app_id: ${APP_ID})..."
JWT=$(generate_jwt "$APP_ID" "$PEM_PATH")

# ── Verify App identity via API + get installations ───────────────────────────
# #631: derive BOT_LOGIN from GET /app (API-authoritative), not from apps.env.
# Both calls use the same JWT — consolidate into one network round-trip.
info "Verifying App identity and looking up installation for ${HOS_REPO_OWNER}..."
# #636: add curl timeouts — no --connect-timeout causes indefinite hang on network failure
APP_INFO=$(curl -sf --connect-timeout 10 --max-time 30 \
    -H "Authorization: Bearer ${JWT}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/app") \
    || err "Failed to reach /app — check App ID, PEM, and network. Verify system clock is synchronized (ntpd/chronyc): clock skew >60s causes JWT rejection. (#636, #640)"

# Derive bot login from app slug — authoritative, not from apps.env (#631)
_api_slug=$(printf '%s' "$APP_INFO" | python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('slug',''))" 2>/dev/null) || _api_slug=""
# #710: empty slug is not a safe fallback — it means the API response was malformed.
# Fail closed rather than silently re-enabling the apps.env circular dependency.
[[ -n "$_api_slug" ]] || err "GET /app returned empty slug — cannot verify bot identity. Check network and GitHub API availability. (#631, #710)"
# API-authoritative ACTUAL identity (what GitHub says this App authenticated as).
BOT_LOGIN="${_api_slug}[bot]"

# #703: fail closed at the source if the App we actually authenticated as does not
# match the operator's declared identity for this role. Previously both sides of
# the identity guard came from this same API slug, making the comparison a
# tautology; cross-checking against the independent apps.env declaration catches a
# mis-mapped APP_ID/PEM (e.g. overseer credentials under the worker role) or a
# stale/incorrect declared login before any token is exported.
[[ "$BOT_LOGIN" == "$DECLARED_BOT_LOGIN" ]] || err "Identity mismatch: authenticated as '$BOT_LOGIN' but apps.env declares HOS_${_role_uc}_BOT_LOGIN='$DECLARED_BOT_LOGIN'. Verify the ${APP_ROLE} APP_ID/PEM and the declared login in apps.env. (#703)"

INSTALL_RESPONSE=$(curl -sf --connect-timeout 10 --max-time 30 \
    -H "Authorization: Bearer ${JWT}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/app/installations") \
    || err "Failed to reach /app/installations — check App ID, PEM, and network. (#636, #640)"

# #630: pass HOS_REPO_OWNER via environment variable, not string interpolation.
# Interpolating into a Python -c string allows injection if value contains quotes.
INSTALL_ID=$(printf '%s' "$INSTALL_RESPONSE" | HOS_REPO_OWNER="$HOS_REPO_OWNER" python3 -c "
import json, sys, os
owner = os.environ['HOS_REPO_OWNER']
for i in json.loads(sys.stdin.read()):
    if i.get('account', {}).get('login') == owner:
        print(i['id'])
        sys.exit(0)
")

[[ -n "$INSTALL_ID" ]] || err "No installation found for ${HOS_REPO_OWNER} — install the App on the repo at github.com/settings/apps"

# ── Get installation token ────────────────────────────────────────────────────
info "Fetching installation token (installation: ${INSTALL_ID})..."
# #636: curl timeouts on token fetch too
TOKEN_RESPONSE=$(curl -sf --connect-timeout 10 --max-time 30 -X POST \
    -H "Authorization: Bearer ${JWT}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/app/installations/${INSTALL_ID}/access_tokens") \
    || err "Failed to get installation token — check network. If App ID and PEM are correct, verify system clock synchronization. (#636, #640)"

# #632/#634: unset JWT immediately — it is valid for 10 min and must not linger in shell state
unset JWT

TOKEN=$(printf '%s' "$TOKEN_RESPONSE" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['token'])")
EXPIRES=$(printf '%s' "$TOKEN_RESPONSE" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('expires_at','unknown'))")
TOKEN_RESPONSE=""  # clear raw response — contains token (#549)

[[ -n "$TOKEN" ]] || err "Empty token in response"

ok "${APP_ROLE} token obtained (expires: ${EXPIRES})"

# ── Output (sourced into caller's shell) ──────────────────────────────────────
# #632: GH_TOKEN is exported into env so `gh` can inherit it — child-process exposure
# is an accepted trade-off for the current architecture. Tracked for v0.5.0 explicit-passing
# refactor (#632). Do NOT add further secrets to this export list.
printf "export GH_TOKEN='%s'\n"              "$TOKEN"
# #703: the two identity values come from independent sources so the downstream
# identity guard is a real comparison, not a tautology:
#   HOS_BOT_LOGIN          = API-authoritative slug (actual; #631)
#   HOS_EXPECTED_BOT_LOGIN = operator's apps.env declaration (expected)
printf "export HOS_BOT_LOGIN='%s'\n"          "$BOT_LOGIN"
printf "export HOS_EXPECTED_BOT_LOGIN='%s'\n" "$DECLARED_BOT_LOGIN"  # #699/#703: cron identity guard
