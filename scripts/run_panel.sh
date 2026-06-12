#!/usr/bin/env bash
# run_panel.sh — the local cross-vendor multi-agent review panel (Layer 2).
#
# Runs the independent-review stage of the oversight pipeline (METHODOLOGY.md §6,
# steps 7–10) LOCALLY, because the reviewer CLIs authenticate against paid
# *subscriptions* (Claude Max / ChatGPT Pro / Gemini Pro) via interactive browser
# OAuth that CI runners can't hold. The panel posts its findings back to the PR so
# the PR stays the auditable record.
#
# Pipeline (this script):
#   TRIAGE     deterministic floor ∪ author's AI-Risk trailer, confirmed/raised by Haiku
#   REVIEWERS  cross-vendor fan-out scaled by risk (Opus is the author → never reviews)
#   ARBITER    Sonnet synthesizes + dedups the independent findings into one verdict
#   POST       one line-level review THREAD per finding (author responds → must resolve)
#              + one arbiter SUMMARY comment for the human's overview
#
# Reviewer roster by risk (see DECISIONS.md D15/D17/D18/D19):
#   MEDIUM   → agy (correctness) + ipcheck (IP/provenance)  [+ Copilot native, not driven here]
#   HIGH     → agy (correctness) + codex (security) + codex (adversary) + ipcheck (IP/provenance)
#   CRITICAL → same roster; + blast-radius required + mandatory human gate
#   LOW      → panel skipped — UNLESS picked by the random red-team audit (below)
# Red-team (adversary) is ALWAYS-on at HIGH+ and PROBABILISTIC (SQC sample) at LOW/MEDIUM.
# IP/provenance (ipcheck) is a LOCAL built-in agent (not a vendor CLI) — a PLACEHOLDER today
# (no analysis yet), grown over time (D19); a clean verdict from it is NOT yet an IP clearance.
#
# Random red-team audit (SQC, D17): a salted-deterministic % of LOWER-tier PRs get an
# extra adversary pass — LOW/MEDIUM sampled at OVERSIGHT_SAMPLE_LOW/MED (pilot: 25/50,
# production: 5/15). Sampling is reproducible (head SHA + secret salt) and logged to
# .ai-local/panel/sample-log.jsonl for the escaped-defect-rate metric. --no-sample disables.
#
# Why threads, not just a summary: the panel is ADVERSARIAL. A finding isn't "done"
# when it's posted — it's done when the author RESPONDS and the thread RESOLVES. Under
# `required_review_thread_resolution` on main (D12), each thread must be addressed
# before merge. That is the human-attention routing mechanism.
#
# Usage:
#   ./scripts/run_panel.sh [PR#]          # defaults to the current branch's open PR
#   ./scripts/run_panel.sh 42
#   ./scripts/run_panel.sh 42 --risk HIGH # force a risk level (skips triage's judgement)
#   ./scripts/run_panel.sh 42 --dry-run   # run reviewers + arbiter, print, post NOTHING
#   ./scripts/run_panel.sh 42 --no-sample # skip the random red-team audit for this run
#   ./scripts/run_panel.sh --help
#
# Requires (install via setup_clis.sh): gh (authed), claude, agy, codex, plus jq + python3.
# Findings I/O is best-effort JSON (D15d): reviewers are asked for a JSON schema and
# parsed leniently (prose/fences tolerated); malformed output degrades to "no findings".

set -euo pipefail

# ── Colours / log helpers (match setup_clis.sh / setup_oversight.sh) ───────────
GREEN="\033[32m"; YELLOW="\033[33m"; CYAN="\033[36m"
RED="\033[31m"; BOLD="\033[1m"; RESET="\033[0m"
ok()   { echo -e "  ${GREEN}✔${RESET}  $*"; }
skip() { echo -e "  ${YELLOW}–${RESET}  $*"; }
info() { echo -e "  ${CYAN}→${RESET}  $*"; }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $*"; }
err()  { echo -e "  ${RED}✘${RESET}  $*" >&2; }
die()  { err "$*"; exit 1; }

