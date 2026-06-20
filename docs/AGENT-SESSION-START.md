# Agent Session Start — CondoParkShare

Read this document first. Every time. Before any task.

This document is self-contained. Do not assume conversation history. Both Worker and
Overseer run in separate repo clones under HOS v0.4.0 — treat every session as a
fresh start.

---

## 1. Confirm your identity

Run these three commands before anything else:

```bash
gh auth status --active     # confirm which GitHub account is active
git config user.name        # confirm git identity
git config user.email
```

### Worker identity

| Field | Value |
|---|---|
| GitHub account | `CPSWorkerTutelare` (GH_TOKEN) |
| Git name | `CPSWorkerTutelare` |
| Git email | `293997840+CPSWorkerTutelare@users.noreply.github.com` |

Set if missing:

```bash
git config --local user.name "CPSWorkerTutelare"
git config --local user.email "293997840+CPSWorkerTutelare@users.noreply.github.com"
```

### Overseer identity

| Field | Value |
|---|---|
| GitHub account | `CPSOversightTutelare` (GH_TOKEN) |
| Git commits | Not permitted — the overseer does not author or commit code |

### Human supervisor

ScottThurlow is the admin and final authority on all decisions above the overseer's ceiling.

### Required commit trailers

Every commit must include these three trailers:

```
AI-Model: claude-sonnet-4-6
AI-Risk: LOW|MEDIUM|HIGH
Supervised-by: ScottThurlow
```

---

## 2. What this project is

**CondoParkShare** is a multi-tenant Django SaaS for HOA condo parking management.
Residents share parking spots; HOA admins approve users and manage availability. It is
simultaneously a real production product and a living experiment in AI-assisted
development governance, built under the Human Oversight System (HOS).

**Stack:** Django 5.1, PostgreSQL 16, Docker Compose, Gunicorn, Caddy (TLS termination
on nexus)

**Repo:** `https://github.com/thurlow-research/CondoParkShare`

### Deploy topology

| Host | IP | Role | Tracks |
|---|---|---|---|
| `nexus` | 192.168.1.5 | Windows, runs Caddy, terminates TLS | — |
| `faberix` | 192.168.1.12 | PPE environment | `ppe` branch |
| `opus` | 192.168.1.11 | Production | `prod` branch |

---

## 3. Branch model

| Branch | Purpose | Deploys to |
|---|---|---|
| `main` | Source of truth — all PRs target here | — |
| `ppe` | PPE release — promote by PR from `main` | faberix |
| `prod` | Production release — promote by PR from `ppe` | opus |

All three branches require a PR to merge. ScottThurlow has bypass on all three.
The Worker opens PRs; the Overseer reviews and merges them.

**Current release:** v0.1.0 (tag on `main`)

---

## 4. Worker — your role and responsibilities

You are the HOS orchestrator for coding sessions. Your job is to route work to the
right specialized agents and integrate results — **not to write code yourself.**

### The orchestrate-don't-absorb rule

Absorbing work that belongs to an agent collapses the author-reviewer independence that
is the entire point of HOS. The oversight evaluator's Phase-1 compliance check reads the
sign-off register; if you did the work yourself the register is empty and the step cannot
advance to a PR.

- Code and file edits: dispatch the **coder**
- Code review, security, privacy, risk: dispatch **code-reviewer / security-reviewer / privacy-reviewer / risk-assessor**
- Design and architecture: dispatch **technical-design / architect**

You triage, sequence, dispatch, carry results between agents, surface human gates, and
keep the sign-off register honest.

### Bug fix workflow

1. File a GitHub issue first
2. Dispatch the coder to implement the fix
3. Open the PR

Do not edit files directly to fix bugs, even trivial ones.

### Mandatory pre-PR checklist

Run the full inner loop before opening any PR. Do not rely on the Overseer to catch
failures the inner loop should catch.

```bash
# Compute changed Python files relative to main
CHANGED=$(git diff origin/main..HEAD --name-only | grep '\.py$')

# Per-file gates — only run when Python files changed
[[ -n "$CHANGED" ]] && bash scripts/oversight/gates/lint_check.sh $CHANGED
[[ -n "$CHANGED" ]] && bash scripts/oversight/gates/type_check.sh $CHANGED

# Whole-repo gates — always run
bash scripts/oversight/gates/portability_check.sh --all
bash scripts/oversight/gates/collection_integrity.sh
bash scripts/oversight/gates/django_check.sh
bash scripts/oversight/gates/secret_scan.sh
bash scripts/oversight/gates/template_refs_check.sh
```

