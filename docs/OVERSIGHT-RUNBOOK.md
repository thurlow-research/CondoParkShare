# CondoParkShare — Oversight Pipeline Runbook

Complete guide to running the Human Oversight System for each build step.
Reference `contract/step-manifest.yaml` for per-step risk tiers and required sign-offs.

---

## Prerequisites

```bash
# Verify CLIs are authenticated
agy -p "hello"              # should respond
codex exec "hello"          # should respond
gh auth status              # should show authenticated
scancode --version          # should show version (Level 1 IP detection)

# Verify validators are ready
cd ~/CondoParkShare
bash scripts/oversight/run_validators.sh parking/admin.py 2>/dev/null \
  && cat .claudetmp/oversight/validators/summary.json | python3 -m json.tool | head -5
```

---

## Project Start Sequence

Run this once before the first build step begins. These agents produce the documents that every subsequent agent reads.

```
1. pm-agent      → docs/pm/CONFIRMED-REQUIREMENTS.md
2. ux-designer   → docs/design/UX-DESIGN-READINESS.md
3. architect     → docs/architecture/ADR-001-pilot.md
4. technical-design (iterated with architect) → docs/design/TECHNICAL-DESIGN.md
```

**Invoke in order:**

```bash
# 1. PM agent — spec review and human Q&A
# "Invoke pm-agent for initial spec review"
# Wait for pm-agent to complete its Q&A with you and write CONFIRMED-REQUIREMENTS.md

# 2. UX designer — design pack audit against full spec
# "Invoke ux-designer for initial design audit"
# ux-designer reads the spec + confirmed requirements, fills all design pack gaps,
# and writes UX-DESIGN-READINESS.md.
# Answer any structural brand questions it surfaces before proceeding.

# 3. Architect — technical architecture
# "Invoke architect for initial architecture review"
# architect reads confirmed requirements + UX-DESIGN-READINESS.md,
# asks any technical questions, and writes ADR-001-pilot.md

# 4. Technical design — detailed spec
# "Invoke technical-design to produce the technical design"
# technical-design reads all three documents above, iterates with architect,
# and writes TECHNICAL-DESIGN.md
```

**Gate:** Do not start build step 1 until TECHNICAL-DESIGN.md is architect-approved.

---

## Per-Step Pipeline

Run this sequence for each build step (N = step number, e.g. 3).

---

### PHASE 0 — Spec Red-Team (before coding starts)

Invoke the `spec-red-team` Claude agent on the relevant spec section before any code is written. It uses agy adversarially to find gaming vectors, contradictions, and implicit assumptions.

```bash
# Open Claude Code in CondoParkShare and invoke the agent:
# "Use spec-red-team to review the spec section for step N"
# The agent will call agy and create spec-gap GitHub issues for any findings.
# pm-agent must resolve open spec-gap issues before coder starts.
```

**Output:** `spec-gap` GitHub issues (if findings). Coder does not start until pm-agent confirms spec is clear.

---

### PHASE 1 — Gate Scripts (blocking, before review chain)

Run before coding is submitted for review. These fail fast and cheap.

```bash
cd ~/CondoParkShare

# Lint + formatting
bash scripts/oversight/gates/lint_check.sh [changed files...]
# or: bash scripts/oversight/gates/lint_check.sh --all

# Type checking
bash scripts/oversight/gates/type_check.sh [changed files...]

# Secret detection
bash scripts/oversight/gates/secret_scan.sh [changed files...]
# or with staged files: bash scripts/oversight/gates/secret_scan.sh --staged

# Security scan (HIGH severity = blocking)
bash scripts/oversight/gates/security_scan.sh [changed files...]
```

All must exit 0 before proceeding. Fix any failures before the review chain.

---

### PHASE 2 — Risk Assessment

Run after gates pass. Scores the code across 9 dimensions and produces an inspection brief for reviewers.