# ── Tunables ───────────────────────────────────────────────────────────────────
CAP=60000          # max diff chars sent to a reviewer in one call; larger → chunk by file
SIZE_FLOOR=500     # added-line count that bumps the deterministic floor to MEDIUM
# Random red-team audit (Statistical Quality Control): % of LOWER-tier PRs given an
# adversarial pass at random, to measure escaped-defect rate + deter risk under-declaration.
# NOTE: these are ELEVATED PILOT rates so the audit actually fires at low PR volume and
# we can observe the mechanism. Production targets are LOW 5% / MEDIUM 15% (see DECISIONS.md
# D17) — lower them (or set OVERSIGHT_SAMPLE_LOW/MED) once the flow is proven.
SAMPLE_LOW="${OVERSIGHT_SAMPLE_LOW:-25}"    # % of LOW PRs sampled for a red-team audit (pilot)
SAMPLE_MED="${OVERSIGHT_SAMPLE_MED:-50}"    # % of MEDIUM PRs sampled for a red-team audit (pilot)
SALT_FILE=".ai-local/sample.salt"           # secret salt → sample is reproducible & non-gameable

# ── Args ─────────────────────────────────────────────────────────────────────--
PR=""; DRY_RUN=0; RISK_OVERRIDE=""; DO_SAMPLE=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)   DRY_RUN=1; shift ;;
    --risk)      [[ $# -ge 2 ]] || die "--risk needs a value (LOW|MEDIUM|HIGH|CRITICAL)"
                 RISK_OVERRIDE="$(echo "$2" | tr '[:lower:]' '[:upper:]')"; shift 2 ;;
    --no-sample) DO_SAMPLE=0; shift ;;
    --help|-h)   sed -n '2,43p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)          die "Unknown option: $1  (try --help)" ;;
    *)           PR="$1"; shift ;;
  esac
done

# ── Risk ranking helpers ───────────────────────────────────────────────────────
rank() { case "$1" in LOW) echo 0;; MEDIUM) echo 1;; HIGH) echo 2;; CRITICAL) echo 3;; *) echo 0;; esac; }
max_risk() { [[ "$(rank "$1")" -ge "$(rank "$2")" ]] && echo "$1" || echo "$2"; }

# ── JSON extraction (best-effort — tolerate prose / code fences around the JSON) ─
extract_json() {
  # NB: stdin is captured to a temp file FIRST — `python3 - <<'PY'` makes the heredoc
  # python's stdin, so the program can't also read the piped data from stdin.
  local _tmp; _tmp="$(mktemp)"; cat > "$_tmp"
  python3 - "$_tmp" <<'PY'
import sys, json, re
data = open(sys.argv[1]).read()
def load(s):
    try: return json.loads(s)
    except Exception: return None
obj = load(data)                                   # 1) whole string is clean JSON (common)
if obj is None:                                    # 2) fenced ```json ... ``` block
    m = re.search(r'```(?:json)?\s*(.*?)```', data, re.S)
    if m: obj = load(m.group(1))
if obj is None:                                    # 3) JSON embedded in prose — raw_decode
    dec = json.JSONDecoder()                       #    (robust to literal braces in strings
    for i, ch in enumerate(data):                  #     and to trailing text after the JSON)
        if ch in '{[':
            try: obj, _ = dec.raw_decode(data[i:]); break
            except Exception: continue
print(json.dumps(obj if obj is not None else {"findings": []}))
PY
  rm -f "$_tmp"
}

# ── Model dispatch (subscription CLIs; Opus is the author and is NEVER called here) ─
call_model() {
  local which="$1" prompt="$2"
  case "$which" in
    haiku)  claude -p --model haiku  "$prompt" 2>>"$RUN_DIR/errors.log" ;;
    sonnet) claude -p --model sonnet "$prompt" 2>>"$RUN_DIR/errors.log" ;;
    agy)    agy   -p "$prompt"                 2>>"$RUN_DIR/errors.log" ;;
    codex)  codex exec "$prompt"               2>>"$RUN_DIR/errors.log" ;;
    ipcheck) ip_agent "$prompt" ;;             # LOCAL built-in agent — not a vendor CLI
    *)      echo "" ;;
  esac
}

