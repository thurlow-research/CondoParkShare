#!/usr/bin/env bash
# audit_conditional_proceed.sh — retroactive audit of CONDITIONAL_PROCEED PRs (#370).
#
# Reads the per-entry audit records under audit/log/, finds CONDITIONAL_PROCEED
# events, and for each merged PR checks (heuristically) whether a human actioned
# the conditional items: a non-bot reply after the earliest CP timestamp.
#
# This is a one-time, READ-ONLY audit tool. It files no issues, modifies no PRs,
# and never writes to the audit log. Findings are work items for a human.
#
# Usage:
#   ./scripts/oversight/audit_conditional_proceed.sh [--root <path>] [--help]
#
# Options:
#   --root <path>  Override repo root holding audit/log/ (default: enclosing git repo)
#   --help, -h     Show this help and exit
#
# Environment overrides:
#   OVERSIGHT_ACCOUNT  overseer bot login to exclude  (default: hos-overseer-hos[bot])
#   WORKER_ACCOUNT     worker  bot login to exclude   (default: hos-worker-hos[bot])
#   GH_API_DELAY_MS    inter-PR API delay in ms       (default: 200)
#
# Output: tab-separated rows  PR_NUMBER<TAB>STATUS<TAB>DETAILS
#   STATUS ∈ { LIKELY_ACTIONED | NEEDS_REVIEW | NO_MERGE | UNKNOWN }
# Written to stdout and to .claudetmp/audit/conditional_proceed_audit_<date>.txt
#
# Exit codes:
#   0  ran to completion (NEEDS_REVIEW findings are human work, not errors)
#   1+ prerequisite failure (missing jq/gh/log/auth, not a repo) or fatal error

set -euo pipefail

# ── Colours / log helpers (match run_review_chain.sh) ─────────────────────────
GREEN="\033[32m"; YELLOW="\033[33m"; CYAN="\033[36m"
RED="\033[31m"; BOLD="\033[1m"; RESET="\033[0m"
ok()   { echo -e "  ${GREEN}✔${RESET}  $*" >&2; }
info() { echo -e "  ${CYAN}→${RESET}  $*" >&2; }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $*" >&2; }
err()  { echo -e "  ${RED}✘${RESET}  $*" >&2; }
die()  { err "$*"; exit 1; }

# Read-shim seam (SPEC-888 P3): the audit event stream is reconstructed from the
# per-entry records under <root>/audit/log/ via audit_read_stream. The helper is
# side-effect-free and safe to source under `set -e`.
# shellcheck source=lib/audit_log.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/audit_log.sh"