```bash
# Collect changed files (adjust pattern as needed)
CHANGED=$(git diff --name-only HEAD~1 -- '*.py' | tr '\n' ' ')

# Run all validators (includes ip_check, prompt_audit_risk, rn_calculator, etc.)
bash scripts/oversight/run_validators.sh $CHANGED

# Read results
cat .claudetmp/oversight/validators/summary.json | python3 -m json.tool
```

**Key outputs:**
- `composite_score` — 0.0–1.0
- `tier` — LOW / MEDIUM / HIGH / CRITICAL
- `successful_validators` — if 0, pipeline halted (fail-closed)
- Per-dimension scores in `.claudetmp/oversight/validators/*.json`

**IP check results** (from `ip_check.json`):
```bash
cat .claudetmp/oversight/validators/ip_check.json | python3 -m json.tool
# Level 1: dependency license findings (copyleft/unknown = HIGH)
# Level 2: prompt artifact clean-room analysis
# Level 3: regurgitation stub (not yet active)
```

**Prompt ambiguity results** (from `prompt_ambiguity.json`):
```bash
cat .claudetmp/oversight/validators/prompt_ambiguity.json | python3 -m json.tool
# High ambiguity score → spec was unclear → reviewer attention directed here
```

Then invoke the `risk-assessor` Claude agent:
```
"Run risk-assessor for step N on [changed files]. Score is [composite] (tier: [TIER])."
```

The agent reads all validator output, validates the risk tier, and writes:
- `.claudetmp/oversight/validators/risk-assessment.md` — validated tier + inspection brief
- At MEDIUM+: invokes `prompt-fidelity` subagent against prompt artifacts
- At HIGH+: invokes `dep-mapper` and `risk-historian` subagents

---

### PHASE 3 — Internal Review Chain

Run the review agents in order, directed by the inspection brief from risk-assessor.

**Always required (every step):**
```
Invoke: code-reviewer
Input: inspection brief from risk-assessment.md
Output: sign-off register entry → .claudetmp/signoffs/stepN-register.md
```

**Required by step (check step-manifest.yaml):**

```bash
# See which reviewers are required for step N:
python3 -c "
import yaml
m = yaml.safe_load(open('contract/step-manifest.yaml'))
step = next(s for s in m['steps'] if s['id'] == N)
print('Required:', step['required_signoffs'])
print('System test:', step.get('system_test_applicable'))
print('Human gate:', step.get('human_gate_required'))
"
```

Run parallel reviewers (security + privacy run simultaneously after code-reviewer approves):
```
Invoke: security-reviewer  ← always for MEDIUM+
Invoke: privacy-reviewer   ← steps with PII: 3, 6, 9, 10, 11
Invoke: ui-reviewer        ← step 10 (templates)
Invoke: a11y-reviewer      ← step 10 (templates)
Invoke: infra-reviewer     ← steps 1, 11 (infrastructure)
```

> **Note — `ux-designer` has two modes:**
> - **Project start (proactive):** invoked after `pm-agent` completes Q&A, before `architect`. Audits the design pack against the full spec, fills all gaps, and writes `docs/design/UX-DESIGN-READINESS.md`. The architect and technical-design agent read this document before starting their own work.
> - **During the build (reactive):** when `ui-reviewer` or `a11y-reviewer` finds a design pack gap (missing token, undocumented component, contrast failure), they invoke `ux-designer` rather than escalating to human. `ux-designer` extends the design pack and notifies both reviewers. This happens within the existing review iteration — it does not add a new pipeline phase.

**Unit tests (iterate with coder until 80%/75%):**
```
Invoke: unit-test
Target: 80% coverage, 75% mutant score
Issues: creates test-resistance GitHub issue on 5-round loop exhaustion
```

**Sign-off register** — each agent writes one entry. Format:
```
## security | parking/views.py | 2026-06-11T15:10Z
Status: APPROVED
Agent: security-reviewer
Artifact: parking/views.py, parking/models.py
Iterations: 2
Critical_findings_resolved: false
Notes: Clean review. select_for_update() present. No cross-tenant leaks.
```