# ── IP / provenance agent (LOCAL built-in) ───────────────────────────────────────
# A first-class panel member checking INTELLECTUAL-PROPERTY exposure — a risk axis
# ORTHOGONAL to correctness/security (D19): copyleft/unknown-license code or deps
# entering the tree, verbatim regurgitation of copyrighted training-data code, and
# permissively-licensed code copied without its required attribution/notice. Unlike the
# other reviewers it is not a vendor LLM CLI but a local function we own — so its brain
# can grow without ever re-wiring the panel around it.
#
# IP/provenance agent — now FUNCTIONAL (IP_STUB=0 is the default).
#
# Growth path (DECISIONS.md D19) — what is implemented:
#   Level 1 ✅: dependency license gate via ip_check.py
#              Uses ScanCode Toolkit if installed (pip install scancode-toolkit)
#              Falls back to PyPI/npm API. Flags copyleft/unknown licenses.
#   Level 2 ✅: prompt clean-room verification via ip_check.py
#              Reads prompt artifacts (Prompt-Artifact: git trailers → prompts/)
#              Flags attribution triggers in prompts; notes clean-room signals.
#   Level 3 🔧: regurgitation lens — STUB
#              Planned: ai-gen-code-search (AboutCode, LSH snippet matching)
#              https://github.com/aboutcode-org/ai-gen-code-search
#              Install: pip install ai-gen-code-search
#              Activate: set IP_REGURGITATION_ENABLED=1
#
# ScanCode install (all platforms): pip install scancode-toolkit
# On Faberix (Ubuntu long-runner): pip install --user scancode-toolkit
#
# Set IP_STUB=1 to revert to empty stub (disables all three levels).
IP_STUB="${IP_STUB:-0}"
ip_agent() {  # $1 = the standard review prompt/diff
  if [[ "$IP_STUB" == "1" ]]; then
    echo '{"findings":[]}'
    return
  fi
  # Build file list from changed files (global CHANGED_FILES set in preflight)
  local changed_files_arr=()
  while IFS= read -r f; do
    [[ -n "$f" ]] && changed_files_arr+=("$f")
  done <<< "${CHANGED_FILES:-}"

  if [[ ${#changed_files_arr[@]} -eq 0 ]]; then
    echo '{"findings":[],"note":"no changed files detected"}'
    return
  fi

  local ip_script
  ip_script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/oversight/validators/ip_check.py"

  if [[ ! -f "$ip_script" ]]; then
    # Try relative to CWD (when run from project root)
    ip_script="scripts/oversight/validators/ip_check.py"
  fi

  if [[ -f "$ip_script" ]]; then
    python3 "$ip_script" --prompts-dir "prompts" "${changed_files_arr[@]}" 2>/dev/null || \
      echo '{"findings":[],"error":"ip_check.py failed"}'
  else
    echo '{"findings":[],"note":"ip_check.py not found — install HOS scripts first"}'
  fi
}

# ── Preflight ───────────────────────────────────────────────────────────────---
echo ""
echo -e "${BOLD}AI Oversight — multi-agent review panel${RESET}"
echo ""

for bin in gh jq python3; do command -v "$bin" >/dev/null 2>&1 || die "$bin not found — see setup_clis.sh"; done
gh auth status >/dev/null 2>&1 || die "gh not authenticated — run: gh auth login"
for bin in claude agy codex; do
  command -v "$bin" >/dev/null 2>&1 || warn "$bin not on PATH — reviews assigned to it will be skipped"
done

# Resolve PR (arg or current branch) and its head SHA / changed files.
if [[ -z "$PR" ]]; then
  PR="$(gh pr view --json number -q .number 2>/dev/null || true)"
  [[ -n "$PR" ]] || die "no PR number given and no open PR for the current branch"
fi
HEAD_SHA="$(gh pr view "$PR" --json headRefOid -q .headRefOid 2>/dev/null || true)"
[[ -n "$HEAD_SHA" ]] || die "could not resolve PR #$PR (is it open, and is gh pointed at the right repo?)"
PR_TITLE="$(gh pr view "$PR" --json title -q .title 2>/dev/null || echo "(unknown)")"

RUN_DIR=".ai-local/panel/pr${PR}-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR"
DIFF_FILE="$RUN_DIR/pr.diff"
gh pr diff "$PR" > "$DIFF_FILE" 2>/dev/null || die "could not fetch diff for PR #$PR"
CHANGED_FILES="$(gh pr diff "$PR" --name-only 2>/dev/null || true)"

# Load PANEL context written by oversight-orchestrator.
# Independence principle: ONLY use step{N}-panel-context.md (structural risk signals,
# no internal findings). Do NOT fall back to step{N}-handoff.md — that file contains
# internal reviewer findings and would anchor cross-vendor reviewers to what the internal
# team already found, violating decorrelation. Fail-closed if missing: the orchestrator
# must produce panel-context.md before the panel runs.
HANDOFF_CONTEXT=""
HANDOFF_FILE="$(python3 - <<'PYEOF' 2>/dev/null
import glob, re
files = glob.glob('.claudetmp/oversight/step*-panel-context.md')
if files:
    files.sort(key=lambda f: int(m.group(1)) if (m := re.search(r'step(\d+)', f)) else 0)
    print(files[-1])
PYEOF
)"
if [[ -n "$HANDOFF_FILE" && -f "$HANDOFF_FILE" ]]; then
    HANDOFF_CONTEXT="$(cat "$HANDOFF_FILE")"
    info "Panel context: $HANDOFF_FILE ($(wc -c < "$HANDOFF_FILE") bytes)"
elif [[ $DRY_RUN -eq 0 ]]; then
    warn "No panel-context.md found in .claudetmp/oversight/ — proceeding without context"
    warn "Run oversight-orchestrator to generate step{N}-panel-context.md before the panel"
    warn "(Refusing to use handoff.md — it contains internal findings that violate independence)"
fi
ADDED=$(grep -cE '^\+([^+]|$)' "$DIFF_FILE" 2>/dev/null || true); ADDED=${ADDED:-0}  # counts blank added lines; excludes +++ header

info "PR #$PR — $PR_TITLE"
info "head $HEAD_SHA · $(echo "$CHANGED_FILES" | grep -c . ) file(s) · +${ADDED} lines · run dir $RUN_DIR"

# ── TRIAGE — deterministic floor ∪ author trailer, confirmed/raised by Haiku ────
det_floor() {
  local files="$1" added="$2" level="LOW"
  echo "$files" | grep -qiE '\.(ts|tsx|js|jsx|py|go|rb|java|cs|php|rs|sh)$' && level="MEDIUM"
  echo "$files" | grep -qiE '(package\.json|package-lock|yarn\.lock|pnpm-lock|requirements\.txt|go\.mod|Gemfile|Cargo\.toml|composer\.json)' && level="MEDIUM"
  (( added > SIZE_FLOOR )) && level="$(max_risk "$level" MEDIUM)"
  echo "$files" | grep -qiE '(auth|login|session|middleware|password|token|crypto|secret|/api/|routes?/|migrations?/|schema|/db/|sql)' && level="$(max_risk "$level" HIGH)"
  echo "$files" | grep -qiE '(payment|billing|stripe|checkout|/delete|destroy|drop_)' && level="$(max_risk "$level" CRITICAL)"
  echo "$level"
}

FLOOR="$(det_floor "$CHANGED_FILES" "$ADDED")"
AUTHOR_RISK="$(gh pr view "$PR" --json commits -q '.commits[].messageBody' 2>/dev/null \
  | grep -oiE 'AI-Risk:[[:space:]]*(LOW|MEDIUM|HIGH|CRITICAL)' \
  | grep -oiE '(LOW|MEDIUM|HIGH|CRITICAL)' | tr '[:lower:]' '[:upper:]' \
  | sort -u | while read -r r; do echo "$(rank "$r") $r"; done | sort -rn | head -1 | awk '{print $2}' || true)"
[[ -n "$AUTHOR_RISK" ]] && FLOOR="$(max_risk "$FLOOR" "$AUTHOR_RISK")"

if [[ -n "$RISK_OVERRIDE" ]]; then
  RISK="$RISK_OVERRIDE"
  info "triage: floor=$FLOOR author=${AUTHOR_RISK:-none} → forced ${BOLD}$RISK${RESET} (--risk)"
elif command -v claude >/dev/null 2>&1; then
  TRIAGE_PROMPT="You are the TRIAGE agent in a code-oversight panel. Classify the risk of this PR using:
LOW=pure UI/styling, no logic. MEDIUM=business logic/data/state/routing. HIGH=auth/input-handling/persistence/external-APIs. CRITICAL=injection/PII/payments/destructive ops.
Deterministic floor already computed: $FLOOR. Author self-declared: ${AUTHOR_RISK:-none}.
You may CONFIRM or RAISE the risk, but NEVER lower it below the floor ($FLOOR).
Changed files:
$CHANGED_FILES

Diff (truncated):
$(head -c "$CAP" "$DIFF_FILE")

Return ONLY JSON: {\"risk\":\"LOW|MEDIUM|HIGH|CRITICAL\",\"reason\":\"one sentence\"}"
  TRIAGE_RAW="$(call_model haiku "$TRIAGE_PROMPT")"
  echo "$TRIAGE_RAW" > "$RUN_DIR/triage.raw.txt"
  HAIKU_RISK="$(printf '%s' "$TRIAGE_RAW" | extract_json | jq -r '.risk // empty' | tr '[:lower:]' '[:upper:]')"
  TRIAGE_REASON="$(printf '%s' "$TRIAGE_RAW" | extract_json | jq -r '.reason // empty')"
  RISK="$(max_risk "$FLOOR" "${HAIKU_RISK:-$FLOOR}")"
  info "triage: floor=$FLOOR author=${AUTHOR_RISK:-none} haiku=${HAIKU_RISK:-?} → ${BOLD}$RISK${RESET}"
  [[ -n "$TRIAGE_REASON" ]] && info "        \"$TRIAGE_REASON\""
else
  RISK="$FLOOR"
  warn "claude unavailable — triage falls back to deterministic floor: $RISK"
fi

# ── Random red-team audit (SQC) — salted-deterministic sample of LOWER-tier PRs ──
# selected ⇔ SHA256(head_sha + secret_salt) mod 100 < tier_rate. Reproducible (an
# auditor with the salt can prove a PR was/wasn't sampled) and non-gameable (the salt
# is secret, so an author can't grind commit hashes to dodge the sample).
sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi | awk '{print $1}'; }
SAMPLED=0; ROLL=-1; RATE=0
if (( DO_SAMPLE )); then
  # Acquire (or mint) the secret salt — kept in gitignored .ai-local so it persists.
  if [[ -n "${OVERSIGHT_SAMPLE_SALT:-}" ]]; then SALT="$OVERSIGHT_SAMPLE_SALT"
  elif [[ -f "$SALT_FILE" ]]; then SALT="$(cat "$SALT_FILE")"
  else
    mkdir -p "$(dirname "$SALT_FILE")"
    SALT="$(openssl rand -hex 16 2>/dev/null || head -c16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    printf '%s' "$SALT" > "$SALT_FILE"; chmod 600 "$SALT_FILE"
    warn "minted a new audit salt at $SALT_FILE (gitignored) — keep it; it makes the sample reproducible & non-gameable"
  fi
  case "$RISK" in LOW) RATE=$SAMPLE_LOW ;; MEDIUM) RATE=$SAMPLE_MED ;; *) RATE=0 ;; esac  # HIGH/CRITICAL already 100%
  if (( RATE > 0 )); then
    HEX=$(printf '%s' "${HEAD_SHA}${SALT}" | sha256 | cut -c1-8)
    ROLL=$(( 0x$HEX % 100 ))
    (( ROLL < RATE )) && SAMPLED=1
    mkdir -p "$(dirname "$SALT_FILE")"
    printf '{"ts":"%s","pr":%s,"head":"%s","tier":"%s","rate":%s,"roll":%s,"selected":%s}\n' \
      "$(date -u +%FT%TZ)" "$PR" "$HEAD_SHA" "$RISK" "$RATE" "$ROLL" \
      "$([[ $SAMPLED -eq 1 ]] && echo true || echo false)" >> ".ai-local/panel/sample-log.jsonl"
    if (( SAMPLED )); then info "🎲 red-team audit: $RISK roll=$ROLL < $RATE% → ${BOLD}SELECTED${RESET} (adds adversary pass)"
    else                   info "🎲 red-team audit: $RISK roll=$ROLL ≥ $RATE% → not selected"; fi
  fi
