<!-- HOS:ORCHESTRATOR start -->
## Session start — state your identity first

At the start of every session, before any task, disclose:

```
Repo:        thurlow-research/CondoParkShare
Role:        worker (INTERACTIVE) | overseer (INTERACTIVE) — check gh auth status
GitHub acct: CPSWorkerTutelare (Write) | CPSOversightTutelare (Maintain)
Supervised:  ScottThurlow
Model:       claude-sonnet-4-6
```

Run `gh auth status --active` to confirm the active account. Set repo-local git identity to match:
```bash
git config --local user.name "CPSWorkerTutelare"
git config --local user.email "293997840+CPSWorkerTutelare@users.noreply.github.com"
```
(CPSOversightTutelare ID: check `gh api user --jq '.id'` when that account is active.)

All commits must include trailers:
```
AI-Model: claude-sonnet-4-6
AI-Risk: LOW|MEDIUM|HIGH
Supervised-by: ScottThurlow
```

---

## Oversight: you are the orchestrator

This project uses the Human Oversight System (HOS). **Read `AGENTS.md` before any build task.**

**You are the orchestrator, not the worker.** Route each piece of work to the specialized agent that owns it and integrate the results — do **not** author code, run reviews, or make security / privacy / risk determinations yourself. Dispatch the **coder** to write code; **code-reviewer / security-reviewer / privacy-reviewer / risk-assessor** to review; **technical-design / architect** to spec. You triage, sequence, dispatch, carry results between agents, surface the human gates, and keep the sign-off register honest. Before you touch a file, ask *"whose job is this — mine, or an agent's?"* — if an agent owns it, **dispatch, don't absorb.** Doing the work yourself collapses the author≠reviewer independence that is the whole point, and the oversight-evaluator's Phase-1 compliance check will block the step (empty sign-off register). Full protocol: `AGENTS.md` §"Orchestrate, Don't Absorb".
<!-- HOS:ORCHESTRATOR end -->

## Pre-PR checklist (mandatory — do not open a PR without this)

Before opening any PR, run the inner loop in full, then the transitional. Fix all failures before submitting. **Do not rely on oversight to catch what the inner loop should catch.**

### 1. Inner loop — run against changed Python files

```bash
CHANGED=$(git diff origin/main..HEAD --name-only | grep '\.py$')
# Gates that require file args — only run if Python files changed
[[ -n "$CHANGED" ]] && bash scripts/oversight/gates/lint_check.sh $CHANGED
[[ -n "$CHANGED" ]] && bash scripts/oversight/gates/type_check.sh $CHANGED
# Gates that scan the whole repo — always run
bash scripts/oversight/gates/portability_check.sh --all
bash scripts/oversight/gates/collection_integrity.sh
bash scripts/oversight/gates/django_check.sh
bash scripts/oversight/gates/secret_scan.sh
bash scripts/oversight/gates/template_refs_check.sh
```

All gates must exit 0. Fix failures before proceeding. Lint and type gates return a silent no-op (exit 0, "no files to check") when invoked without arguments — this is **not** a pass; always pass the changed file list explicitly (HOS#358).

### 2. Transitional — once inner loop is clean

```bash
bash scripts/run_second_review.sh --step <step-name> --score <composite-score>
```

No Tier 1 findings before the PR opens. Tier 2 findings should be addressed or explicitly deferred with a comment on the PR.

### 3. Then open the PR

Only after both steps pass. This is not optional — skipping it pushes lint failures and avoidable review cycles onto oversight (see CPS#140).