# ── Arg parsing ───────────────────────────────────────────────────────────────
ROOT_OVERRIDE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      [[ $# -ge 2 ]] || die "--root requires a path"
      ROOT_OVERRIDE="$2"
      shift 2 ;;
    --help|-h)
      sed -n '2,31p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)
      die "Unknown option: $1  (try --help)" ;;
  esac
done

# ── Account / config resolution ───────────────────────────────────────────────
OVERSIGHT_ACCOUNT="${OVERSIGHT_ACCOUNT:-hos-overseer-hos[bot]}"
WORKER_ACCOUNT="${WORKER_ACCOUNT:-hos-worker-hos[bot]}"
GH_API_DELAY_MS="${GH_API_DELAY_MS:-200}"
# Bot logins excluded from "human reply" — overseer, worker, and CI bot.
BOTS_JSON="$(printf '["%s","%s","github-actions[bot]"]' "$OVERSIGHT_ACCOUNT" "$WORKER_ACCOUNT")"

# ── Prerequisite checks (fail-closed before any processing) ───────────────────
command -v jq >/dev/null 2>&1 || die "jq not found — required for JSONL parsing (install jq)"
command -v gh >/dev/null 2>&1 || die "gh CLI not found — required for GitHub API access"
command -v python3 >/dev/null 2>&1 || die "python3 not found — required by the audit read-shim"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$REPO_ROOT" ]] || die "not inside a git repository"

# Repo root whose audit/log/ holds the per-entry records. A missing audit/log
# directory yields an empty stream (zero events) rather than an error — the
# read-shim is total, so an absent log produces an empty report, not a failure.
AUDIT_ROOT="${ROOT_OVERRIDE:-$REPO_ROOT}"

gh auth status >/dev/null 2>&1 || die "gh is not authenticated — run 'gh auth login'"

REPO_SLUG="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
[[ -n "$REPO_SLUG" ]] || die "could not determine repo slug via gh repo view"

# ── Startup banner (R3: print resolved accounts so the operator can verify) ───
echo "" >&2
echo -e "${BOLD}CONDITIONAL_PROCEED audit${RESET}" >&2
info "repo:            $REPO_SLUG"
info "audit root:      $AUDIT_ROOT  (audit/log/)"
info "overseer acct:   $OVERSIGHT_ACCOUNT"
info "worker acct:     $WORKER_ACCOUNT"
info "ci bot excluded: github-actions[bot]"
info "api delay:       ${GH_API_DELAY_MS}ms"
echo "" >&2

# ── Step A: parse CONDITIONAL_PROCEED events ──────────────────────────────────
# Emit TSV: pr_number<TAB>timestamp<TAB>items_count  (one per matching log line).
# Tolerate absent/null/empty conditional_items and skip malformed JSON lines.
CP_TSV="$(
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    printf '%s\n' "$line" | jq -r '
      select((.event // "" | ascii_downcase) == "conditional_proceed")
      | select((.pr_number // "") != "")
      | [ (.pr_number|tostring),
          (.timestamp // ""),
          ( .conditional_items
            | if . == null then 0
              elif type == "array" then length
              else 0 end )
        ] | @tsv
    ' 2>/dev/null || true
  done < <(audit_read_stream "$AUDIT_ROOT")
)"

N_EVENTS="$(printf '%s\n' "$CP_TSV" | grep -c . || true)"

# Dedup to one row per PR using the EARLIEST timestamp (architect binding B2).
# ISO-8601 UTC 'Z' timestamps sort chronologically as plain text. Sort by
# pr_number then timestamp ascending, take the first row seen per pr_number.
CANDIDATES="$(
  printf '%s\n' "$CP_TSV" \
    | grep . \
    | sort -t "$(printf '\t')" -k1,1 -k2,2 \
    | awk -F '\t' '!seen[$1]++'
)"

# ── Report scaffolding ────────────────────────────────────────────────────────
GENERATED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
DATE_STAMP="$(date -u +%Y-%m-%d)"
OUT_DIR="$REPO_ROOT/.claudetmp/audit"
mkdir -p "$OUT_DIR"
OUT_FILE="$OUT_DIR/conditional_proceed_audit_${DATE_STAMP}.txt"

# tee_out: write a line to both stdout and the report file.
REPORT_BUF=""
emit() { REPORT_BUF+="$1"$'\n'; printf '%s\n' "$1"; }

emit "CONDITIONAL_PROCEED Audit Report"
emit "Generated: $GENERATED"
emit "Repo: $REPO_SLUG"
emit "Audit root: $AUDIT_ROOT/audit/log ($N_EVENTS CONDITIONAL_PROCEED events found)"
emit ""
emit "$(printf '%s\t%s\t%s' 'PR_NUMBER' 'STATUS' 'DETAILS')"
emit "$(printf '%s\t%s\t%s' '---------' '------' '-------')"

# ── Per-PR processing ─────────────────────────────────────────────────────────
n_merged=0
n_needs=0
n_likely=0
n_nomerge=0
n_unknown=0

DELAY_SEC="$(awk "BEGIN{printf \"%.3f\", ${GH_API_DELAY_MS}/1000}")"

if [[ -n "$CANDIDATES" ]]; then
  while IFS=$'\t' read -r pr anchor items; do
    [[ -n "$pr" ]] || continue
    items_label="$items"
    [[ "$items" == "0" ]] && items_label="not logged"

    # Step B — merged status
    merged="$(gh api "repos/${REPO_SLUG}/pulls/${pr}" --jq '.merged' 2>/dev/null || echo "ERROR")"
    # A transient API error must NOT be folded into NO_MERGE: unlike UNKNOWN,
    # NO_MERGE carries no re-run signal, so a genuinely-merged CP PR would be
    # silently dropped. Branch on the ERROR sentinel first → UNKNOWN (re-run);
    # only an explicit non-true value is a real NO_MERGE.
    if [[ "$merged" == "ERROR" ]]; then
      emit "$(printf '#%s\t%s\t%s' "$pr" 'UNKNOWN' "merged-status fetch failed (rate-limit or API error); items=${items_label}")"
      n_unknown=$((n_unknown + 1))
      sleep "$DELAY_SEC"
      continue
    fi
    if [[ "$merged" != "true" ]]; then
      emit "$(printf '#%s\t%s\t%s' "$pr" 'NO_MERGE' "not merged (or not found); items=${items_label}")"
      n_nomerge=$((n_nomerge + 1))
      sleep "$DELAY_SEC"
      continue
    fi
    n_merged=$((n_merged + 1))

    # Step C — human-actioning heuristic (non-bot reply after earliest CP anchor)
    comments_json="$(gh api "repos/${REPO_SLUG}/issues/${pr}/comments" --paginate 2>/dev/null || echo "ERROR")"
    if [[ "$comments_json" == "ERROR" ]]; then
      emit "$(printf '#%s\t%s\t%s' "$pr" 'UNKNOWN' "comment fetch failed (rate-limit or API error); items=${items_label}")"
      n_unknown=$((n_unknown + 1))
      sleep "$DELAY_SEC"
      continue
    fi

    qualifying="$(
      printf '%s' "$comments_json" | jq --arg ts "$anchor" --argjson bots "$BOTS_JSON" '
        [ .[]
          | select((.created_at // "") > $ts)
          | select((.user.type // "") != "Bot")
          | select((.user.login // "") as $l | ($bots | index($l)) | not)
        ] | length
      ' 2>/dev/null || echo "ERROR"
    )"

    if [[ "$qualifying" == "ERROR" ]]; then
      emit "$(printf '#%s\t%s\t%s' "$pr" 'UNKNOWN' "comment parse failed; items=${items_label}")"
      n_unknown=$((n_unknown + 1))
    elif [[ "$qualifying" -ge 1 ]]; then
      emit "$(printf '#%s\t%s\t%s' "$pr" 'LIKELY_ACTIONED' "merged; ${qualifying} non-bot reply(ies) after CP @ ${anchor}; items=${items_label}")"
      n_likely=$((n_likely + 1))
    else
      emit "$(printf '#%s\t%s\t%s' "$pr" 'NEEDS_REVIEW' "merged; no non-bot reply after CP @ ${anchor}; items=${items_label}")"
      n_needs=$((n_needs + 1))
    fi

    sleep "$DELAY_SEC"
  done <<< "$CANDIDATES"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
emit ""
emit "Summary"
emit "  Total CONDITIONAL_PROCEED events: $N_EVENTS"
emit "  Merged PRs examined:              $n_merged"
emit "  NEEDS_REVIEW (file as issues):    $n_needs"
emit "  LIKELY_ACTIONED (no action req):  $n_likely"
emit "  NO_MERGE (skipped):               $n_nomerge"
emit "  UNKNOWN (API error, re-run):      $n_unknown"
emit ""
emit "NOTE: STATUS is heuristic — \"human reply after CP verdict\" is not a definitive"
emit "determination. Manually verify each NEEDS_REVIEW PR before filing findings as issues."

# Persist report to .claudetmp/audit/ (same-day re-run overwrites).
printf '%s' "$REPORT_BUF" > "$OUT_FILE"
echo "" >&2
ok "report written to $OUT_FILE"

exit 0