fi

# LOW reaches the panel ONLY when selected for a random red-team audit.
if [[ "$(rank "$RISK")" -lt 1 ]]; then
  if (( SAMPLED )); then
    warn "LOW tier, but SELECTED for a random red-team audit — running an adversarial panel."
  else
    ok "Risk is $RISK and not in the audit sample — panel does not run (Copilot floor + deterministic gates + spot-check apply)."
    exit 0
  fi
fi

# ── Reviewer roster for this risk level (+ red-team if sampled) ──────────────────
if [[ "$RISK" == "LOW" ]]; then
  ROSTER=("agy:correctness" "codex:adversary" "ipcheck:ip")   # only reached when SAMPLED
else
  ROSTER=("agy:correctness")
  [[ "$(rank "$RISK")" -ge 2 ]] && ROSTER+=("codex:security")                     # HIGH+
  [[ "$(rank "$RISK")" -ge 2 ]] && ROSTER+=("codex:adversary")                    # HIGH+ : red-team ALWAYS (D18)
  [[ "$RISK" == "MEDIUM" && $SAMPLED -eq 1 ]] && ROSTER+=("codex:adversary")      # sampled MEDIUM (SQC)
  ROSTER+=("ipcheck:ip")                                                          # IP/provenance — every panel run (D19)
