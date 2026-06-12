# Framework Build Log — Agent Pipeline Development

*Session date: 2026-06-12. Written for the doctoral research paper as an addendum to METHODOLOGY.md.*

This document is a chronological log of what was built, what was learned, and how those learnings were applied during the design and construction of the multi-agent oversight framework. It records decisions, surprises, errors caught, and the feedback loops that corrected them.

---

## Context

This project is simultaneously a real software product (CondoParkShare) and a living experiment in AI-assisted development governance. The framework described here — the agent pipeline, validation suite, and oversight tooling — was itself built using the same AI-assisted process it governs. This creates an interesting recursive structure: the governance system was subjected to its own oversight mechanisms as it was built.

The session that produced this log began with a specific escalation: the oversight system surfaced a UX design question (missing error color palette) to the human that should have been answerable without human involvement. That escalation revealed a structural gap: there was no design authority in the agent pipeline. Everything in this log flows from that discovery.

---

## 2026-06-12 — Session log

### ~09:00 — Trigger event: ux-designer agent created

**What happened:** The system escalated a design question to the human — specifically, a missing color palette for error states that `ui-reviewer` couldn't resolve because no agent had authority to extend the design pack. The human observed this and asked: could we have an agent that answers these questions directly?

**What we learned:** The pipeline had a structural hole. `ui-reviewer` could check conformance against the design pack but had no path to fill gaps in it. Every gap escalated to human. This was the wrong default — most design gaps are additive (new tokens, new component variants) and don't need human judgment.

**How we applied it:**
- Created `ux-designer` as a 4th authority tier, peer to `pm-agent` and `architect` within the design domain
- Gave it authority to make additive/clarifying changes to the design pack without human approval
- Structural changes (changing core brand colors, typeface, or the brief) still require human escalation
- The three-tier classification (clarifying/additive/structural) was borrowed from `pm-agent`'s existing spec-amendment taxonomy — deliberate mirroring so agents reason about change risk the same way across domains

**Connections to the research:** This illustrates the governance problem in miniature: the human was acting as a bottleneck not because they needed to, but because no agent had been granted the authority to decide. Authority delegation with explicit escalation conditions is the mechanism that scales oversight without requiring human attention on every decision.

---

### ~09:30 — ux-designer invocation timing: proactive vs. reactive

**What happened:** After creating ux-designer as a reactive gap-filler, the human asked: shouldn't ux-designer also run proactively at project start, the way pm-agent and architect do?

**What we learned:** The initial design had ux-designer as a support agent only — invoked when reviewers hit gaps. But by the time a reviewer hits a gap, the coder has already written code that may not fit the missing pattern. Running ux-designer proactively at project start eliminates the gap before any code is written.

**How we applied it:**
- Added a mandatory "Initial design audit" section to ux-designer — reads the full spec, walks all 12 user-visible feature areas, fills all design pack gaps, writes `docs/design/UX-DESIGN-READINESS.md`
- Positioned it in the project-start sequence: pm-agent → ux-designer → architect → technical-design
- architect and technical-design now read `UX-DESIGN-READINESS.md` before starting their work
- ux-designer retained its reactive role for gaps that emerge during the build

**Key insight recorded in decisions.md (DEC-001):** ux-designer has two modes. Any documentation that describes only one mode is wrong. This became the first entry in `doc-patterns.md` — a pattern to check for in future validation runs.

---

### ~10:00 — Documentation inconsistency discovered: ux-designer described as reactive-only

**What happened:** After implementing the two-mode design, a documentation note (added earlier in the session) still described ux-designer as "Not a step in the pipeline; invoked reactively on demand." The human spotted this.

**What we learned:** There's a class of documentation bug that isn't a contradiction — the reactive description was correct as far as it went — but an *omission*. An agent file says X and Y; documentation says only X. This doesn't trigger the kinds of consistency checks that look for contradictions.

**How we applied it:**
- Fixed the two notes in `docs/OVERSIGHT-RUNBOOK.md` immediately
- Recognized this as a distinct bug class that required its own validator
- Created `doc-validator` agent and `validate_docs.sh` (Phase 3 of the validation suite)
- Created `doc-patterns.md` to record this pattern so future validation runs actively check for recurrences
- PATTERN-001: "ux-designer described as reactive-only" — check for any description containing "reactive" that doesn't also mention "project start"

**Connections to the research:** This is automation bias in reverse: because the existing validators didn't flag the note, there was a temptation to assume it was correct. The lesson is that absence of a validator finding is not the same as correctness. The validator suite had a blind spot for omissions.

---

### ~10:30 — Framework validation suite: scripts/framework/ created

**What happened:** With ux-designer in place, the human asked whether we had tooling to validate the framework itself — not just the application code.

**What we learned:** The oversight pipeline that had been built for application code had no equivalent for its own definition files. The agent files and documentation were reviewed by humans during session work, but nothing checked them programmatically between sessions.

