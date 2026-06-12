# Framework Design Decisions

This file records architectural and design decisions made during development sessions.
It gives the spec-compliance validator a durable record of intent that can be checked
against actual implementation — filling the role that chat history would otherwise play.

**When to add an entry:** When a decision is made in a session that:
- Changes how an agent behaves or is invoked
- Establishes a new rule, constraint, or taxonomy
- Resolves an ambiguity that was previously implicit
- Supersedes a previous decision

Write the entry before the session ends. A decision not written here is invisible to
future validation.

**Format:**
```
## DEC-NNN: [title]
**Date:** YYYY-MM-DD
**Status:** implemented | pending | superseded
**Decision:** [what was decided]
**Rationale:** [why — the reason that makes this non-obvious]
**Implemented in:** [files where this decision manifests]
**Verification:** [what the spec-compliance validator should check to confirm implementation]
```

---

## DEC-001: ux-designer invoked proactively at project start AND reactively during build

**Date:** 2026-06-12
**Status:** implemented
**Decision:** ux-designer has two operating modes: (1) proactive — invoked after pm-agent completes Q&A, before architect, to audit the design pack against the full spec and produce `docs/design/UX-DESIGN-READINESS.md`; (2) reactive — invoked by coder, ui-reviewer, a11y-reviewer, technical-design during the build to fill design pack gaps.
**Rationale:** Without a proactive audit, the design pack would have gaps at build-time that would cause repeated reactive invocations. Running it once at project start makes the design pack complete before any code is written, so the build can proceed without design bottlenecks.
**Implemented in:** `.claude/agents/ux-designer.md`, `docs/AGENTS.md` (§9 and pipeline overview), `docs/OVERSIGHT-RUNBOOK.md` (Project Start Sequence and Phase 3 note)
**Verification:** ux-designer.md must contain both "Initial design audit" and reactive invocation sections. docs/AGENTS.md pipeline diagram must show ux-designer in the START phase (step 2). Any description of ux-designer must mention both modes.

---

## DEC-002: pm-agent has a mandatory project-start role, not just a reactive resource

**Date:** 2026-06-12
**Status:** implemented
**Decision:** pm-agent is the first agent invoked at project start — reads all spec files, surfaces ambiguities, conducts human Q&A, and writes `docs/pm/CONFIRMED-REQUIREMENTS.md`. This is a blocking sequential step. It also answers product questions reactively during the build, but the project-start role is mandatory and sequential.
**Rationale:** If pm-agent is only described as a reactive resource, agents that need the confirmed requirements document may proceed without it, producing designs and code that miss spec requirements.
**Implemented in:** `.claude/agents/pm-agent.md`, `docs/AGENTS.md` (§1 and pipeline overview), `docs/OVERSIGHT-RUNBOOK.md` (Project Start Sequence)
**Verification:** pm-agent.md must contain an "Initial spec review" section. docs/AGENTS.md pipeline diagram must show pm-agent as step 1 in START. Any description of pm-agent must mention the project-start role.

---

## DEC-003: ux-designer is a 4th authority tier, peer to pm-agent and architect within its domain

**Date:** 2026-06-12
**Status:** implemented
**Decision:** The framework has four authority tiers: Human → Architect (technical) → PM (product) → UX Designer (design). ux-designer can extend the design pack for additive/clarifying changes without human approval. Only structural brand changes (core palette, typeface, brief) require human escalation.
**Rationale:** Without a design authority tier, every design gap escalated to human, blocking the pipeline for decisions that are well within the design system's defined constraints.
**Implemented in:** `.claude/agents/ux-designer.md`, `docs/AGENTS.md` (design principles table)
**Verification:** docs/AGENTS.md authority tier table must list ux-designer as the 4th tier. ux-designer.md must define the three-tier change classification (clarifying/additive/structural) with human escalation only for structural.

---

## DEC-004: clarifying/additive/structural change taxonomy is shared across pm-agent and ux-designer