fi
IP_IN_ROSTER=0; printf '%s\n' "${ROSTER[@]}" | grep -q '^ipcheck:' && IP_IN_ROSTER=1
info "roster ($RISK$( ((SAMPLED)) && echo ' +audit' )): ${ROSTER[*]}   (Opus authored → excluded; Copilot runs natively in CI)"

# ── Chunk the diff if it exceeds the cap (split on file boundaries) ─────────────
CHUNKS=()
DIFF_SIZE=$(wc -c < "$DIFF_FILE")
if (( DIFF_SIZE <= CAP )); then
  CHUNKS=("$DIFF_FILE")
else
  CDIR="$RUN_DIR/chunks"; mkdir -p "$CDIR"
  awk -v d="$CDIR" 'BEGIN{n=0} /^diff --git /{n++; f=sprintf("%s/chunk-%03d.diff", d, n)} {if(n==0){n=1; f=sprintf("%s/chunk-%03d.diff", d, n)} print > f}' "$DIFF_FILE"
  for f in "$CDIR"/chunk-*.diff; do CHUNKS+=("$f"); done
  warn "diff is ${DIFF_SIZE}B > ${CAP}B cap → chunked into ${#CHUNKS[@]} file-group(s)"
fi

lens_brief() {
  case "$1" in
    correctness) echo "logic errors, wrong edge cases, off-by-one, incorrect or HALLUCINATED library/framework APIs, broken assumptions, missing error handling." ;;
    security)    echo "injection (SQL/cmd/XSS/SSRF/CSRF), auth/authorization flaws, secret/credential mishandling, unsafe handling of untrusted input, insecure defaults." ;;
    adversary)   echo "actively TRY TO BREAK this. Assume hostile users and worst-case inputs/sequencing. What is the single worst thing that can go wrong, and the exact input that triggers it?" ;;
    ip)          echo "intellectual-property exposure: copyleft (GPL/AGPL) or unknown-license code or dependencies entering the tree, verbatim regurgitation of copyrighted source, and permissively-licensed code copied without its required attribution/notice." ;;
    *)           echo "general code quality and correctness." ;;
  esac
}