---

### PHASE 4 — System Tests (if applicable for this step)

```bash
# Check if system tests apply for this step:
# system_test_applicable: true in step-manifest.yaml

# Run system tests
cd ~/CondoParkShare
python manage.py test tests/system/ --keepdb

# pm-agent must have signed off on the test plan before tests run.
# Invoke: system-test agent (runs tests + writes register entry)
```

Persistent failures after 5 rounds → `bug` GitHub issue filed → escalate to architect.

---

### PHASE 5 — Prompt Artifact Capture (MEDIUM+ steps)

For steps where risk tier is MEDIUM or above, capture the prompt artifact before committing.

```bash
# Capture prompt artifact (mirrors src/ structure into prompts/)
bash scripts/capture_prompt.sh parking/views.py "Booking gate logic for step 6"

# Add Prompt-Artifact trailer to commit message:
git commit -m "Add booking gate view

Implements three-gate booking creation: horizon check, one-active check,
and GiST exclusion constraint for overlap safety.

Prompt-Artifact: prompts/parking/views.md
AI-Model: claude-sonnet-4-6
AI-Risk: CRITICAL"
```

---

### PHASE 6 — Second Review (pre-PR, cross-vendor)

Run after all internal reviewers have approved and system tests pass.

```bash
# Get composite score from validators (or re-run if stale)
SCORE=$(cat .claudetmp/oversight/validators/summary.json | \
  python3 -c "import json,sys; print(json.load(sys.stdin)['composite_score'])")

echo "Score: $SCORE"

# Run second review
bash scripts/run_second_review.sh \
  --step N \
  --score $SCORE \
  --diff HEAD~1

# agy fires at score ≥ 0.30 (MEDIUM+)
# codex fires at score ≥ 0.55 (HIGH+)
# Output: .claudetmp/second-review/stepN-{timestamp}.md
# Top-level fields: verdict, highest_severity, unresolved_findings
```

Check the verdict:
```bash
head -8 .claudetmp/second-review/stepN-*.md | tail -5
# verdict: approve | request_changes | error | skipped
```

If `request_changes`: address findings, re-run. If `verdict: error`: check CLI auth.

**Token usage after second review:**
```bash
python3 scripts/oversight/token_tracker.py report
# Shows agy + codex usage vs. subscription quotas
```

---

### PHASE 7 — Oversight Evaluation

Invoke the `oversight-evaluator` agent. It runs Phase 1 (compliance) then Phase 2 (quality).

```
Invoke: oversight-evaluator
Input: contract/step-manifest.yaml, .claudetmp/signoffs/stepN-register.md,
       .claudetmp/second-review/stepN-*.md,
       .claudetmp/oversight/validators/risk-assessment.md
```

**For CRITICAL steps (3 and 6) — human authorization required FIRST:**
```bash
# Before running oversight-evaluator on steps 3 or 6:
mkdir -p .claudetmp/oversight
cat > .claudetmp/oversight/stepN-human-authorization.md << EOF
Authorized: $(date -I)
Decision: [your explicit decision to proceed]
Reviewed: [what you personally reviewed]
Authorized by: Scott Thurlow
EOF
```

**Evaluator outputs:**
- `PROCEED` → orchestrator opens PR
- `CONDITIONAL_PROCEED` → PR opens with "Human Review Required Before Merge" section
- `ESCALATE` → PR does NOT open; specific question surfaced to human

For ESCALATE, read the escalation items, address them, then re-run.

---

### PHASE 8 — Orchestrator (open PR)

Invoke the `oversight-orchestrator` agent after evaluator recommends PROCEED or CONDITIONAL.

```
Invoke: oversight-orchestrator
Input: .claudetmp/oversight/stepN-evaluation-{ts}.md
```

The orchestrator:
1. Writes `panel-context.md` — structural risk signals only (no internal findings)
2. Writes `handoff.md` — full picture for human/PR body
3. Opens the PR: `gh pr create --title "Step N: [name]" --body "$(cat .claudetmp/oversight/stepN-handoff.md)"`
4. Prints the panel command