**How we applied it:**
- Created a 4-phase validation suite in `scripts/framework/`:
  - Phase 1: `check_agents_static.sh` — structural checks, no AI, runs in CI/pre-commit
  - Phase 2: `validate_agents.sh` — agy + codex semantic review (loops, contradictions, gaps)
  - Phase 3: `validate_docs.sh` — documentation coverage (omission detection)
  - Phase 4: `validate_spec_compliance.sh` — governance requirements compliance
- Created `run_framework_validation.sh` as the single entry point
- Created three new agents: `framework-validator`, `framework-setup-validator`, `doc-validator`, `spec-compliance-validator`, `post-change-sweep`
- Created `install.sh` for applying the framework to new projects

**Key insight:** The framework scripts themselves are project-agnostic by design. All project-specific context lives in `config.sh`, generated by `install.sh`. This separation means the framework scripts can be updated without touching project customizations.

---

### ~11:00 — Decisions.md and doc-patterns.md: chat history as a validator input

**What happened:** The human asked whether the validators could catch things that were discussed in chat but not implemented, or implemented incorrectly.

**What we learned:** Validators work within a closed set of committed files. Chat history is outside that set. Two distinct gaps:
1. Chat → Implementation: Things decided in conversation but never committed. No validator can catch this automatically — it requires a human discipline rule.
2. Implementation → Functional expectations: The validators check internal consistency but not whether the implementation satisfies the governance spec described in METHODOLOGY.md.

**How we applied it:**
- `decisions.md`: a structured file where design decisions made during sessions are recorded with verification criteria. The spec-compliance validator reads this file and checks each decision against its stated implementation files. This is the durable replacement for chat history.
- `doc-patterns.md`: same concept for documentation bug patterns — when a class of doc bug is found and fixed, the pattern is recorded so future runs actively check for it.
- `validate_spec_compliance.sh` (Phase 4): reads METHODOLOGY.md and root AGENTS.md to extract governance requirements, then checks each agent file and script against those requirements. Checks REQ-001 through REQ-008.

**Connections to the research:** This is the core oversight scaling challenge in microcosm. The human can't remember everything decided in prior sessions; the AI can't access prior conversations. The only durable knowledge is what's written to files. The implication for organizations using AI-assisted development: decisions made in AI chat sessions must be immediately committed to tracked artifacts (decisions.md, ADRs, spec updates) or they effectively don't exist.

---

### ~11:30 — Running the validation suite: first real run

**What happened:** The human asked to run the full validation suite. Three phases were run (Phase 1 static; Phase 2 agy + codex semantic; Phase 4 spec compliance). Phase 3 timed out.

**What we learned — findings from Phase 2 (agy):**

1. `prompt-fidelity` agent referenced in risk-assessor.md but no agent file existed — a dead end in the escalation chain
2. `deploy-verify` escalations not listed in coder.md and system-test.md receiving lists
3. `code-reviewer` escalates design disputes to technical-design, but technical-design had no handler for this
4. `architect.md` escalation arbitration only listed coder/code-reviewer/technical-design, but security/privacy/a11y/unit-test also escalate there
5. `pm-agent.md` only received from technical-design/unit-test/system-test, but security-reviewer/privacy-reviewer/ux-designer/coder also escalate to pm-agent
6. `coder.md` said "three reviewers" but the pipeline requires five parallel reviewers for template changes
7. `framework-validator.md` was missing Phase 3 and Phase 4 in its process steps
8. `ux-designer.md` listed pm-agent and technical-design as reactive invokers but neither file had invocation instructions
9. `framework-setup-validator.md` REQUIRED list had 16 agents; actual count is 25
10. Agent count inconsistency across docs (17/23/25/16)
11. `post-change-sweep.md` missing SETUP.md/CUSTOMIZATION.md from framework domain
12. `post-change-sweep.md` referenced `bookings` directory; actual app uses `parking`
13. `coder.md` primary inputs missing `CONFIRMED-REQUIREMENTS.md`

**What we learned — findings from Phase 4 (agy spec compliance):**

1. REQ-004 model assignment: `coder.md` used `claude-sonnet-4-6` but the coder is the authoring agent — METHODOLOGY.md states "Opus is the author." Should be `claude-opus-4-8`.
2. REQ-006: `coder.md` had no instructions for the 5 mandatory self-flagging behaviors (risk classification, Human Review Required, confidence, hallucination warnings, blast radius)
3. REQ-007: `coder.md` had no prompt capture instruction for MEDIUM+ changes

**How we applied it:**
- All 13 Phase 2 findings and 3 Phase 4 compliance gaps fixed in a single commit
- Added `prompt-fidelity.md` stub agent
- Updated 9 agent files and 2 documentation files
- Re-ran Phase 1 static check: clean (0 findings)