build_review_prompt() {  # $1=lens  $2=diff-chunk-file
  local handoff_section=""
  if [[ -n "$HANDOFF_CONTEXT" ]]; then
    handoff_section="$(cat <<HANDOFF

## Structural Panel Context
The following is structural risk signal from the oversight system — composite scores,
high-risk areas, and spec sections to verify. It contains NO internal reviewer findings.
Use it to direct your attention. Your job is to find what the structural signals suggest
may be risky — independently, without anchoring to any prior review conclusions.

${HANDOFF_CONTEXT}
HANDOFF
)"
  fi

  cat <<EOF
You are an INDEPENDENT, cross-vendor code reviewer on a multi-agent oversight panel.
The author was Claude Opus; you are the independent check — do not assume the author is correct.
Risk level of this change: $RISK.   Your review LENS: $1.
Report ONLY issues that fall under your lens: $(lens_brief "$1")
${handoff_section}

Severity tiers: tier1=must-fix (blocks merge), tier2=should-fix (pre-release),
tier3=consider (tech debt), tier4=noted (minor). Be precise about file + line.
Do NOT invent issues. If you find none under your lens, return an empty findings array.

Return ONLY JSON of this exact shape (no prose outside the JSON):
{"findings":[{"file":"path","line":<int>,"end_line":<int>,"severity":"tier1|tier2|tier3|tier4","title":"short","detail":"why it's wrong","suggestion":"concrete fix"}]}

PR diff to review:
$(head -c "$CAP" "$2")
EOF
}

