# Known Documentation Bug Patterns

This file records documentation omission patterns discovered during development sessions.
It is read by `validate_docs.sh` to give the AI reviewers explicit pattern guidance —
filling the role that chat history would otherwise play.

**When to add an entry:** When a doc bug is found and fixed during a session, add the
pattern here so future validation runs actively look for recurrences. One entry per
pattern; keep descriptions concrete and reusable.

**Format:**
```
## PATTERN-NNN: [short name]
**Type:** mode-omission | pipeline-omission | stale | scope-narrowing | role-boundary
**Discovered:** YYYY-MM-DD
**Example of the bug:** [exact quote or paraphrase of the broken text]
**Why it's wrong:** [what the agent file actually says]
**Correct form:** [what the doc should say]
**Check:** [what to grep for, or what question to ask, to detect recurrences]
```

---

## PATTERN-001: ux-designer described as reactive-only

**Type:** mode-omission
**Discovered:** 2026-06-12
**Example of the bug:** "Not a step in the pipeline; invoked reactively on demand. When `ui-reviewer` or `a11y-reviewer` finds a design pack gap..."
**Why it's wrong:** `ux-designer` has two modes: (1) proactive — invoked after `pm-agent` at project start to audit the design pack against the full spec and write `docs/design/UX-DESIGN-READINESS.md`; (2) reactive — fills gaps during the build. Any description that only mentions the reactive mode is incomplete.
**Correct form:** Any description of when to invoke ux-designer must cover both modes. Short form: "invoked at project start (proactive audit) and reactively during the build (gap filling)."
**Check:** Search for "ux-designer" in doc files. Any sentence containing "reactive" or "on demand" or "when ui-reviewer" that does NOT also mention "project start" or "initial audit" is suspect.

---

## PATTERN-002: pm-agent described only as a during-build resource

**Type:** mode-omission
**Discovered:** 2026-06-12
**Example of the bug:** "Invoke whenever any agent needs a product/requirements question answered."
**Why it's wrong:** `pm-agent` also has a mandatory project-start role: reads all spec files, surfaces ambiguities, conducts human Q&A, and writes `docs/pm/CONFIRMED-REQUIREMENTS.md`. This is a blocking sequential step, not just a reactive resource.
**Correct form:** Descriptions of pm-agent should mention both: (1) mandatory first step at project start, and (2) reactive spec-question answering during the build.
**Check:** Any description of pm-agent that describes it only as "invoked when..." without mentioning "at project start" or "initial spec review" is suspect.

---
<!-- Add new patterns below this line -->