**Date:** 2026-06-12
**Status:** implemented
**Decision:** Both pm-agent (for spec changes) and ux-designer (for design pack changes) use the same three-tier classification: clarifying (no behavior change), additive (new requirement/pattern), structural (changes existing behavior/identity). Human escalation only for structural.
**Rationale:** Consistent taxonomy lets agents reason about change risk the same way across domains, and makes the human escalation gate predictable.
**Implemented in:** `.claude/agents/pm-agent.md`, `.claude/agents/ux-designer.md`, `docs/AGENTS.md` (§1 pm-agent spec update path, §9 ux-designer change classification)
**Verification:** Both pm-agent.md and ux-designer.md must contain a clarifying/additive/structural table. The human escalation condition must be "structural" in both.

---

## DEC-005: framework scripts are project-agnostic; all project-specific config lives in config.sh

**Date:** 2026-06-12
**Status:** implemented
**Decision:** check_agents_static.sh, validate_agents.sh, validate_docs.sh, validate_spec_compliance.sh, and run_framework_validation.sh contain no project-specific values (no hostnames, project names, stack names, or domain terms). All such values live in scripts/framework/config.sh, which is generated by install.sh and sourced automatically.
**Rationale:** Framework scripts copied to a new project must work without modification. Baking project-specific values into scripts means every project maintains a fork.
**Implemented in:** `scripts/framework/config.sh`, all `scripts/framework/*.sh` scripts
**Verification:** grep for 'CondoParkShare', 'opus.kumajyo', 'Django', 'HTMX' in any framework script — must return zero matches.

---

## DEC-006: framework-validator, framework-setup-validator, post-change-sweep, doc-validator, spec-compliance-validator are copied to all projects

**Date:** 2026-06-12
**Status:** implemented
**Decision:** All framework agents (the five named above) are copied to every project that uses this framework. Any project will customize agents, and these validators catch issues with those customizations.
**Rationale:** Without these agents in a target project, there is no validation loop after customization — the framework is applied once and never re-validated.
**Implemented in:** `scripts/framework/install.sh` (copies all .claude/agents/*.md)
**Verification:** install.sh must copy .claude/agents/*.md without exclusion. All five framework agent files must be present in .claude/agents/.

---

## DEC-007: cross-vendor independence constraint — Claude models must not be the independent reviewer

**Date:** 2026-06-12 (from METHODOLOGY.md)
**Status:** implemented
**Decision:** Claude (Opus/Sonnet/Haiku) is the author. For independent review, only non-Claude models count: agy (Gemini) for correctness/completeness, codex (OpenAI) for adversarial/security. Same-vendor review correlates errors. Claude Sonnet is the arbiter (synthesizes others' votes) but never an independent reviewer.
**Rationale:** An AI reviewing its own family of models tends to ratify its own mistakes. Independence requires vendor diversity.
**Implemented in:** `scripts/run_second_review.sh`, `scripts/run_panel.sh`, all `scripts/framework/validate_*.sh` scripts
**Verification:** No validate_*.sh script may invoke `claude` CLI for the independent review step. All AI review prompts must be sent to agy or codex, not to a Claude model.

---

## DEC-008: decisions.md and doc-patterns.md are the durable replacements for chat history

**Date:** 2026-06-12
**Status:** implemented
**Decision:** Chat history is ephemeral and not accessible to validators. Any decision or pattern that should inform future validation must be written to decisions.md (architectural decisions) or doc-patterns.md (documentation bug patterns) before the session ends. These files are read by validate_docs.sh and validate_spec_compliance.sh.
**Rationale:** Without durable capture, the same class of bug recurs in every new session because validators have no memory of prior sessions.
**Implemented in:** `scripts/framework/decisions.md`, `scripts/framework/doc-patterns.md`, `scripts/framework/validate_docs.sh`, `scripts/framework/validate_spec_compliance.sh`
**Verification:** Both files must be sourced by their respective validator scripts. validate_docs.sh must reference doc-patterns.md. validate_spec_compliance.sh must reference decisions.md.