# ── REVIEWERS — fan-out, parse best-effort JSON, tag each finding ───────────────
ALL_FINDINGS="[]"
for spec in "${ROSTER[@]}"; do
  tool="${spec%%:*}"; lens="${spec##*:}"
  if [[ "$tool" != "ipcheck" ]] && ! command -v "$tool" >/dev/null 2>&1; then warn "skip $spec — $tool not on PATH"; continue; fi
  info "reviewing: ${BOLD}$tool${RESET} · lens=$lens"
  tool_findings="[]"
  ci=0
  for chunk in "${CHUNKS[@]}"; do
    ci=$((ci+1))
    raw="$(call_model "$tool" "$(build_review_prompt "$lens" "$chunk")")" || raw='{"findings":[]}'
    printf '%s' "$raw" > "$RUN_DIR/${tool}-${lens}-chunk${ci}.raw.txt"
    f="$(printf '%s' "$raw" | extract_json | jq -c '.findings // []' 2>/dev/null || echo '[]')"
    tool_findings="$(jq -cn --argjson a "$tool_findings" --argjson b "$f" '$a + $b' 2>/dev/null || echo "$tool_findings")"
  done
  n=$(printf '%s' "$tool_findings" | jq 'length' 2>/dev/null || echo 0)
  ok "$tool/$lens → $n raw finding(s)"
  tool_findings="$(printf '%s' "$tool_findings" | jq -c --arg t "$tool" --arg l "$lens" 'map(. + {reviewer:$t, lens:$l})' 2>/dev/null || echo '[]')"
  ALL_FINDINGS="$(jq -cn --argjson a "$ALL_FINDINGS" --argjson b "$tool_findings" '$a + $b')"
done
printf '%s' "$ALL_FINDINGS" > "$RUN_DIR/findings.raw.json"
RAW_COUNT=$(printf '%s' "$ALL_FINDINGS" | jq 'length')

# ── ARBITER — Sonnet synthesizes + dedups into one verdict (NOT the independent check) ─
if command -v claude >/dev/null 2>&1 && (( RAW_COUNT > 0 )); then
  info "arbiter: Sonnet synthesizing $RAW_COUNT raw finding(s)…"
  ARB_PROMPT="You are the ARBITER (Claude Sonnet) in a code-oversight panel. You synthesize the
independent reviewers' findings into ONE verdict — you are NOT yourself the independent check, so
do not add new findings the reviewers didn't raise. Risk level: $RISK.

Tasks: (1) DEDUPE findings that describe the same underlying issue (same file/line/cause), keeping
the highest severity and merging detail. (2) Drop anything clearly spurious or out of its lens.
(3) Write a short markdown SUMMARY for the human (verdict + the headline risks).

Reviewer findings (JSON):
$ALL_FINDINGS