**Key insight:** The model assignment error (coder using Sonnet instead of Opus) is exactly the kind of subtle compliance gap that the governance spec is designed to prevent but that no human would notice without the validator. The coder is the highest-risk agent in the pipeline — it generates all the code. Using Sonnet instead of Opus violates the "Opus is the author" constraint from METHODOLOGY.md. The validator caught this; a human code review almost certainly wouldn't have.

---

## Meta-observations for the research paper

### 1. The framework governed itself

The agent pipeline framework was built using the same Claude Code session that the framework governs. The mandatory self-flagging behaviors (risk classification, confidence declarations, hallucination warnings) were active throughout. The validation suite was run against the framework files themselves before any commit.

This is the "living experiment" property of the project — the methodology is not described from the outside but is being exercised on the artifacts that describe it.

### 2. Validators find things humans miss

Across the session, the validators surfaced:
- A model assignment error (coder on Sonnet instead of Opus) — invisible to human review
- 13 structural inconsistencies in escalation paths — would have caused silent failures at runtime
- A documentation omission pattern (ux-designer described as reactive-only) — technically not incorrect, therefore easy to miss

None of these were caught by the human during the session; all were caught by the automated validators. This supports the research thesis that risk-stratified automated review routes human attention to the right places rather than requiring exhaustive human review.

### 3. Chat history is an unreliable artifact

Several times during the session, a decision made earlier was not reflected in later files. The ux-designer two-mode decision was recorded in the agent file but not propagated to two documentation files. The solution — `decisions.md` and `doc-patterns.md` — acknowledges that chat is ephemeral and that any decision worth preserving must be written to tracked files.

This has a direct implication for organizations: AI-assisted development sessions must include explicit "capture" rituals for decisions, just as the prompt-capture rule in AGENTS.md requires capturing code generation prompts. The discipline is the same: the conversation is not an artifact; what's committed to git is.

### 4. The recursion is productive

Applying the framework to its own development files revealed gaps that weren't visible when building application code. The escalation-path dead ends (items 1–8 in the Phase 2 findings) existed because the framework was designed for application code review, and the framework agents themselves were never subjected to the same level of consistency checking. Running the validators against the framework files closed this loop.

### 5. Agent count drift is a leading indicator of documentation health

The agent count inconsistency (17/23/25/16 across different files) is a proxy metric for how well documentation has tracked implementation. When the count is inconsistent, it means some documentation was written at one point in time and not updated when agents were added. The static checker now enforces consistency.

---

## Artifacts produced this session

| Artifact | Type | Purpose |
|---|---|---|
| `.claude/agents/ux-designer.md` | New agent | Design authority — proactive audit + reactive gap-filling |
| `.claude/agents/framework-validator.md` | New agent | Runs full validation suite before committing |
| `.claude/agents/framework-setup-validator.md` | New agent | Verifies framework installation is complete |
| `.claude/agents/doc-validator.md` | New agent | Catches documentation omission class bugs |
| `.claude/agents/spec-compliance-validator.md` | New agent | Checks pipeline satisfies governance spec |
| `.claude/agents/post-change-sweep.md` | New agent | Orchestrates all reviews after any change |
| `.claude/agents/prompt-fidelity.md` | New agent stub | Semantic prompt-vs-code comparison (designed; pending full implementation) |
| `scripts/framework/check_agents_static.sh` | New script | Phase 1: structural validation, no AI |
| `scripts/framework/validate_agents.sh` | New script | Phase 2: agy + codex semantic review |
| `scripts/framework/validate_docs.sh` | New script | Phase 3: documentation coverage review |
| `scripts/framework/validate_spec_compliance.sh` | New script | Phase 4: governance requirements compliance |
| `scripts/framework/run_framework_validation.sh` | New script | Top-level runner for all 4 phases |
| `scripts/framework/run_post_change_sweep.sh` | New script | Diff categorizer and agent routing planner |
| `scripts/framework/install.sh` | New script | Interactive framework installer for new projects |
| `scripts/framework/config.sh` | Generated config | Project-specific values (CondoParkShare) |
| `scripts/framework/decisions.md` | New knowledge base | Durable record of design decisions with verification criteria |
| `scripts/framework/doc-patterns.md` | New knowledge base | Known documentation bug patterns for recurrence detection |
| `docs/SETUP.md` | New doc | Step-by-step installation guide |
| `docs/CUSTOMIZATION.md` | New doc | What to customize and how |
| Updated: 8 agent files | Modified | Escalation path fixes, model corrections, compliance gaps |
| Updated: 4 doc files | Modified | Reflect all new agents and scripts |
| `.github/pull_request_template.md` | Modified | AI submission attribution section |
| `AGENTS.md` (root) | Modified | Pull request attribution rule |

All changes committed to branch `build`, PR #11 open for human review.