---

### PHASE 9 — Cross-Vendor Panel (post-PR)

Run after the PR is open. This is the outer loop — adversarial, independent.

```bash
# Get the PR number from the orchestrator output or:
PR=$(gh pr view --json number -q .number)

# Run the full panel
bash scripts/run_panel.sh $PR

# Panel uses: agy (correctness), codex (security + adversary), IP agent, Copilot
# Reads: .claudetmp/oversight/stepN-panel-context.md (structural signals only)
# Posts: one PR thread per finding
# Creates: escaped-defect GitHub issues for new findings
```

**Monitor panel output:**
```bash
# Panel logs to .ai-local/panel/pr{N}-{date}/
ls .ai-local/panel/
```

**Token usage after panel:**
```bash
python3 scripts/oversight/token_tracker.py report
```

---

### PHASE 10 — Human Gate

Review and resolve all PR threads before merging.

```bash
# List open threads
gh pr view $PR --json reviewThreads

# Each finding must be explicitly addressed:
# - Fix the code and push → thread resolves on re-review
# - Respond with explanation if you disagree → thread resolves when reviewer agrees
# Merge is blocked until ALL threads are resolved (branch protection setting)

# Merge when clean
gh pr merge $PR --squash
```

---

### PHASE 11 — Audit Log Entry

After merge, append the step summary to the audit trail.

```bash
cd ~/CondoParkShare

# Quick entry (full entry generated by orchestrator at PR open time)
python3 -c "
import json
from datetime import datetime, timezone
entry = {
    'ts': datetime.now(timezone.utc).isoformat(),
    'event': 'merge',
    'step': N,
    'pr': $PR,
    'commit': '$(git rev-parse HEAD)',
    'tier': 'HIGH',  # replace with actual tier from summary.json
}
with open('audit/oversight-log.jsonl', 'a') as f:
    f.write(json.dumps(entry) + '\n')
print('Audit entry written')
"

git add audit/
git commit -m "Step N: audit log entry — merge PR $PR"
git push origin build
```

---

## Checkpoint Red-Teams

Run at steps 3, 6, 10, 11 (after those steps are merged):

```bash
# After step 3 — auth system
bash scripts/run_red_team.sh --milestone auth

# After step 6 — booking gates
bash scripts/run_red_team.sh --milestone booking

# After step 10 — admin portals
bash scripts/run_red_team.sh --milestone admin

# After step 11 — live deployment
bash scripts/run_red_team.sh --milestone deploy
```

Each red-team:
- Reads the full codebase (not just a diff)
- Uses codex (adversarial attack chains) + agy (spec vs. implementation gap)
- Requires "not exploitable" attestations — a clean finding list without them is invalid
- Creates `red-team-finding` GitHub issues for critical/high findings
- Output: `.claudetmp/red-team/checkpoint-{milestone}-{ts}.md`

---

## IP Check Details

IP checking runs automatically as part of `run_validators.sh`. But you can run it standalone:

```bash
# Check changed dependency files + prompt artifacts
python3 scripts/oversight/validators/ip_check.py \
  --prompts-dir prompts/ \
  requirements.txt parking/views.py

# Level 1: License check (ScanCode Toolkit — full text comparison)
# Level 2: Prompt artifact clean-room analysis
# Level 3: Regurgitation stub — awaiting AboutCode API access
```

**If Level 1 finds copyleft/unknown licenses:**
```bash
cat .claudetmp/oversight/validators/ip_check.json | \
  python3 -c "
import json, sys
d = json.load(sys.stdin)
for f in d.get('findings', []):
    if f.get('severity') in ('high', 'medium'):
        print(f['message'])
"
```

Address copyleft findings before the PR opens. Unknown licenses → legal review before shipping.

---

## Useful Queries

