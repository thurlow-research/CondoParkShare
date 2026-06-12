# How This Works — The Oversight Methodology

This document explains the whole system end-to-end: the philosophy, the AIs involved, the agent roles, the risk model, the pipeline steps, and the tooling that ties it together.

It is the conceptual companion to:
- **[`AGENTS.md`](AGENTS.md)** — the protocol a single AI assistant follows (the authoritative behavior spec).
- **[`SETUP.md`](SETUP.md)** — how to install everything on a machine.
- **[`README.md`](README.md)** — project overview.

> **Status legend.** This system is built incrementally. Throughout this doc:
> **✅ Implemented** — exists and works today.
> 🔧 **Designed / planned** — agreed in design, not yet built.
> Don't assume a 🔧 capability is live just because it's described here.

---

## 1. The Goal

The hard problem of "vibe coding" is **scaling human oversight**. AI writes code far faster than humans can carefully review it, and AI-generated code fails differently from human code — plausible-but-wrong logic, hallucinated APIs, security antipatterns that *look* correct. Reviewing everything exhaustively doesn't scale; trusting blindly is reckless.

The thesis here: **route human attention by risk.** Make the oversight signal visible and stratified so a human reviews the 10% that matters line-by-line and spot-checks the rest. The mechanism is a system of **checks and balances across multiple, independent AIs**, escalating to a human as risk rises.

This repo is both the **tool** and the **experiment** — it bootstraps that methodology into any project, and it is itself governed by it.

---

## 2. Two Layers of Oversight

### Layer 1 — Self-flagging (single agent) ✅ Implemented
The authoring AI flags its *own* work, per **[`AGENTS.md`](AGENTS.md)**: every non-trivial change gets a risk classification, human-review flags, a confidence declaration, hallucination warnings, and a blast-radius note for destructive operations. This is "a senior engineer who flags their own work," not a code dispenser.

### Layer 2 — Independent review (multiple agents) 🔧 Built, not yet exercised
Self-flagging has a blind spot: an AI is bad at catching its *own* class of mistakes. So a second layer brings in **independent reviewers from different vendors** to check the author's work, scaled by risk, culminating in a human gate. This is the multi-agent "panel" (§6), implemented in [`scripts/run_panel.sh`](scripts/run_panel.sh) (triage → cross-vendor reviewers → arbiter → posts threads to the PR) — built, awaiting its first run on a live PR.

The two layers compose: the author self-flags (Layer 1), then independent reviewers scrutinize (Layer 2), then the human decides.

---

## 3. The AIs

We deliberately use **multiple vendors and model tiers** — the whole point is *decorrelated* judgment. An AI reviewing its own family tends to ratify its own mistakes.

| AI | Subscription | Driven via | Role |
|---|---|---|---|
| **Claude** (Opus / Sonnet / Haiku) | Claude Max (20×) | `claude` CLI | Author (Opus), triage & cheap review (Haiku), arbiter (Sonnet) |
| **OpenAI** | ChatGPT Pro | `codex` CLI (`codex exec`) | Independent reviewer / adversary (high risk) |
| **Google** | Gemini Pro | `agy` (Antigravity CLI) | Independent cross-vendor reviewer; architecture/whole-repo lens |
| **GitHub Copilot** | Copilot Pro | GitHub-native PR review | **Baseline reviewer on _every_ PR** (automatic, in CI) |

**Key constraints that shape the design:**
- These are **app/CLI subscriptions, not API keys.** To use the quota you pay for, each reviewer runs through its subscription-authenticated CLI — *not* a metered API. (Why the panel runs **locally**, not in CI — see §6.)
- **Opus is the author**, so Opus may never review its own output. At the highest risk, the *independent* votes must be **cross-vendor** (Google/OpenAI); same-vendor Claude tiers can assist but don't count as the independent check.
- **Copilot is the always-on floor, the cross-vendor panel is the escalation.** Copilot runs automatically on **every** PR (GitHub-native, in CI) — including LOW changes the local panel skips — so there is an AI review on everything without spending subscription quota. The cross-vendor panel (`agy`/`codex`) then layers on by risk. (Copilot Pro required; see [`DECISIONS.md` D16](DECISIONS.md) for quota and metering details.)
- **Quota-aware allocation:** spend the abundant Claude Max quota on high-frequency roles (author, triage, arbiter); reserve scarce ChatGPT Pro reasoning for high-risk review and adversarial passes; use Antigravity's large context for breadth.
- **Why no Claude in the reviewer seat:** Opus is the author, so no Claude model casts an independent review — same-vendor judgement correlates errors. Sonnet is the **arbiter** (synthesis), never an independent reviewer; the independent votes are cross-vendor (`agy`/`codex`).