Return ONLY JSON of this exact shape:
{\"summary\":\"markdown overview for the human\",
 \"findings\":[{\"file\":\"path\",\"line\":<int>,\"end_line\":<int>,\"severity\":\"tier1|tier2|tier3|tier4\",\"lens\":\"...\",\"reviewer\":\"...\",\"title\":\"short\",\"detail\":\"why\",\"suggestion\":\"fix\"}]}"
  ARB_RAW="$(call_model sonnet "$ARB_PROMPT")"
  printf '%s' "$ARB_RAW" > "$RUN_DIR/arbiter.raw.txt"
  ARB_JSON="$(printf '%s' "$ARB_RAW" | extract_json)"
else
  # No reviewer findings (or no claude): nothing to arbitrate.
  ARB_JSON="$(jq -cn --arg s "Panel found no issues under the active lenses at risk $RISK." '{summary:$s, findings:[]}')"
fi
printf '%s' "$ARB_JSON" > "$RUN_DIR/arbiter.json"
SUMMARY="$(printf '%s' "$ARB_JSON" | jq -r '.summary // "(no summary)"')"
FINDINGS="$(printf '%s' "$ARB_JSON" | jq -c '.findings // []')"
FCOUNT=$(printf '%s' "$FINDINGS" | jq 'length')
ok "arbiter: $FCOUNT finding(s) after dedup (from $RAW_COUNT raw)"

# ── POST — one line-level thread per finding, then one summary comment ──────────
# Line threads (review comments on the diff) are what the resolution gate enforces;
# the summary is a plain issue comment for the human's overview.
post_thread() {  # $1=path $2=line $3=body  → 0 on success
  gh api "repos/{owner}/{repo}/pulls/$PR/comments" \
    -f body="$3" -f commit_id="$HEAD_SHA" -f path="$1" -F line="$2" -f side=RIGHT \
    >/dev/null 2>>"$RUN_DIR/errors.log"
}

UNANCHORED=""   # findings we couldn't pin to a diff line → folded into the summary
POSTED=0
if (( FCOUNT > 0 )); then
  while IFS= read -r row; do
    file=$(jq -r '.file // ""' <<<"$row")
    line=$(jq -r '.line // 0'  <<<"$row")
    sev=$(jq -r '.severity // "tier3"' <<<"$row")
    lens=$(jq -r '.lens // "?"' <<<"$row")
    rvw=$(jq -r '.reviewer // "panel"' <<<"$row")
    title=$(jq -r '.title // ""' <<<"$row")
    detail=$(jq -r '.detail // ""' <<<"$row")
    sugg=$(jq -r '.suggestion // ""' <<<"$row")
    body=$(printf '**🔭 Oversight panel — %s / %s** (via %s)\n\n**%s**\n\n%s\n\n%s%s' \
      "$sev" "$lens" "$rvw" "$title" "$detail" \
      "${sugg:+_Suggested fix:_ }" "$sugg")
    if (( DRY_RUN )); then
      echo -e "  ${CYAN}[dry-run] thread${RESET} $file:$line  [$sev/$lens] $title"
    elif [[ -n "$file" && "$line" -gt 0 ]] && post_thread "$file" "$line" "$body"; then
      POSTED=$((POSTED+1))
    else
      UNANCHORED+="- **$sev/$lens** ($rvw) — \`$file:$line\` — $title — $detail"$'\n'
      [[ $DRY_RUN -eq 0 ]] && warn "couldn't anchor $file:$line to the diff → folding into summary"
    fi
  done < <(printf '%s' "$FINDINGS" | jq -c '.[]')
fi

# Assemble the summary comment.
SUMMARY_BODY=$(cat <<EOF
## 🔭 Oversight panel — verdict

**Risk:** \`$RISK\`  ·  **Reviewers:** ${ROSTER[*]}  ·  **Findings:** $FCOUNT ($POSTED posted as threads)

$SUMMARY
EOF
)
if (( SAMPLED )); then
  SUMMARY_BODY+=$'\n\n> 🎲 **Selected for random red-team audit** — this `'"$RISK"$'` PR was sampled for an adversarial pass (SQC; rate '"$RATE"$'%, roll '"$ROLL"$'). Lower-tier PRs are spot-checked at random to estimate the escaped-defect rate and to deter risk under-declaration. Selection is reproducible from the head SHA + the secret audit salt.'
fi
if [[ -n "$UNANCHORED" ]]; then
  SUMMARY_BODY+=$'\n\n### Findings that could not be anchored to a diff line\n'"$UNANCHORED"
fi
if [[ "$RISK" == "CRITICAL" ]]; then
  if ! gh pr view "$PR" --json commits -q '.commits[].messageBody' 2>/dev/null | grep -qi 'blast.radius'; then
    SUMMARY_BODY+=$'\n\n> ⚠️ **CRITICAL** change with no blast-radius note found in commit trailers — a blast-radius assessment is required (AGENTS.md §5) before merge.'
  fi
fi
if (( IP_IN_ROSTER )) && (( IP_STUB )); then
  SUMMARY_BODY+=$'\n\n> ⚖️ **IP/provenance was checked by a placeholder agent** — `ipcheck` ran but performs no real analysis yet (D19), so a clean result here is **not** an IP clearance.'
fi
SUMMARY_BODY+=$'\n\n<sub>Posted by `run_panel.sh` — independent cross-vendor review. Threads must be resolved before merge (branch policy D12). Author: respond on each thread.</sub>'

if (( DRY_RUN )); then
  echo ""
  echo -e "${BOLD}[dry-run] summary comment that WOULD be posted:${RESET}"
  echo "$SUMMARY_BODY"
  echo ""
  warn "dry-run: nothing posted to PR #$PR. Raw outputs in $RUN_DIR"
else
  printf '%s' "$SUMMARY_BODY" | gh pr comment "$PR" --body-file - >/dev/null \
    && ok "posted summary comment to PR #$PR" \
    || err "failed to post summary comment (see $RUN_DIR/errors.log)"
  ok "posted $POSTED line-level thread(s)"
fi

echo ""
echo -e "${GREEN}${BOLD}Panel complete.${RESET}  PR #$PR · risk $RISK · $FCOUNT finding(s) · raw archive: $RUN_DIR"
echo ""