```bash
# Token burn today
python3 scripts/oversight/token_tracker.py report

# Token burn all-time
python3 scripts/oversight/token_tracker.py report --all

# All escaped defects
jq 'select(.event=="panel-run") | {step, escaped_defects}' \
  audit/oversight-log.jsonl

# All security findings filed
gh issue list --label security-finding --state all

# Sign-off register for step N
cat .claudetmp/signoffs/stepN-register.md

# Second review verdict for step N
head -10 .claudetmp/second-review/stepN-*.md

# Risk tier history
jq 'select(.event=="risk-assessment") | {step, tier, score}' \
  audit/oversight-log.jsonl
```

---

## Post-Change Sweep

Run before committing any batch of changes. The sweep agent categorizes your diff and drives all relevant reviews automatically.

```bash
# Step 1 — see routing plan (no AI, instant)
bash scripts/framework/run_post_change_sweep.sh

# Step 2 — invoke the sweep agent in Claude Code
# "Run post-change sweep"
# The agent reads the routing plan and invokes all listed agents in order.
```

**Framework-only changes** (agent files, docs, framework scripts):
```bash
# Fast path — static + AI review of framework files only
bash scripts/framework/run_framework_validation.sh

# Or static-only (no AI, for quick pre-commit check):
bash scripts/framework/run_framework_validation.sh --static-only
```

**What the sweep agent drives, by domain:**

| Domain changed | Agents invoked |
|---|---|
| `.claude/agents/`, `docs/AGENTS.md`, `scripts/framework/` | `framework-validator` |
| `*.py` app code | `code-reviewer` → (parallel) `security-reviewer`, `privacy-reviewer` |
| `templates/*.html` | `ui-reviewer`, `a11y-reviewer` (after `code-reviewer` approves) |
| `docker-compose.yml`, `Caddyfile` | `infra-reviewer` |
| `tests/**` | `unit-test` |
| `Specs/*design*/**` | `ux-designer` → `ui-reviewer` |
| `Specs/*.md` | `pm-agent` |

> **Note on `ux-designer`:** Has two modes. **At project start** it is invoked proactively (after `pm-agent`, before `architect`) to audit the design pack against the full spec and write `docs/design/UX-DESIGN-READINESS.md` — see the Project Start Sequence above. **During the per-step pipeline** it is reactive: when `ui-reviewer` or `a11y-reviewer` finds a design pack gap, they invoke `ux-designer` rather than escalating to human. `ux-designer` extends the design pack and notifies both reviewers within the existing review iteration, without adding a new phase.

---

## Quick Reference — Which Reviewers for Which Steps

| Step | Risk | code | security | privacy | ui/a11y | infra | test-unit | test-system | process | human gate | red-team |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 Scaffold | LOW | ✓ | | | | ✓ | | | | | |
| 2 Multi-tenant | HIGH | ✓ | ✓ | | | | ✓ | | | | |
| 3 Auth | CRITICAL | ✓ | ✓ | ✓ | | | ✓ | ✓ | ✓ | ✓ | auth |
| 4 Data model | HIGH | ✓ | ✓ | | | | ✓ | | | | |
| 5 Availability | HIGH | ✓ | ✓ | | | | ✓ | ✓ | ✓ | | |
| 6 Booking gates | CRITICAL | ✓ | ✓ | ✓ | | | ✓ | ✓ | ✓ | ✓ | booking |
| 7 Horizon | MEDIUM | ✓ | ✓ | | | | ✓ | ✓ | ✓ | | |
| 8 Cancellation | MEDIUM | ✓ | ✓ | | | | ✓ | ✓ | ✓ | | |
| 9 Notifications | MEDIUM | ✓ | ✓ | ✓ | | | ✓ | ✓ | ✓ | | |
| 10 Admin portals | HIGH | ✓ | ✓ | ✓ | ✓ | | ✓ | ✓ | ✓ | | admin |
| 11 Deploy | HIGH | ✓ | ✓ | ✓ | | ✓ | ✓ | ✓ | | | deploy |