> Google is retiring the Gemini CLI (consumer shutoff **2026-06-18**) in favor of the **Antigravity CLI (`agy`)**, which is what the tooling now installs.

---

## 4. Agent Role Definitions

Roles are defined first; models are then assigned to roles (§3). Think *separation of powers*.

| Role | What it does | Typically |
|---|---|---|
| **Author** | Generates code from a prompt; self-flags per `AGENTS.md`. | Claude Opus |
| **Triage** | Scores the change's risk (see §5). Deterministic rules set a floor; the author cannot lower its own risk. | rules + Claude Haiku |
| **Reviewer(s)** | Independent critique, each with a *lens*: correctness, security, maintainability, spec-conformance. Count scales with risk. | Antigravity, Codex, Copilot |
| **Adversary / red-team** | Actively tries to *break* the change rather than bless it. High/critical risk only. | Codex or Antigravity |
| **IP / provenance** | Checks intellectual-property exposure — copyleft/unknown-license code or deps entering the tree, verbatim regurgitation of copyrighted source, stripped attribution. An axis *orthogonal* to the risk tier; surfaces exposure for human/legal review, does not adjudicate. Runs on every panel pass. | `ipcheck` → `scripts/oversight/validators/ip_check.py`. **Level 1 ✅** (dependency license gate via ScanCode Toolkit, falls back to PyPI/npm API). **Level 2 ✅** (prompt clean-room: reads captured prompt artifacts, flags attribution triggers). **Level 3 🔧** (regurgitation via LSH; planned: ai-gen-code-search/AboutCode service API — see D20). |
| **Arbiter** | Reconciles conflicting reviews into a single verdict + rationale; dedups; loops back to author or escalates. (Synthesizes others' independent reviews — not itself the independent check.) | Claude Sonnet |
| **Human** | Sets policy; the final gate for high/critical changes; spot-checks the rest. | You |

---

## 5. The Risk Model

Risk classification (from [`AGENTS.md`](AGENTS.md)) is the dial that controls how much scrutiny a change gets:

| Level | Criteria | Scrutiny (escalating) |
|---|---|---|
| **LOW** | Pure UI/styling, no logic/data/external calls | Self-flag; deterministic gates; **Copilot baseline review (auto, all PRs)**; **random red-team audit (SQC sample)**. No prompt artifact required. |
| **MEDIUM** | Business logic, data transforms, state, routing | + prompt artifact captured; **local panel runs**: ≥1 **independent cross-vendor** reviewer (`agy`); human reviews flagged items. |
| **HIGH** | Auth, input handling, persistence, external APIs | + a **security lens** + an **adversary/red-team pass** (always-on at HIGH); human reviews line-by-line. |
| **CRITICAL** | XSS/CSRF/injection, PII, payments, destructive ops | same panel roster as HIGH + **blast-radius required** + **mandatory human approval**. |

**Who assigns risk:** a deterministic floor (file-path globs like `auth/**` or `**/migrations/**`, dependency-manifest changes, diff size) the author can *raise* freely but can only *lower* with a second agent's or the human's concurrence. Triage signals also include: blast radius (import-graph reach), computational complexity, reversibility, test coverage of touched lines, and cheap static-analysis findings.

This is the research hypothesis in action: **risk-stratified flagging** routes 100% review to CRITICAL and spot-checks LOW — oversight that scales.

**Random red-team audit (Statistical Quality Control).** The adversary/red-team pass is *always-on at HIGH+*; below that, a *salted-random sample* of LOWER-tier PRs (LOW/MEDIUM) gets one too — so red-team coverage is **guaranteed at HIGH+ and probabilistic below**. This audits the auto-pass lane — catching changes that were mis-triaged as low — and yields an **escaped-defect rate**: an empirical measure of how many defects survive in the population we chose not to scrutinize, which is the signal for whether the tier thresholds are calibrated. Selection is a salted deterministic hash of the PR's head SHA (reproducible and auditable, non-gameable without the secret salt); production rates are LOW 5% / MEDIUM 15% (elevated during the pilot so it visibly fires). See [`DECISIONS.md` D17](DECISIONS.md).

---

## 6. The Pipeline (Steps)

How a change flows from prompt to merge. Deterministic, cheap checks run first (fail fast); expensive AI/human review is spent only where risk warrants.

```
1. PROMPT            Author is prompted. The prompt is a first-class artifact (§7).
2. AUTHOR + SELF-FLAG  Opus generates code, classifies risk, flags review items,
                       states confidence, warns on hallucination surface.          [Layer 1 ✅]
3. CAPTURE           For MEDIUM+: ./scripts/capture_prompt.sh records the prompt
                       artifact in prompts/ (mirrors the source path).             [✅]
4. COMMIT            Commit with provenance trailers: Prompt-Artifact / AI-Model /
                       AI-Risk. Provenance is queryable via prompt_audit.sh.        [✅]
5. PR                Open a PR. main is protected: ≥1 approval + all review threads
                       must be resolved before merge.                               [✅]
6. CHEAP GATES       (CI) lint, types, build, unit tests, secret scan, npm audit.
                       Fail fast before spending review tokens.                      [🔧 per-project]
7. TRIAGE            Determine final risk level (§5).                                [🔧 run_panel.sh]
8. EXPENSIVE GATES   Gated by risk: e2e/system tests, coverage, mutation testing.    [🔧]
9. AI PANEL          Independent reviewers (by risk) run LOCALLY via subscription
                       CLIs, each with a lens; adversary at HIGH/CRITICAL. Findings
                       posted to the PR as line-level threads.                        [🔧 run_panel.sh]
10. ARBITER          Synthesizes reviews → verdict; requests changes or escalates.   [🔧 run_panel.sh]
11. HUMAN GATE       Mandatory at HIGH/CRITICAL; PR conversation-resolution forces
                       each finding to be addressed before merge.                    [✅ gate / 🔧 panel]
12. MERGE → ARCHIVE  Raw review/turn logs archived; summaries regenerable (§7).      [🔧]
```

**Why the panel runs locally, not in CI:** the subscription CLIs authenticate interactively (browser OAuth that lives on your machine); CI runners can't hold that session. So CI handles the deterministic gates + Copilot's native PR review, while the cross-vendor AI panel runs from a local command and **posts its findings to the PR**. The PR stays the auditable record.

---

## 7. Prompts as Source Code

A defining principle: **prompts are treated as source code.**

- The prompt is the **"C source"**; the generated code is the **"compiled artifact"** (object/binary). In principle you should be able to *regenerate* the code from the prompt.
- Unlike a normal build (where you'd gitignore the binary), here **both** the prompts and the generated code are committed and versioned. The compiler analogy is the mental model for *provenance and authority*, not the version-control policy.
- `prompts/` holds the curated, cleaned prompt artifacts (one per MEDIUM+ file, mirroring `src/`). 🔧 A finer-grained **append-only raw log** of each turn, plus periodic regenerable **summaries** and a **watermark** (so a session can stop/restart without losing context), is designed but not yet built — think incremental compilation: only re-summarize what changed since the last watermark.

See [`AGENTS.md` → Prompts-as-Artifact Discipline](AGENTS.md) for the authoritative rules.

---

## 8. The Tooling

| Tool | Purpose | Status |
|---|---|---|
| **`scripts/setup_clis.sh`** | Repo-independent **machine** bootstrap: installs Node 22 + `claude`/`codex`/`agy`/`gh`, drives browser sign-in, smoke-tests each (`install`/`auth`/`smoke`/`doctor`). Installs ONLY oversight tooling — never project libraries. | ✅ |
| **`scripts/setup_oversight.sh`** | Bootstraps the protocol **into a repo**: AGENTS.md, CODEOWNERS, PR template, `.claude/settings.json`, capture/audit scripts, `prompts/`, branch protection. | ✅ |
| **`scripts/capture_prompt.sh`** | Scaffolds a prompt artifact in `prompts/`, with versioning and a reproducibility check. | ✅ |
| **`scripts/prompt_audit.sh`** | Queries the provenance trail: `--stats`, `--pending`, `--risk LEVEL`. | ✅ |
| **`.claude/settings.json`** | Risk-tiered permission policy: auto-allow reads/safe writes/commits; prompt on push/config; **block** `git push --force`, `rm -rf`, `.env` writes, `sudo`, `curl \| bash`. | ✅ |
| **`.github/CODEOWNERS` + PR template + branch protection** | Owner approval on all files; PR checklist tied to the protocol; `main` requires approval + conversation resolution. | ✅ |
| **`install.sh`** | Platform-aware unified installer (macOS brew / Ubuntu apt / Fedora dnf / yum / pacman). Installs Python 3.10+, ScanCode Toolkit (with system deps), gh CLI, pip analysis packages, agent CLIs guidance. Scaffolds full project structure including `audit/`. | ✅ |
| **`scripts/run_panel.sh`** | The local multi-agent review orchestrator (§6 steps 7–10): triage (Haiku) → cross-vendor reviewers (`agy`/`codex`) → arbiter (Sonnet) → posts per-finding line threads + a summary to the PR. Loads `step{N}-panel-context.md` only (structural signals, no internal findings). IP agent (`ip_check.py`) runs Level 1+2. | 🔧 built, not yet exercised on a live PR |
| **`scripts/run_second_review.sh`** | Pre-PR cross-vendor second review. agy fires at MEDIUM+ (conditional screening); codex fires at HIGH+ (reserve adversarial). Writes machine-readable verdict header. Fail-closed when agy unavailable at MEDIUM+. | ✅ |
| **`scripts/run_red_team.sh`** | Checkpoint system-level adversarial red-team (after steps 3, 6, 10, 11). Uses both codex (attack chains) and agy (spec vs. implementation gap). Requires "not exploitable" attestations — a clean finding list without them is not a valid report. | ✅ |
| **`scripts/oversight/validators/ip_check.py`** | IP/provenance validator. Level 1: dependency license gate (ScanCode Toolkit, PyPI/npm API fallback). Level 2: prompt artifact clean-room verification. Level 3: regurgitation stub (references ai-gen-code-search). | Level 1+2 ✅ / Level 3 🔧 |
| **`scripts/oversight/validators/prompt_audit_risk.py`** | Prompt ambiguity scoring (question density, hedging language, TBDs, implicit assumptions) + fidelity surface (code/prompt ratio, unmentioned functions). Reads `Prompt-Artifact:` git trailers. | ✅ |
| **`audit/oversight-log.jsonl`** | Append-only structured event log committed to the current branch. Covers risk assessments, sign-offs, evaluator decisions, second reviews, panel runs, human authorizations, merges. Queryable with `jq`. | ✅ |

**Scope rule for the bootstrap:** it installs *only* what the oversight system needs (agent CLIs, their Node runtime, `gh`, Python analysis packages). Project frameworks/libraries are out of scope — each project installs its own.

---

## 9. A Worked Example — Correct Escalation

While wiring up the Antigravity CLI, the assistant tried to run its installer: `curl -fsSL https://antigravity.google/cli/install.sh | bash`.

The repo's own `.claude/settings.json` policy **blocks `curl | bash`**, so the gate **denied the action and escalated to the human**. The assistant then fetched and reviewed the installer (checksum-verified, user-scoped, no `sudo`) and surfaced the trade-offs; the human made an explicit decision — *trust the vendor, and keep autoupdate on for security patches.*

This is the methodology working as intended: a deterministic gate routed a genuine trust decision (external code provenance) to a human, with the AI providing a transparent review to *inform* — not replace — the human's call.

---

## 10. What This Is *Not*

This is **not** about slowing development. It is about making oversight information **visible and stratified** so the human makes fast, informed decisions instead of either blindly trusting AI output or exhaustively re-reading every line. If a change is genuinely LOW risk, the correct output is to say so and move on. The goal is **accurate risk signal, not performative process.**