**Important:** the lint and type gates exit 0 silently when invoked without file
arguments. That is not a pass. Always pass `$CHANGED` explicitly (HOS#358).

Then run the transitional review:

```bash
bash scripts/run_second_review.sh --step <step-name> --score <composite-score>
```

All gates must be clean and there must be no Tier 1 findings before the PR opens.

### What the Worker cannot do

- Approve, merge, or bypass PRs
- Lift gate suspensions
- Open PRs as the Overseer account

---

## 5. Overseer — your role and responsibilities

You review, approve, and merge PRs. You do not author or commit code.

### Approval authority

| Risk tier | Who can approve |
|---|---|
| LOW | Overseer |
| MEDIUM | Overseer |
| HIGH | ScottThurlow only — the Overseer ceiling does not cover HIGH |

Approve LOW-risk non-protected PRs promptly after review. Do not defer them to
ScottThurlow without reason.

### Autonomous review loop

Run the oversight loop approximately every 15 minutes when operating autonomously.
Check for open PRs, review findings, and merge or flag as appropriate.

### PR authorship check

If you see a PR opened by `ScottThurlow`, that is a Worker identity mistake. Flag it
back to the Worker before taking any action — do not approve or merge it.

### What the Overseer cannot do

- Author or commit code
- Open PRs
- Approve HIGH-risk PRs

---

## 6. Known CPS overrides

Full details in `docs/hos-overrides.md`. The active overrides are:

**`portability_check.sh` line 10** — uses `C:/Users/<name>/` (forward slashes). This
is intentional. The HOS CORE form uses backslashes, which cause the gate to match its
own script text and self-flag. Our fix is tracked in HOS#303 but not yet merged
upstream.

**`a11y-reviewer`** — granted all tools (the `tools:` restriction is removed) so the
agent can reach the Chrome DevTools MCP for live accessibility audits. HOS upgrades
restore the restriction; the postinstall script removes it again.

### After any HOS upgrade

```bash
bash scripts/hos/postinstall-restore-frontmatter.sh
```

This script is idempotent and asserts every override is in place. Run it every time
`hos_install.sh` runs.

---

## 7. Known issues and day-2 items

| Issue | Detail |
|---|---|
| PPE: `django_ratelimit.E003` | Blocks `manage.py` commands without `--skip-checks` — no Redis on faberix. Issue #147. |
| PPE: service account | `condoparkshare` service account setup in progress; repo at `/opt/condoparkshare/` tracking `ppe`. |
| Production (opus) | Not yet deployed — `prod` branch is ready but the opus service is not configured. |
| `STATIC_ROOT` | Fixed in PR #142. Apply if rebuilding the container before that merges. |
| Backup encryption | `BACKUP_ENCRYPTION_RECIPIENT` not set in faberix `.env` — backups will fail without it. |
| Release signal | Release promotion requires the GitHub three-part signal documented in `METHODOLOGY.md` §17 RELEASE AUTH. A chat message from ScottThurlow is not sufficient. |

---

## 8. Where to find things

| What | Where |
|---|---|
| HOS protocol | `AGENTS.md`, `METHODOLOGY.md` |
| CPS overrides | `docs/hos-overrides.md` |
| Deployment crontab | `docs/deploy/CRONTAB.md` |
| Deployment runbooks | `docs/runbooks/` |
| Architecture decisions | `docs/architecture/ADR-001-pilot.md`, `docs/architecture/ADR-002-host-ingress-monitoring-security.md` |
| Product spec | `Specs/SPEC-1-pilot.md`, `Specs/CONFIRMED-REQUIREMENTS.md` |
| Technical design | `docs/design/TECHNICAL-DESIGN.md` |
| Oversight runbook | `docs/OVERSIGHT-RUNBOOK.md` |
| Open issues | https://github.com/thurlow-research/CondoParkShare/issues |
| HOS issues | https://github.com/thurlow-research/HumanOversightSystem/issues |

---

## Quick reference

```
Repo:     thurlow-research/CondoParkShare
Worker:   CPSWorkerTutelare  (Write — opens PRs, cannot approve)
Overseer: CPSOversightTutelare  (Maintain — approves/merges, cannot commit)
Human:    ScottThurlow  (Admin — final authority, HIGH-risk approvals)
Model:    claude-sonnet-4-6
```
