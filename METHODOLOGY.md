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

## 2. Three Layers of Oversight

### Layer 1 — Self-flagging (single agent) ✅ Implemented
The authoring AI flags its *own* work, per **[`AGENTS.md`](AGENTS.md)**: every non-trivial change gets a risk classification, human-review flags, a confidence declaration, hallucination warnings, and a blast-radius note for destructive operations. This is "a senior engineer who flags their own work," not a code dispenser.

### Layer 2 — Independent review (multiple agents) 🔧 Built, not yet exercised at full scale
Self-flagging has a blind spot: an AI is bad at catching its *own* class of mistakes. So a second layer brings in **independent reviewers from different vendors** to check the author's work, scaled by risk, culminating in a human gate. This is the multi-agent "panel" (§6), implemented in [`scripts/run_panel.sh`](scripts/run_panel.sh) (reads `panel-context.md` only → cross-vendor reviewers → arbiter → posts threads to the PR) — built, awaiting its first run on a live PR at full scale.

### Layer 3 — Risk-stratified human gates ✅ Implemented
Human attention is finite and degrades on low-signal repetitive tasks. Layer 3 routes that attention by risk tier rather than applying it uniformly:

| Risk | Gate |
|---|---|
| LOW | Automated CI gates + statistical spot-check audit |
| MEDIUM | ≥1 cross-vendor reviewer; human reviews flagged items |
| HIGH | Security adversary always-on; human reviews line-by-line |
| CRITICAL | Blast radius required; human approval mandatory before merge |

The spot-check on LOW-tier changes is not theater — it provides an ongoing escaped-defect rate, the primary feedback signal for calibrating tier thresholds over time.

The three layers compose: the author self-flags (Layer 1), independent reviewers scrutinize (Layer 2), and risk-stratified gates ensure human attention lands where it matters most (Layer 3).

**How the human is routed — a flagged queue, not a scan (Jidoka in practice).** The human does not watch every change go by. The system *pulls* a human only where a decision is genuinely needed, and it does so through two explicit, durable channels:

- **GitHub issues labeled [`needs-human`](https://github.com/ScottThurlow/HumanOversightSystem/labels)** — the escalations an agent cannot resolve on its own: spec gaps, design concerns, tier-floor disputes, structural-authorization and CRITICAL-step authorizations, suspension/loosening requests. The label *is* the human's inbox: "requires human attention or decision before it can proceed."
- **Blocking PR review threads** — each panel finding posts a thread that blocks merge until a human resolves it with a *decision* (not just an acknowledgment).

So the human's job is bounded and legible: **keep the `needs-human` issue queue and the open PR threads empty.** Everything not flagged flowed through the automated gates by design — that is the line running normally; a flag is the line stopping (jidoka) to pull a human to the one spot that needs them. This is why "scales human oversight" is not a slogan: the human's workload is the size of the *flagged* set, not the size of the change stream.

**The return path (human → HOS).** When the human decides, the issue moves from the human's inbox back to HOS's: the human **removes `needs-human`, adds [`needs-ai`](https://github.com/ScottThurlow/HumanOversightSystem/labels)**, and writes the decision in a comment with a parseable opener — `Decision: <choice>` (optionally `Action:` / `Disposition:`). HOS (the agentic loop / the standing daily job, #131) scans `needs-ai` issues, reads the `Decision:`, acts on it, and **closes** the issue — or swaps it back to `needs-human` if it needs more input. The result is a clean two-state handoff, `needs-human` ⇄ `needs-ai` → closed, where each side's queue is exactly one label, so neither the human nor HOS has to guess whether the ball is in their court.

> **Threat model — the human gate is forge-proof via identity separation (#152, `docs/AGENT-IDENTITY.md`).** The gate runs as two orthogonal checks: (1) **server-side branch protection** requires an approving review from a human account (`ScottThurlow`) before a PR touching a protected surface can merge; and (2) the **`require-human-approval` CI status check** re-derives whether human approval is present independently. The critical property: the bots (`hos-worker-hos[bot]` / `hos-overseer-hos[bot]`) hold *only their own PATs* — the human's credentials are absent from their environments. GitHub's identity layer therefore makes it structurally impossible for a bot to produce a human approval: the required reviewer is a different account the bot cannot authenticate as. This supersedes the prior #151 TOTP/out-of-band-proof approach: the cleaner fix is the identity split, not a cryptographic add-on. **Residual:** repo-config tampering (disabling branch protection) remains a possible bypass — but it is loud, auditable, and detectable post-hoc (a committed change to `.github/CODEOWNERS` or the workflow file, both on the protected surface, would itself require a human approval to merge). The `#127` accountability layer (content-hashed `human-authorization` records in `audit/oversight-log.jsonl`) stays as the underlying audit trail; forge-proofing is added on top by the identity split.

---

## 3. The AIs

We deliberately use **multiple vendors and model tiers** — the whole point is *decorrelated* judgment. An AI reviewing its own family tends to ratify its own mistakes.

| AI | Driven via | Role |
|---|---|---|
| **Claude** (Opus / Sonnet / Haiku) | `claude` CLI | Author (Opus), triage & cheap review (Haiku), arbiter (Sonnet) |
| **OpenAI** | `codex` CLI (`codex exec`) | Independent reviewer / adversary (high risk) |
| **Google** | `agy` (Antigravity CLI) | Independent cross-vendor reviewer; architecture/whole-repo lens |
| **GitHub Copilot** | GitHub-native PR review | **Baseline reviewer on _every_ PR** (automatic, in CI) |

**Key constraints that shape the design:**
- **CLI-authenticated, not API-keyed.** Each reviewer runs through its own authenticated CLI rather than a metered API. This shapes several design decisions — most importantly why the panel runs locally, not in CI (see §6).
- **Opus is the author**, so Opus may never review its own output. At the highest risk, the *independent* votes must be **cross-vendor** (Google/OpenAI); same-vendor Claude tiers can assist but don't count as the independent check.
- **Copilot is the always-on floor, the cross-vendor panel is the escalation.** Copilot runs automatically on **every** PR (GitHub-native, in CI) — including LOW changes the local panel skips — so there is baseline AI coverage on everything without the cost of the full cross-vendor panel. The cross-vendor panel (`agy`/`codex`) then layers on by risk.
- **Strategic model allocation:** match model capability to task stakes and frequency. High-frequency, lower-stakes work (author triage, cheap review) uses efficient models; high-capability reasoning is reserved for high-risk review and adversarial passes where the cost of a missed finding is highest. Running the most capable model on every task regardless of stakes is wasteful and unnecessary — the goal is appropriate coverage, not maximum spend.
- **Why no Claude in the reviewer seat:** Opus is the author, so no Claude model casts an independent review — same-vendor judgement correlates errors. Sonnet is the **arbiter** (synthesis), never an independent reviewer; the independent votes are cross-vendor (`agy`/`codex`).

> Google is retiring the Gemini CLI (consumer shutoff **2026-06-18**) in favor of the **Antigravity CLI (`agy`)**, which is what the tooling now installs.

### Runtime orchestration agents (the human interface layer)

Two named agents sit above the specialist roles listed above: `worker` is the human's console interface and the autonomous build agent — it accepts work from a human or from a cron invocation, routes it to the appropriate specialist agents (coder, reviewers, etc.), and never absorbs their outputs into its own response. `overseer` is the oversight console and the autonomous review/merge agent — it evaluates what the worker produced and decides what may merge per the merge-authority matrix. Both run under their dedicated machine accounts (`hos-worker-hos[bot]` / `hos-overseer-hos[bot]`) in autonomous mode. This is an orchestration layer above the specialist agents, not a replacement for them; the specialists perform the actual code authoring, reviewing, and testing.

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

**Signals vs. oversight — what the research is actually about.** HOS is a two-layer pipeline, and the distinction matters:

- **Signal layer** — the validators and reviewers *measure or detect* something about the AI-generated code and emit a signal: complexity, N+1, coverage, reliability (quality signals); security, correctness, IP/provenance, prompt-fidelity, hallucination (other signals).
- **Oversight layer** — *acts* on those signals: aggregates them into the composite score, stratifies into tiers, routes human attention, gates merges, escalates, ratchets, audits.

**The research subject is the oversight layer** — how signals become *scaled human oversight*. The signal generators are inputs. Several of them — complexity, N+1, function metrics, portability, diff size — are ordinary **software-quality** checks, so running them also makes the product better; that improvement is a **byproduct, a benefit — not the research claim**. The contribution is the *routing of human attention over aggregated signals*, not the individual metrics, which are cheap, replaceable proxies. The signal set is **extensible**: a project can register its own signal generators (see #80) and the oversight layer consumes them unchanged.

### How the composite score is computed

Each build step runs a set of deterministic validator scripts (`scripts/oversight/run_validators.sh`). **Twelve validator scripts each produce one scored dimension** tabulated below. Every dimension produces a score in [0.0, 1.0] and a weight. The composite is a weighted average across all validators that ran without error — validators that fail to execute are excluded rather than zeroing the score, so a misconfigured tool degrades coverage gracefully rather than suppressing the signal entirely.

```
composite = Σ(score_i × weight_i) / Σ(weight_i)   [over validators without error]
```

The composite then maps to a tier via fixed thresholds:

| Composite score | Tier |
|---|---|
| < 0.30 | LOW |
| 0.30 – 0.54 | MEDIUM |
| 0.55 – 0.77 | HIGH |
| ≥ 0.78 | CRITICAL |

**The validator dimensions and their weights.** The **Signal type** column tags *what each generator detects*. The `quality` signals (complexity, N+1, function metrics, portability, diff size) are ordinary software-quality measures — running them improves the product, which is a benefit, but they are signal *sources*, not the research subject. The `correctness / security / provenance / fidelity / hallucination` signals are the AI-code-specific risk axes; `oversight-loop` is a signal the oversight layer feeds back to itself (escape-rate calibration). All twelve are **inputs** the oversight layer aggregates — the aggregation-and-routing is the contribution, not any individual metric.

| Dimension | Weight | Signal type | What it measures |
|---|---|---|---|
| **Risk Number** (Dai et al.) | 0.18 | correctness | Per-statement nesting increment (empirically calibrated from bug data) + judgment increment (+1 per flow-break, +1 per logical operator). The heaviest weight — the only metric derived from regression on actual bug-nesting relationships. |
| **Static analysis** | 0.15 | security | Bandit MEDIUM security findings. HIGH findings are a blocking gate and never reach the composite; MEDIUM findings are scored as risk signal. Optionally augmented with semgrep Django rules. |
| **Migration risk** | 0.12 | correctness | Django migration operation classification: CRITICAL (RunPython, DeleteModel, RemoveField), HIGH (AlterField type/nullability, RenameField), MEDIUM (AddField nullable, RunSQL read-only), LOW (AddIndex, CreateModel). |
| **Historical density** | 0.12 | oversight-loop | Bug density per file from GitHub issues (`bug`, `security-finding`, `escaped-defect` labels) and git churn. Starts empty; accumulates signal as issues are filed — the loop that makes the scorer improve over time. |
| **Complexity** | 0.08 | quality | McCabe cyclomatic complexity (independent execution paths — testability) and Campbell (2018) cognitive complexity (readability) combined into one score by `complexity_metrics.py` via radon. The two are independent signals — code can be testable but unreadable, or readable but hard to test. |
| **N+1 query detection** | 0.08 | quality | Heuristic for Django ORM calls inside loops — a common performance and correctness hazard in AI-generated Django code. |
| **IP / provenance** | 0.08 | provenance | License gate (ScanCode / PyPI / npm API) + prompt clean-room verification. Orthogonal to correctness risk — surfaces legal exposure independently of code quality. |
| **Function metrics** | 0.07 | quality | Function length, parameter count, return path count. Proxies for review difficulty and likelihood of specification drift. |
| **Prompt ambiguity** | 0.07 | fidelity | Ambiguity score of the captured prompt artifact: question density, hedging language, TBDs, implicit assumptions. High ambiguity → higher probability the generated code diverges from intent. |
| **Hallucination surface** | 0.06 | hallucination | Version-sensitive API usage: imports and attribute accesses flagged against a known list of renamed, removed, or breaking-changed APIs across major library versions. |
| **Portability** | 0.06 | quality | Stack-specific portability signals (e.g. hardcoded paths, environment assumptions, platform-specific calls). |
| **Diff size** | 0.00 | quality | Change-size proxy for review difficulty and blast radius (lines/files changed). Inert in the composite (weight 0.0) — it acts as a discrete **tier floor** rather than a weighted term (#377). |

> **Quality signals are a benefit and a signal source — not the research subject.** Five of the twelve dimensions (`quality`) are conventional software-quality checks. They earn their place by *feeding the oversight layer* a cheap proxy for review difficulty and defect-proneness, and as a side effect they make the generated code better. The research claim does **not** rest on them: swap cyclomatic complexity for any other quality proxy and the oversight contribution — aggregate, stratify, route human attention, gate, ratchet, audit — is unchanged. This is why the signal set is **extensible** (#80): a project registers its own quality (or domain) signals and the oversight layer consumes them without modification.

**The score is a floor, not a ceiling.** The deterministic floor rules (path globs, operation types) can raise the tier independently of the composite score. A migration touching `auth/**` may be forced to HIGH regardless of its composite. The composite can only raise the author's declared tier further — neither the composite nor the floor rules can lower it. The final tier is the maximum across: author declaration, floor rules, and composite score.

**Random red-team audit (Statistical Quality Control).** The adversary/red-team pass is *always-on at HIGH+*; below that, a *salted-random sample* of LOWER-tier PRs (LOW/MEDIUM) gets one too — so red-team coverage is **guaranteed at HIGH+ and probabilistic below**. This audits the auto-pass lane — catching changes that were mis-triaged as low — and yields an **escaped-defect rate**: an empirical measure of how many defects survive in the population we chose not to scrutinize, which is the signal for whether the tier thresholds are calibrated. Selection is a salted deterministic hash of the PR's head SHA (reproducible and auditable, non-gameable without the secret salt); production rates are LOW 5% / MEDIUM 15% (elevated during the pilot so it visibly fires). See [`DECISIONS.md` D17](DECISIONS.md).

---

## 6. The Pipeline (Steps)

The pipeline has three tiers with different cadences: an **inner development loop** that repeats with every incremental prompt, a **transition phase** that runs once per feature before the PR opens, and an **outer loop** that runs once per PR. Conflating them is the most common source of accumulated technical debt in AI-assisted development.

```mermaid
flowchart LR
    A([Spec phase\nspec-red-team]) --> B

    subgraph B [Inner loop — repeats per change]
        direction TB
        B1[Prompt] --> B2[Author + self-flag]
        B2 --> B3[Blocking gates]
        B3 --> B4[Risk scoring\nrun_validators.sh]
        B4 --> B5[Internal review chain]
        B5 --> B6{Clean?}
        B6 -- No --> B1
        B6 -- Yes --> B7[Capture prompt artifact\nMEDIUM+]
    end

    B --> C

    subgraph C [Transition phase — once per feature]
        direction TB
        C1[Commit with provenance\ntrailers] --> C2[System tests]
        C2 --> C3[Second review\nagy / codex]
        C3 --> C4[oversight-evaluator\nPhase 1 + Phase 2]
        C4 --> C5[oversight-orchestrator\nwrites panel-context.md\nand handoff.md]
        C5 --> C6{Recommendation}
        C6 -- PROCEED / CONDITIONAL --> C7[Open PR]
        C6 -- ESCALATE --> H([Human\ndecision])
    end

    C7 --> D

    subgraph D [Outer loop — once per PR]
        direction TB
        D1[CI cheap gates\nlint · types · unit tests] --> D2[AI panel\nreads panel-context.md only]
        D2 --> D3[Human gate\nresolve panel threads]
        D3 --> D4[Merge → audit\noversight-log.jsonl]
    end
```

> **Theoretical model vs. current implementation.** This section describes the pipeline as designed — the invariants that must hold and the logical sequence in which they should be enforced. In the ideal implementation, each step runs inside a controlled pipeline that gates the next step automatically. See the [implementation note](#implementation-note) at the end of this section for how the current implementation approximates those invariants today.

### Inner development loop (repeats N times per feature)

Each prompt-to-verify cycle must leave the codebase in a working state before the next prompt is issued. **Never prompt for the next change on a broken working tree.** Without this invariant, each change builds on unverified output from the previous one — a "house of cards" structure that becomes increasingly difficult to debug as the stack grows.

```
┌─────────────────────────────────────────────────────────────┐
│  INNER LOOP  (repeat until feature is complete)             │
│                                                             │
│  1. PROMPT          Issue one focused, scoped prompt.       │
│                     The prompt is a first-class artifact    │
│                     (§7). Scope it to one logical change.   │
│                                                             │
│  2. AUTHOR + SELF-FLAG  Agent generates code, classifies    │
│                     risk, flags review items, states        │
│                     confidence, warns on hallucination.     │
│                     [Layer 1 ✅]                            │
│                                                             │
│  3. BLOCKING GATES  lint · type-check · secret scan ·       │
│                     security-HIGH. If any gate fails: fix   │
│                     before the next prompt. Do not          │
│                     accumulate failures across prompts.     │
│                     [✅ scripts/oversight/gates/]           │
│                                                             │
│  4. RISK SCORING    Twelve signal dimensions score the      │
│                     change: complexity, N+1, migration      │
│                     risk, IP/provenance, prompt fidelity,   │
│                     hallucination surface, and others.      │
│                     The oversight layer (risk-assessor)     │
│                     aggregates them into a composite        │
│                     score + inspection brief.               │
│                     [✅ scripts/oversight/run_validators.sh]│
│                                                             │
│  5. INTERNAL REVIEW Review panel runs in parallel; each     │
│                     agent appends a structured sign-off     │
│                     to the shared register. Register is     │
│                     machine-readable and checked for        │
│                     completeness before step advances.      │
│                     [Layer 2 🔧 at full scale]              │
│                                                             │
│  6. CAPTURE         For MEDIUM+: capture_prompt.sh records  │
│                     the prompt artifact. [✅]               │
│                                                             │
│  └──────── back to 1 for next incremental change ──────────┘
```

**Agent dispatch sequence — one inner loop cycle:**

```mermaid
graph TD
    P[Prompt issued] --> CO[coder\nwrites code + self-flags]
    CO --> GA[Gates\nlint · type-check · secret scan · bandit HIGH]
    GA -- gate fails --> CO
    GA -- gates pass --> VA[run_validators.sh\n12 signal dimensions → composite score]
    VA --> RA[risk-assessor\nvalidates tier · produces inspection brief]
    RA --> PF[prompt-fidelity\nMEDIUM+ only]
    RA --> DM[dep-mapper\nHIGH+ only]
    RA --> RH[risk-historian\nHIGH+ only]
    PF --> CR[code-reviewer\ngates parallel reviewers]
    DM --> CR
    RH --> CR
    CR -- changes requested --> CO
    CR -- approved --> PR[Parallel reviewers\nsecurity · privacy · ui · a11y\nops · reliability · infra]
    PR --> UT[unit-test]
    UT --> REG[Sign-off register\nentry written]
```

### Definition of Done (inner loop step)

A build step is **not done** until all four conditions hold — in this order:

1. **Code passes inner-loop tests.** `run_tests_inner_loop.sh` exits 0 against the current working tree.
2. **Relevant docs updated.** If the step modified documented behavior (new agent, new gate, new governance rule, changed spec flow), the relevant spec, design doc, AGENTS.md, or DECISIONS.md entry reflects what was built. A step whose observable behavior differs from its documentation is not done — it is broken.
3. **Research finding filed (if applicable).** If the step produced empirical data (escaped defect, tier-calibration signal, pipeline timing), a finding is recorded in `research/findings/`.
4. **Sign-off register entry written.** The step's register entry is complete with all required fields (§3).

"I'll update the docs later" is not a valid sign-off state.

### Design artifacts (required before coding begins)

Every design cycle that produces code must commit two artifacts before the coder begins:

1. **pm-agent spec** → `docs/specs/SPEC-{feature}.md` (normative requirements)
2. **Technical design** → `docs/v{version}/TECHNICAL-DESIGN-{feature}.md` (coder-ready contract)

The `technical-design` agent writes its output to disk as part of the task — not as conversation text only. A sign-off register step may not be marked complete if the corresponding design doc is absent. This applies to all tiers; patch-tier steps that touch documented behavior are not exempt.

The design doc convention is also the audit trail for "why is the code this way?" — losing it to conversation history is a governance gap.

> **Why the inner loop matters:** AI agents have no memory of the codebase state from one prompt to the next beyond what is in the current context. An agent asked to "add X" on a tree where Y is already broken will produce code that looks correct but depends on a broken foundation. By the time CI runs (after the PR opens), the failure stack may span five prompts and require significant archaeology to untangle. Local verification after each prompt is the only reliable way to keep the codebase in a known-good state throughout development.

### Transition phase (runs once per feature, pre-PR)

Once the inner loop produces a complete verified working state, a transition phase runs before the PR opens. This phase is distinct from the outer loop: it has access to the full internal review record and produces a curated output for the panel that deliberately withholds that record.

```
7. COMMIT            Commit verified working state with provenance
                     trailers: Prompt-Artifact / AI-Model / AI-Risk.
                     Provenance is queryable via prompt_audit.sh.        [✅]

8. SYSTEM TESTS      e2e/system tests, coverage, mutation testing
                     gated by risk tier.                                 [🔧 per-project]

9. SECOND REVIEW     Cross-vendor review of the full change set:
                     agy at MEDIUM+, codex at HIGH+. Produces a
                     machine-readable verdict header.
                     Fail-closed when agy unavailable at MEDIUM+.       [✅ run_second_review.sh]

10. OVERSIGHT-EVAL   Phase 1 — compliance: did all required sign-offs
                     happen? prompt artifacts captured? human auth
                     present where required?
                     Phase 2 — quality: convergence failures, unresolved
                     critical findings, confidence gaps.
                     Produces: PROCEED / CONDITIONAL_PROCEED / ESCALATE. [✅ oversight-evaluator]

11. ORCHESTRATE      Acts on evaluator recommendation.
                     Writes two separate outputs:
                       panel-context.md  — structural signals only,
                                          no internal findings
                       handoff.md        — full picture for human/PR
                     PROCEED/CONDITIONAL → opens PR.
                     ESCALATE → surfaces bounded questions to human;
                                does NOT open the PR.                   [✅ oversight-orchestrator]

   pr_readiness.py   →  deterministic self-assessment gate (REQ-W-01..W-14);
                        exit 0 required before gh pr create              [✅ #317]
```

**Why `panel-context.md` withholds internal findings:** if the outer panel could read the internal reviewers' conclusions, it would tend to converge on the same findings rather than produce a genuinely independent signal. The isolation is an anchoring-prevention mechanism — the same principle as blind peer review. The panel sees structural signals (risk tier, blast radius, provenance) but forms its own judgments.

### Convergence by disposition — triage/accept, not fix-everything (#133)

An adversarial self-review on a rich governance corpus **never says "nothing left."** Treating every `blocking` finding as fix-or-file has two costs: **churn-induced regressions** (editing a dense governance file to fix a minor finding re-enters it into the changed set, the reviewer surfaces the *next* subtlety, and the edit itself can introduce a worse problem — fixing can cost more than the finding) and **non-termination** (each fix spawns new findings). So convergence is defined by **disposition, not repair**: every finding is routed to exactly one of —

| Disposition | When |
|---|---|
| **fix** | clear, safe, low-churn fix AND the finding is non-trivial |
| **filed** | real design/foundational issue → tracked as an issue, no churn now |
| **residual (accept)** | minor in practice AND fix-churn-risk > finding-severity → record + move on |
| **noise** | false positive / non-reproducing |

**Stopping rule:** once dispositioned (any of the four), a finding is deduped in the convergence ledger and never re-gates. **Convergence = "every finding dispositioned," not "every finding fixed."** **Anti-gaming guardrail:** `residual` accepts a *real* finding as not-worth-fixing — a **human** (or an explicit confidence/severity threshold) decides residual-vs-fix; the AI must **not** silently downgrade a real finding to `residual` to unblock itself (the agent cannot mark its own homework done). The ledger is `scripts/framework/validate_agents.sh --record FILES CLASS DISPOSITION`. This is the same triage discipline the consumer-facing `docs/HANDLING-FINDINGS.md` teaches (fix / accept-with-rationale / scanner-fp) and that Faberix R1 applies to validator debt (#167).

### Outer loop (runs once per PR)

```
12. PR               Opened by oversight-orchestrator (or human on
                     ESCALATE). main is protected: ≥1 approval + all
                     review threads resolved before merge.              [✅]

   overseer bounce-back  →  register-completeness check before merge-authority
                            matrix; incomplete → pr-bounced (PR stays open,
                            re-assigned to worker, failure counter unchanged) [✅ #317]

13. CI CHEAP GATES   lint, types, build, unit tests, secret scan.
                     If CI fails on a gate the inner loop should have
                     caught, that is a process health signal — the
                     inner loop was skipped or incomplete.              [🔧 per-project]

14. AI PANEL         Reads panel-context.md only. Cross-vendor
                     reviewers each apply a lens; adversary at
                     HIGH/CRITICAL. Findings posted as PR review
                     threads. Runs locally via authenticated CLIs.     [🔧 run_panel.sh]

15. HUMAN GATE       Risk-stratified, NOT every PR (Jidoka): the
                     human is pulled in only where needed —
                     HIGH/CRITICAL get mandatory human review;
                     LOW/MEDIUM auto-pass (SQC spot-check audit).
                     WHERE a panel raises a review thread, that
                     thread needs a human decision to resolve —
                     not just an acknowledgment.                       [✅ gate / 🔧 panel]

16. MERGE → AUDIT    Resolved trail committed to
                     audit/oversight-log.jsonl (append-only).          [✅]

17. RELEASE AUTH     Human-authorized only (NG3b). Trigger: human creates
                     "do release vX.Y.Z" issue (label release-request, assigned
                     to worker). Worker validates all required suites, posts
                     results + "Release candidate SHA: <sha>" + how-to-authorize
                     instructions. Final cut ONLY on three-part GitHub signal:
                     release-authorized label + needs-human removal + re-assign
                     to worker — all by the same human CODEOWNER, temporally
                     after the results comment (§6 four conditions + §6.3
                     same-actor three-signal). Chat never authorizes the cut.
                     Violations → ng3b-violation-attempt audit event.  [#345]

**Three-tier review model (confirmed, #356 decision 2026-06-16):**

| Tier | When | Script | Vendor cost |
|---|---|---|---|
| **Inner loop** | Per code change | `run_validators.sh` + reviewer agents | None |
| **Pre-PR** | Before opening any PR | `run_review_chain.sh --tier <tier>` | agy (MEDIUM+), codex (HIGH+) |
| **Release gate** | Per phase, at release | `run_panel.sh` on phase-review PR | Full panel (agy + codex + Copilot) |

`run_review_chain.sh` is the canonical pre-PR script. Panel is release-only by default; invoke one-off with `--pr N` when needed.
```

**Why the panel runs locally, not in CI:** the CLIs authenticate interactively (browser OAuth that lives on your machine); CI runners can't hold that session. So CI handles the deterministic gates + Copilot's native PR review, while the cross-vendor AI panel runs from a local command and **posts its findings to the PR**. The PR stays the auditable record.

**CI cheap gates as a diagnostic signal:** if CI fails on a gate that the inner loop should have caught (lint, type errors, unit tests), that is evidence the inner loop was skipped or the verify step was incomplete. Track CI-caught-but-inner-loop-missed failures as a process health metric — a rising rate indicates the verify step is being skipped under time pressure.

### Implementation note

The pipeline above describes the invariants — what must be true before each step can advance. The current implementation enforces those invariants differently from how an ideal in-pipeline system would.

**How it actually works today:**
- The inner loop gates and validators (`run_validators.sh`, the review agents) are invoked by the developer locally, outside of any automated pipeline. They are not triggered by a CI system watching commits.
- Each step produces a **sign-off artifact** — a structured file written to the register when the step completes. The sign-off is the proof that the step ran.
- The PR check does not re-run the gates. It checks that sign-off artifacts **exist** for all required steps at the correct risk tier. If they are absent, the PR is blocked.

**The gap this creates:** sign-off existence proves the scripts were run, but not necessarily in the right order or against the right version of the code. A sign-off from a previous session could satisfy the check even if the code changed since.

**The ideal implementation** would close this gap with a file-timestamp ordering check: sign-off files carry a timestamp, and the PR check verifies that each sign-off's timestamp is newer than the last commit touching the files it covers. This proves the gate ran *after* the relevant change, not before it. This mechanism is designed but not yet implemented — it is the target state toward which the current sign-off-existence check is a stepping stone.

---

## 7. Prompts as Source Code

A defining principle: **prompts and their summaries are treated as source code artifacts**, version-controlled alongside the code they produce.

**The compiler analogy.** The prompt is the **"C source"**; the generated code is the **"compiled artifact"** (object/binary). In principle you should be able to *regenerate* the code from the prompt. Unlike a normal build (where you'd gitignore the binary), here **both** the prompts and the generated code are committed and versioned. The compiler analogy is the mental model for *provenance and authority*, not the version-control policy.

**Prompt summaries as first-class artifacts.** A raw turn log is too verbose to review directly. Periodic, regenerable summaries serve two distinct purposes:

1. **Human comprehension.** A summary lets a human reviewer grok the outcome of a session — what intent was expressed, what decisions were made, what was deferred, what tradeoffs were accepted — without replaying every turn. This is the oversight purpose: a human should be able to read the summary and form a genuine judgment about whether the session did what it was supposed to do.

2. **Expedited rerun.** A well-formed summary doubles as a one-shot prompt: a distillation of the session's intent precise enough that re-issuing it should reproduce the session's outputs. This makes AI-generated work reproducible and auditable in the same way that source code is — the summary is the "source" that regenerates the session, not just a record that it happened.

These summaries are themselves versioned and subject to the same sign-off requirements as the code they describe. A summary that diverges from the actual generated code is a provenance failure, not a documentation gap. 🔧 The summary/watermark pipeline is designed but not yet fully built: think incremental compilation — only re-summarize turns since the last watermark, keeping the artifact current without replaying the full session history.

**What lives where.**
- `prompts/` — curated, cleaned prompt artifacts (one per MEDIUM+ change, mirroring `src/`). ✅
- Turn-level raw log — append-only per-session record (`capture_session.sh --log`). ✅
- Session summaries + watermarks — generated by `capture_session.sh --summarize` (requires agy or codex); `--watermark` marks last summarized position so incremental re-summarization only covers new turns. ✅ mechanism built. 🔧 the *expedited-rerun* claim (that a summary can reproduce the session) is **unvalidated** — the structural pipeline exists but rerun fidelity has not been empirically tested.
- Git commit trailers (`Prompt-Artifact`, `AI-Model`, `AI-Risk`) — lightweight provenance on every commit, queryable via `prompt_audit.sh`. ✅

See [`AGENTS.md` → Prompts-as-Artifact Discipline](AGENTS.md) for the authoritative rules.

---

## 8. The Tooling

> **Two tool families — only one ships to consumers.** (1) The **per-step oversight pipeline** — gates, `run_validators.sh`, `run_second_review.sh`, `run_panel.sh`, the validators under `scripts/oversight/`, the agents under `.claude/agents/` — is what a consumer project runs on every build step, and `hos_install.sh` installs it. As of v0.3.0 this includes both the oversight layer and the **canonical 16-agent base development team** (pm-agent, architect, technical-design, coder, 8 reviewers, unit-test, system-test, ops-designer, ux-designer); consumers no longer hand-roll the base team. (2) The **framework-development harness** under `scripts/framework/` — `run_framework_validation.sh`, `validate_self.sh`, `validate_agents.sh`, `validate_docs.sh`, `validate_spec_compliance.sh`, `check_agents_static.sh`, `cut_release.sh` — validates and releases the *framework itself* and is run **from the HOS source repo**, by framework maintainers, when changing HOS's own agents/docs. It is **NOT installed into consumer projects** (and the installer does not copy it). A consumer never runs `run_framework_validation.sh`; their equivalent is the per-step pipeline above. (HOS#139)
>
> **Why a consumer doesn't need it — the customization contract.** Agent files are **layered**: **CORE** (HOS-owned generic behavior, validated at release), **PACK:\<name\>** (HOS-owned stack depth from `packs/<name>/`, selected via `--pack <name>` at install and recorded as `PACK=` in `config.sh`), and **PROJECT** (consumer-owned project-specific additions). CORE and PACK regions are HOS-owned and taken from HOS on every upgrade (hard-stop on drift unless `--squash`). PROJECT regions are consumer-owned and never overwritten. Consumers also configure via the **declared manifest**: the placeholders in `scripts/framework/placeholders.manifest` → `config.sh` (`PROJECT_NAME`, `SPEC_FILE`, `DESIGN_PACK_DIR`, `ADR_FILE`), plus the one sanctioned structural override (`dep-mapper`, intended to be stack-specific). Because a consumer's CORE/PACK regions are always "the HOS-validated set," there is nothing for them to *re*-validate — which is why the framework-validation harness stays at the source. A project that needs to edit CORE or PACK agent *logic* has forked — and a fork clones the HOS source and owns its own validation. The framework's core guarantee — *"this pipeline was validated"* — holds precisely because CORE/PACK customization is gated by HOS.

| Tool | Purpose | Status |
|---|---|---|
| **`bootstrap/setup_clis.sh`** | Repo-independent **machine** bootstrap: installs Node 22 + `claude`/`codex`/`agy`/`gh`, drives browser sign-in, smoke-tests each (`install`/`auth`/`smoke`/`doctor`). Installs ONLY oversight tooling — never project libraries. | ✅ |
| **`scripts/setup_oversight.sh`** | *Legacy* project installer (AGENTS.md, CODEOWNERS, PR template, `.claude/settings.json`, capture/audit scripts, `prompts/`, branch protection). **Superseded by `bootstrap/hos_install.sh`** (release-pinned install); reconciliation tracked in #87. | ✅ |
| **`scripts/capture_prompt.sh`** | Scaffolds a prompt artifact in `prompts/`, with versioning and a reproducibility check. | ✅ |
| **`scripts/capture_session.sh`** | Session turn log, summary, and watermark management. `--log FILE MSG` appends a turn entry; `--summarize` generates a session summary via agy/codex covering human comprehension + expedited rerun; `--watermark` marks the last summarized position for incremental re-summarization; `--status` shows current state. Summaries written to `prompts/sessions/`. | ✅ mechanism / 🔧 rerun-fidelity unvalidated |
| **`scripts/prompt_audit.sh`** | Queries the provenance trail: `--stats`, `--pending`, `--risk LEVEL`. | ✅ |
| **`.claude/settings.json`** | Risk-tiered permission policy: auto-allow reads/safe writes/commits; prompt on push/config; **block** `git push --force`, `rm -rf`, `.env` writes, `sudo`, `curl \| bash`. | ✅ |
| **`.github/CODEOWNERS` + PR template + branch protection** | Owner approval on all files; PR checklist tied to the protocol; `main` requires approval + conversation resolution. | ✅ |
| **`bootstrap/hos_bootstrap.sh`** | MACHINE bootstrap (macOS brew / Ubuntu apt / Fedora dnf / yum / pacman): Python 3.10+, ScanCode Toolkit (with system deps), gh CLI, pip analysis packages, and (via `setup_clis.sh`) the agent CLIs. Run once per machine. | ✅ |
| **`bootstrap/hos_install.sh`** | PROJECT installer: fetches a validated **release** (`--release <tag>` / `--local`) and scaffolds it into a target repo including `audit/`. No sudo; checks prerequisites. Records the installed tag at `.hos-release`. | ✅ |
| **`scripts/run_panel.sh`** | The local multi-agent review orchestrator (§6 steps 7–10): triage (Haiku) → cross-vendor reviewers (`agy`/`codex`) → arbiter (Sonnet) → posts per-finding line threads + a summary to the PR. Loads `step{N}-panel-context.md` only (structural signals, no internal findings). IP agent (`ip_check.py`) runs Level 1+2. | 🔧 built, not yet exercised on a live PR |
| **`scripts/run_second_review.sh`** | Pre-PR cross-vendor second review. agy fires at MEDIUM+ (conditional screening); codex fires at HIGH+ (reserve adversarial). Writes machine-readable verdict header. Fail-closed when agy unavailable at MEDIUM+. | ✅ |
| **`scripts/run_red_team.sh`** | Checkpoint system-level adversarial red-team (after steps 3, 6, 10, 11). Uses both codex (attack chains) and agy (spec vs. implementation gap). Requires "not exploitable" attestations — a clean finding list without them is not a valid report. | ✅ |
| **`scripts/run_redteam_sample.sh`** | Statistical sampling red-team. Draws N recent LOW-tier commits (default 20, last 30 days), red-teams each diff independently, computes escaped-defect rate. The empirical signal for calibrating tier thresholds — the Cleanroom/SQC mechanism. Options: `--n`, `--days`, `--dry-run`. Appends `sampling-audit` to `audit/oversight-log.jsonl`. | ✅ |
| **`scripts/oversight/sign_off.sh`** | Writes a committed sign-off stamp (`signoffs/<role>.stamp`) for a validation-suite role. Validates the role against `step-manifest.yaml` via the oversight venv Python (PEP 668-safe). The CI-enforcement complement to the markdown sign-off register. | ✅ |
| **`scripts/oversight/signoff_gate.py`** | Sign-off gate. Fails unless every required role (union of `required_signoffs` across steps) has a committed stamp no older than the newest changed file, by git commit timestamp. `--base REF` (PR/CI) or `--all` (deploy). Self-execs into the oversight venv if the system Python lacks PyYAML. | ✅ |
| **`scripts/oversight/suspension_manager.py`** | Manages `contract/gate-suspension.md`. `--census` (active count + `review-by` warnings + `suspension-census` event), `--check` (run auto-checkable script gates, record pass-history), `--auto-remove` (re-enable pure script gates that pass N consecutive checks unless `[pinned]`). Ratchet-safe: only ever removes suspensions, never adds. Emits `gate-auto-reenabled`. | ✅ |
| **`scripts/oversight/validators/ip_check.py`** | IP/provenance validator. Level 1: dependency license gate (ScanCode Toolkit, PyPI/npm API fallback). Level 2: prompt artifact clean-room verification. Level 3: regurgitation stub (references ai-gen-code-search). | Level 1+2 ✅ / Level 3 🔧 |
| **`scripts/oversight/validators/prompt_audit_risk.py`** | Prompt ambiguity scoring (question density, hedging language, TBDs, implicit assumptions) + fidelity surface (code/prompt ratio, unmentioned functions). Reads `Prompt-Artifact:` git trailers. | ✅ |
| **`audit/oversight-log.jsonl`** | Append-only structured event log committed to the current branch. Covers risk assessments, sign-offs, evaluator decisions, second reviews, panel runs, human authorizations, merges. Queryable with `jq`. | ✅ |
| **`scripts/framework/install.sh`** | Installs or updates the agent pipeline framework in a consumer project repo. Interactive: creates dirs, copies agent files, collects project-specific config values, writes `config.sh`, runs static check. Idempotent — re-running preserves existing config. | ✅ |
| **`scripts/framework/check_agents_static.sh`** | Fast structural consistency checker for the agent pipeline (no AI). Checks: agent files exist for every name referenced in docs, file paths in agent prompts are valid, escalation targets resolve, project-start output doc paths are consistent. Suitable for pre-commit and CI. | ✅ |
| **`scripts/framework/validate_agents.sh`** | AI-powered semantic review of agent definitions and docs. agy lens: consistency and completeness (loops, dead ends, cross-file mismatches). codex lens: adversarial gap-finding (scope-creep vectors, human-gate bypasses, missing exit conditions). Project-agnostic via `config.sh`. | ✅ |
| **`scripts/framework/validate_docs.sh`** | AI-powered documentation coverage validator. Catches the omission class of doc bug: agent file says X and Y, doc says only X. Reads `doc-patterns.md` (known bug patterns) and `decisions.md` (verification criteria). | ✅ |
| **`scripts/framework/validate_spec_compliance.sh`** | Governance requirements compliance checker. Verifies the pipeline satisfies METHODOLOGY.md and AGENTS.md: cross-vendor independence, risk-tiered thresholds, human gates, model tier assignments, loop exit conditions, mandatory self-flagging behaviors. Also checks `decisions.md` verification criteria. | ✅ |
| **`scripts/framework/run_framework_validation.sh`** | Top-level runner for all 4 framework validation phases. The single command a **framework maintainer** runs **from the HOS source repo** before committing a change to the **framework itself** (HOS's own agents/docs). **Not installed into consumer projects** — see the two-families note above. | ✅ |
| **`ops-designer` agent** | Observability authority (optional). Invoked at project start (after `architect` ADR) to produce `docs/ops/TELEMETRY-SPEC.md`. Architect validates. Reactive during build when `ops-reviewer` escalates gaps. N/A for projects without background jobs, external integrations, or multi-service architecture. | ✅ |
| **`ops-reviewer` agent** | Telemetry spec enforcer (optional). Inner-loop reviewer, parallel with security/privacy. Enforces `TELEMETRY-SPEC.md` per PR. Escalates spec gaps to `ops-designer`; 2-cycle loop exit to architect. | ✅ |
| **`reliability-reviewer` agent** | Resilience reviewer (optional — projects with external connections). Inner-loop reviewer. Reviews timeouts on outbound connections, retry with backoff, graceful degradation, no unbounded waits. No spec file required — best practices are universal. N/A for projects without DB, HTTP, or queue calls. | ✅ |
| **`scripts/framework/decisions.md`** | Durable record of architectural decisions made during development sessions. Each entry has a verification criterion checked by `validate_spec_compliance.sh`. Replaces chat history as session-to-session memory. | ✅ |
| **`scripts/framework/doc-patterns.md`** | Known documentation omission patterns discovered during sessions. Read by `validate_docs.sh` so previously-discovered bug classes are actively checked for recurrence. | ✅ |

**Scope rule for the bootstrap:** it installs *only* what the oversight system needs (agent CLIs, their Node runtime, `gh`, Python analysis packages). Project frameworks/libraries are out of scope — each project installs its own.

---

## 9. A Worked Example — Correct Escalation

While wiring up the Antigravity CLI, the assistant tried to run its installer: `curl -fsSL https://antigravity.google/cli/install.sh | bash`.

The repo's own `.claude/settings.json` policy **blocks `curl | bash`**, so the gate **denied the action and escalated to the human**. The assistant then fetched and reviewed the installer (checksum-verified, user-scoped, no `sudo`) and surfaced the trade-offs; the human made an explicit decision — *trust the vendor, and keep autoupdate on for security patches.*

This is the methodology working as intended: a deterministic gate routed a genuine trust decision (external code provenance) to a human, with the AI providing a transparent review to *inform* — not replace — the human's call.

---

## 10. What This Is *Not*

This is **not** about slowing development. It is about making oversight information **visible and stratified** so the human makes fast, informed decisions instead of either blindly trusting AI output or exhaustively re-reading every line. If a change is genuinely LOW risk, the correct output is to say so and move on. The goal is **accurate risk signal, not performative process.**
