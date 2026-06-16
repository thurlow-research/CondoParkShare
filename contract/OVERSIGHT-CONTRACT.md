# Oversight Contract v1

This document defines what any compliant agent team must produce for the Human Oversight System (HOS) to evaluate it. The HOS oversight agents (evaluator, orchestrator, risk-assessor) program against this contract, not against any specific team's agent names or file paths.

---

## 1. Filesystem protocol

All files live relative to the project root. The HOS reads these locations; compliant teams must write to them.

```
audit/                               ← COMMITTED to project repo (not gitignored)
  oversight-log.jsonl               ← append-only audit trail; one JSON event per line
  step-{N}-summary.md               ← human-readable per-step report (generated at merge)
  escaped-defects.md                ← consolidated escaped-defect record

.claudetmp/                          ← ephemeral working state (gitignored)
  signoffs/
    step{N}-register.md          ← sign-off register for build step N
  reviews/
    {agent-role}-{step}-{ts}.md  ← iteration state per reviewer per step
  tests/
    unit-test-{step}-{ts}.md     ← unit test iteration state
    system-test-{step}-{ts}.md   ← system test iteration state
  design/
    architect-{step}-{ts}.md     ← architect design critique state
    technical-design-{step}-{ts}.md
  oversight/
    validators/
      summary.json               ← composite risk score (written by run_validators.sh)
      {dimension}.json           ← per-validator output
      risk-assessment.md         ← validated risk tier + inspection brief (written by
                                    risk-assessor agent, read by oversight-evaluator)
    step{N}-evaluation-{ts}.md   ← oversight-evaluator output
    step{N}-panel-context.md     ← external panel context (structural risk signals ONLY —
                                    no internal findings, no resolved vulnerabilities)
    step{N}-handoff.md           ← human/PR context (full picture: internal review
                                    summary, second review findings, human notes)
    step{N}-human-authorization.md ← CRITICAL steps only: human creates this file
                                    BEFORE running oversight-evaluator to authorize
                                    proceeding. Evaluator reads it during Phase 1.
                                    Required content: date + decision text.
    human-tier-override.md       ← created by human to authorize lowering a risk tier
                                    below the coder's declaration. Risk-assessor reads
                                    this before accepting a lower tier. Without it the
                                    declared tier is a hard floor.
  notifications/
    step{N}/
      {from}-to-{to}-{ts}.md     ← durable inter-agent notification artifact.
                                    Required fields:
                                      Step: N
                                      From: {agent-name}
                                      To: {agent-name}
                                      Changed: [list of files or spec sections changed]
                                      Reason: [why the receiving agent needs to act]
                                      Blocking: yes | no
                                      Required action: [what the receiving agent must do]
                                      Acknowledged: [left blank; receiving agent fills in]
                                    Use when: one agent changes a shared artifact
                                    (design pack, telemetry spec) that another agent
                                    must re-review. Prevents notifications from being
                                    lost in chat context.
  second-review/
    step{N}-{ts}.md              ← second review output (run_second_review.sh).
                                    Always written — for actual runs includes a
                                    machine-readable header then reviewer JSON blocks;
                                    contains verdict: skipped when below thresholds or
                                    when no diff content. Top-level fields:
                                      verdict: approve|request_changes|error|skipped|unparseable
                                      highest_severity: critical|high|medium|low|none
                                      unresolved_findings: N
  red-team/
    checkpoint-{milestone}-{ts}.md ← red-team report
```

---

## 2. Self-flag format (authoring agents)

Any agent that produces code at MEDIUM risk or above must emit:

```
RISK: LOW | MEDIUM | HIGH | CRITICAL
CONFIDENCE: [0-100]% — [one sentence: what you're confident about and what you're not]
```

For destructive operations (migrations, auth changes, schema drops):
```
BLAST RADIUS: [what breaks if this is wrong]
Rollback: [how to undo]
```

For MEDIUM+ changes:
```
## Human Review Required
**[file:line]** — [why this needs human eyes, not just what it is]
```

Git commit trailers (every AI-generated commit):
```
Prompt-Artifact: [path to technical design section or prompts/ file]
AI-Model: [model id]
AI-Risk: [LOW|MEDIUM|HIGH|CRITICAL]
```

### 2a. Structural-override signatures (force `structural`, independently enforced)

Authoring agents that fill gaps (`ux-designer`, `ops-designer`, `pm-agent`, …) classify each change `clarifying` / `additive` / `structural`. Only `structural` escalates to a human; `clarifying` and `additive` are applied autonomously. Because the actor being governed is also the classifier, the following **mechanical signatures force `structural` regardless of the agent's own assessment** — and the `oversight-evaluator` re-derives them from the diff (`scripts/oversight/change_classifier.py`), so mislabeling cannot bypass the human gate:

| Signature | What it is |
|---|---|
| **new-external-dependency** | A new entry in any dependency manifest (`requirements*.txt`, `pyproject.toml`, `package.json`, `go.mod`, `Gemfile`, `Cargo.toml`, …) |
| **new-permission-or-auth-state** | A new authentication/authorization check (`permission_required`, `login_required`, `IsAuthenticated`, `has_perm`, `PermissionDenied`, role guards, auth mixins, …) |
| **new-user-facing-surface** | A template/component added as a **new file** (`templates/**/*.html`, `*.jsx/tsx/vue/svelte`) |
| **new-user-flow-or-route** | A new route/URL (`path(`, `re_path(`, `@app.route`, router registrations, `<Route>`) |
| **new-user-facing-state** | A new user-visible state enum (`choices=`, `TextChoices`/`IntegerChoices`, state-machine fields/transitions, `STATUS_*` constants) |

An authoring agent must classify a change matching any of these as `structural` and obtain human authorization before applying it. The signature set is a **floor**: it is deliberately biased to over-detect (a false positive merely sends a benign change to a human; a false negative is the only real failure). Projects with a known stack may **extend** the signatures in `change_classifier.py` but must not narrow this base set. Enforcement runs **only in the loosening direction** — see §7; a change the agent already classified `structural` (or a step that cleared a human gate) is not re-checked.

**Residual coverage gap (do not over-claim).** These signatures detect structural changes that **add** a new artifact (dependency, auth check, route, surface, state). They do **not** detect a structural change that **modifies existing behavior** without adding a signature — altering an existing flow's completion criterion, widening an existing permission's scope, changing established gate logic. Per the full taxonomy, "changes existing behavior" is structural, but the mechanical re-derivation cannot see it. Those changes rely on honest self-classification plus reviewer/panel detection. Agent prompts must not tell authors "it will always be caught" — only signature-bearing additions are mechanically guaranteed.

---

## 3. Sign-off register schema

### Register header (commit range)

The first lines of `.claudetmp/signoffs/step{N}-register.md` record the commit range the step covers, so the oversight-evaluator knows exactly which commits to check (e.g. for prompt-artifact compliance) without guessing:

```markdown
# Sign-off Register — Step {N}
base_sha: {SHA the step started from — previous step's head_sha, or merge-base with the default branch}
head_sha: {current HEAD when the evaluator runs}
```

The oversight-evaluator writes/updates this header when it runs. `base_sha` is taken from the previous step's recorded `head_sha` (via the audit log) or, for the first step, the merge-base of the current branch with the default branch. The definitive commit range for the step is `git log base_sha..head_sha`. This range also feeds the reactive re-run mechanism so each step's commits are unambiguous.

### Sign-off entries

Every agent that approves or escalates a build step writes one entry below the header:

```markdown
## {role} | {artifact} | {ISO-8601 datetime}
Status: APPROVED | ESCALATED | CONDITIONAL | N/A
Agent: {agent-name}
Artifact: {what was reviewed — file paths or description}
Iterations: {N}
Critical_findings_resolved: true | false | N/A
Human_resolution: {ISO date} — {decision text}   ← required when Status: ESCALATED
Notes: {one paragraph: what was found and how resolved. Empty if clean.}
```

**Required fields:** Status, Agent, Artifact, Iterations.

**`Critical_findings_resolved`** is required for `security` and `privacy` roles; optional (N/A) for all others. When `true`, it signals that the evaluator should add the finding to the conditional-items list for human review before merge — it is a Phase 2 quality signal, not a Phase 1 compliance check.

**`Human_resolution`** is required only when `Status: ESCALATED`. Format: `{ISO date} — {decision text}`. The oversight-evaluator reads this field to confirm human resolution is on record before clearing the compliance check. Example: `Human_resolution: 2026-06-11 — Reviewed 5-round loop; architect decision is sound, proceed`.

**`Status: CONDITIONAL`** passes Phase 1 compliance but automatically causes the oversight-evaluator to recommend at least `CONDITIONAL_PROCEED` — a human must verify the conditional item before merge.

**`Status: N/A`** passes Phase 1 compliance and means the role had no applicable changes in this diff (the reviewer's domain was not touched). Written by `post-change-sweep` on behalf of skipped reviewers (with a `Reason:` field), or by `code-reviewer` itself when there is no application code to review. An explicit N/A entry distinguishes "considered, not applicable" from a missing entry — see `research/findings/explicit-na-audit-entries.md`. Each N/A entry corresponds to a `gate-na` audit event (§6a).

**Test agents additionally write the §4 test declaration fields** (Coverage_pct, Mutant_score_pct, Thresholds_met, All_passing) inline in their register entry. The oversight-evaluator reads these fields directly from the register; §4 defines the schema those fields must follow.

**Roles** (the HOS checks for these roles, not agent names):

| Role key | Meaning |
|---|---|
| `code-review` | Correctness, design adherence, idioms |
| `security` | Security vulnerabilities, OWASP |
| `privacy` | PII handling, GDPR compliance |
| `test-unit` | Unit test coverage + mutant score |
| `test-system` | Functional spec conformance |
| `process` | PM sign-off on test plan or spec section |
| `infra` | Infrastructure / deployment config |
| `ui` | Design system / template conformance |
| `a11y` | Accessibility (WCAG AA) |
| `ops` | Observability / telemetry spec conformance (optional — projects with ops complexity) |
| `reliability` | Resilience review — timeouts, retry, graceful degradation (optional — projects with external connections) |
| `risk-assessment` | Risk tier validation — note: risk-assessor writes to `.claudetmp/oversight/validators/risk-assessment.md`, NOT to the sign-off register. Do not include `risk-assessment` in `required_signoffs` — it is a validator artifact, not a sign-off role. |

**Documentation currency (required for any step that modifies documented behavior):** The relevant spec, design doc, AGENTS.md entry, or METHODOLOGY.md section must reflect what was built before the step is signed off. A step whose observable behavior differs from its documentation is not done — it is broken. "I'll update the docs later" is not a valid sign-off state.

**Gate suspension (brownfield remediation):**
A project may temporarily suspend specific gates/roles during brownfield onboarding by creating `contract/gate-suspension.md` (see `contract/gate-suspension.template.md`). Suspended gates exit 0 instead of blocking; suspended sign-off roles are treated as WAIVED by the oversight-evaluator. The suspension file:
- Must be created by a human (agents may not create or modify it)
- Must include `Authorized by:` and `Date:` fields
- Lists suspended gates as `SUSPENDED: {role-name}` lines
- Is committed to git — the suspension is auditable

Gate script names for suspension: `lint`, `security`, `secrets`, `types`, `template-refs`, `portability`, `django`
Sign-off role names match `required_signoffs` in `step-manifest.yaml`

---

## 4. Test declaration schema

Test agents append a declaration to the sign-off register:

```markdown
## test-unit | test-results | {datetime}
Status: APPROVED | ESCALATED
Agent: unit-test
Coverage_pct: 83
Mutant_score_pct: 77
Thresholds_met: true
Surviving_equivalents: 2
Equivalents_documented: true
Iterations: 3
Notes: ...
```

```markdown
## test-system | test-results | {datetime}
Status: APPROVED | ESCALATED
Agent: system-test
Spec_flows_covered: [list of §11 flows covered]
All_passing: true
Iterations: 2
Notes: ...
```

---

## 5. Step manifest schema

Each project provides a step manifest at `contract/step-manifest.yaml` (copy from `contract/step-manifest.template.yaml`). The oversight-evaluator reads this to know what is required for each step.

```yaml
contract_version: "1"
project: my-project
role_mappings:
  code-review:    code-reviewer      # agent name → role key
  security:       security-reviewer
  privacy:        privacy-reviewer
  test-unit:      unit-test
  test-system:    system-test
  process:        pm-agent
  infra:          infra-reviewer

steps:
  - id: 1
    name: "Scaffold"
    risk_tier: LOW
    required_signoffs: [code-review, infra]
    system_test_applicable: false

  - id: 3
    name: "Auth + TOTP"
    risk_tier: CRITICAL
    required_signoffs: [code-review, security, privacy, test-unit, test-system, process]
    system_test_applicable: true
    human_gate_required: true          # CRITICAL: human must authorize before PR opens
```

---

## 6. Issue creation rules

Compliant agents create GitHub issues at defined trigger points. Issue creation is enforced by agent instructions and is auditable via `gh issue list`. The evaluator does NOT query GitHub to verify issue existence — it trusts the sign-off register which agents update after creating issues. The issue trail is a research and audit artifact, not a blocking compliance check.

### 6.0 Fix-in-place vs. file-an-issue (the fixer triage)

Any agent that **both detects and can correct** problems — the `coder` in the inner loop, `doc-validator`, and the framework-validation fixers — applies the same triage when it finds an issue. This is one boundary instantiated in many places (the coder's inner loop, the self-review capped-iterate protocol, `doc-validator`'s loop-exit, the change-type classification):

- **Mechanical / local / unambiguous → fix in place, do not file an issue.** The correct fix is determined by an authoritative source and is a local correction: a typo, a path/reference mismatch, a missing required field, a stale capability claim, a numbering or format error, a doc made faithful to the agent definition it describes. Apply it directly and re-run the check; iterate until clean. **No issue** — issues feed risk scoring (`risk-historian` density), so filing mechanical fixes is noise. This is the inner loop.
- **Structural / design / judgment → open an issue and escalate, do not paper over it.** The finding reveals a design contradiction, a governance change, an ambiguous decision, a missing capability or permission, or anything whose fix is not a local mechanical correction. File a GitHub issue (design findings are real risk; they must feed risk scoring and reach a human or another agent) and stop. Do not disguise a structural gap with a mechanical edit.
- **Direction guard (the ratchet).** A fix-in-place may only correct *toward* the authoritative source, and may **never** loosen governance (relax a gate, lower a tier, weaken a control) or edit an authoritative spec/governance artifact to match a downstream doc. Those are structural by definition → issue. (Example: if a doc and an agent definition disagree, the doc is corrected to match the definition — never the reverse, unless a human decides the definition was wrong.)
- **Cap (don't loop).** Bounded fix-and-rerun cycles, default 3. If the same class of finding recurs past the cap, stop and escalate to a human with what was tried — never loop indefinitely.

See `research/findings/fixer-triage-inner-loop-boundary.md`.

**AI issue title convention:** Every issue created by an AI agent must begin with `[AI: {agent-name}]`:

```
[AI: {agent-name}] {issue-type}: {description}
```

**AI issue footer convention:** Every AI-created issue must include this footer at the end of the body:

```markdown
---
*🤖 Created by `{agent-name}` | Step: {N or "session"} | Branch: `{branch}` | {YYYY-MM-DD}*
```

Both requirements apply to all projects that install this framework.

| Trigger | Label(s) | Who creates | Example title prefix |
|---|---|---|---|
| Spec silent/ambiguous → human escalation | `spec-gap` | pm-agent or equivalent | `[AI: spec-red-team] spec-gap:` |
| Design loop → 5-round escalation | `design-concern` | architect or equivalent | `[AI: architect] design-concern:` |
| Unit test loop exhausted | `test-resistance` | unit-test agent | `[AI: unit-test] test-resistance:` |
| System test fails after fix iterations | `bug` | system-test agent | `[AI: system-test] bug:` |
| Security finding (crit/high) resolved pre-merge | `security-finding`, `resolved-in-review` | security reviewer | `[AI: security-reviewer] security-finding:` |
| Privacy finding (blocking) resolved pre-merge | `privacy-finding`, `resolved-in-review` | privacy reviewer | `[AI: privacy-reviewer] privacy-finding:` |
| Second review finds crit/high | `second-review-finding` | run_second_review.sh | `[AI: second-review/agy] second-review-finding:` |
| Panel finds something internal team missed | `escaped-defect` | panel arbiter | `[AI: panel-arbiter] escaped-defect:` |
| Red-team finds exploitable issue | `red-team-finding` | run_red_team.sh | `[AI: red-team/codex] red-team-finding:` |
| Startup artifact missing a case | `startup-artifact-gap` | any downstream agent | `[AI: ui-reviewer] startup-artifact-gap:` |

---

## 6a. Audit-log event catalog

`audit/oversight-log.jsonl` is an append-only log, one JSON object per line, each with an `"event"` field and an ISO-8601 `"timestamp"`. The catalog below is canonical — every line written to the log uses one of these event types. The log distinguishes the states that an absent register entry otherwise conflates (ran-and-clean vs. never-ran vs. intentionally-skipped vs. failed).

| Event | Meaning | Emitted by | Key fields |
|---|---|---|---|
| `step-head` | Records a step's HEAD SHA so the next step finds its base | oversight-evaluator | `step`, `head_sha` |
| `human-authorization` | A human authorization gate was satisfied — pins the content hash, decision, and claimed authorizer into committed history | oversight-evaluator | `step`, `artifact`, `content_sha256`, `authorized_by`, `decision` |
| `validator-failure` | A validator/gate exhausted retries (timeout or crash) | run_with_retry.sh | `validator`, `required`, `attempts`, `final_outcome` (failed\|skipped), `last_error` |
| `gate-suspended` | A required role/gate was waived because it is suspended | oversight-evaluator | `gate`, `step`, `authorized_by`, `suspension_file` |
| `gate-na` | An orchestrator determined a reviewer is not applicable to the diff | post-change-sweep | `gate`, `step`, `reason`, `determined_by` |
| `gate-rerun` | A step was re-run because one of its inputs changed | reactive re-run mechanism | `gate`, `step`, `trigger`, `previous_run` |
| `gate-auto-reenabled` | A suspended gate was auto-removed after consistent passes | suspension auto-removal | `gate`, `step`, `consecutive_passes` |
| `suspension-census` | Per-run count of active suspensions (health metric) | oversight-evaluator | `active_suspensions`, `suspended_gates` |
| `sampling-audit` | A statistical sampling red-team run completed | run_redteam_sample.sh | `pool_size`, `sample_size`, `tier_escapes`, `escape_rate_pct` |
| `na-invalidated` | An independent re-derivation rejected a `Status: N/A` waiver because the role's domain was in fact changed | oversight-evaluator | `role`, `step`, `evidence` |
| `structural-override` | A structural-override signature was detected in a change not labeled `structural` (a self-classification escape, caught pre-PR) | oversight-evaluator | `signal`, `step`, `file`, `covered` (bool) |
| `hos-prune` | A file removed from the framework during an install/upgrade was archived (provenance + content hash recorded) | hos_install.sh | `file`, `archived_to`, `release`, `sha256` |
| `pr-bounced` | Overseer returned PR to worker for register/completeness gaps; PR left open, assigned to worker, not a task failure | overseer (`record_pr_bounce`) | `pr`, `cid`, `bounce_number`, `failures` (check_id list), `assigned_to`, `repo`, `timestamp` |

**Why this matters (ratchet + audit completeness):** the three "non-APPROVED" states — `gate-suspended` (human chose to skip), `gate-na` (not applicable), `validator-failure` (tried and failed) — are genuinely different and currently invisible if not logged. A complete audit trail records all of them. Note the ratchet: `gate-suspended` requires a human (`authorized_by`); `gate-auto-reenabled` does not (re-enabling is the safe direction). See `research/findings/ratchet-principle.md` and `research/findings/explicit-na-audit-entries.md`.

---

## 7. Compliance check

The `oversight-evaluator` agent checks compliance before quality evaluation. Compliance fails if:

1. Sign-off register is missing or has no entries for a required role
2. Any required role entry is missing required §3 fields (`Status`, `Agent`, `Artifact`, `Iterations`)
3. Any required role shows `Status: ESCALATED` without a `Human_resolution:` field in that entry
4. `test-unit` declaration is missing `Thresholds_met: true`
5. `test-system` declaration is missing when `system_test_applicable: true`
6. `process` sign-off missing when `system_test_applicable: true` (PM must sign off on test plan)
7. **Effective human gate** (`manifest.human_gate_required == true` **OR** validated tier == CRITICAL): `.claudetmp/oversight/step{N}-human-authorization.md` must exist and be non-empty BEFORE the evaluator runs; if missing, compliance fails immediately. The requirement is **re-derived from the validated tier**, not trusted from the manifest flag — `risk-assessor` ratchets the tier but nothing ratchets the flag, so a re-derived-CRITICAL step with `human_gate_required: false` must still hit the gate (same anti-gaming principle as conditions 9–10).
7a. `.claudetmp/oversight/validators/risk-assessment.md` must exist and establish a validated tier on every per-step build evaluation. If absent → **COMPLIANCE FAIL** (the validated tier is a required input; the evaluator cannot substitute for risk-assessor's deterministic floor, required-reviewers set, prompt-fidelity, dep-mapper, or risk-historian — failing closed is the safe direction). A fallback to `max(manifest risk_tier, MEDIUM)` is permitted **only** under an explicit human authorization artifact for brownfield/emergency use; without it, absence is a hard fail. An undetermined tier may never silently downgrade the tier-gated checks (second-review, prompt-artifact, conditions 9–10) to no-ops.
7b. **Risk-assessment scope + blocking findings (#204)** — when `risk-assessment.md` is present, its header must record `base_sha`/`head_sha`/`files_assessed` and a `blocking_findings:` list. (i) If its `base_sha..head_sha` does not equal the register header's range, or `files_assessed` omits files the step diff changed → **COMPLIANCE FAIL** (the assessment was scoped to a different — possibly empty — file set, since a post-commit `git diff HEAD` is empty; the validated tier and required-reviewers set are therefore untrustworthy). (ii) Any `blocking_findings` entry with `resolution: unresolved` (or `escalated:` naming an absent/empty human artifact) → **COMPLIANCE FAIL**. This gives a blocking finding (e.g. non-suspended dep-mapper `Data confidence: LOW` at HIGH+) the consumer it previously lacked — before #204 it was recorded nowhere and could not stop the PR. Same anti-gaming shape as 9–10: a self-reported value that gates oversight is verified against the diff/register, not trusted blind.
8. MEDIUM+ commits missing `Prompt-Artifact:` git trailer → **COMPLIANCE WARN** (not hard fail — add to conditional items; human confirms intent was captured another way). If the trailer references a path that does not exist → **COMPLIANCE FAIL**
9. A `Status: N/A` entry for a role whose domain the diff actually touched (independent re-derivation via `change_classifier.py`, scoped to the N/A'd roles) → **COMPLIANCE FAIL** (the waiver is not credible; require a real sign-off). See §2a and the evaluator's "Independent diff re-derivation."
10. A structural-override signature (§2a) present in the diff with no covering human-authorization artifact → **COMPLIANCE FAIL** (a change that forces `structural` reached pre-PR without the human gate). Checked **only in the loosening direction**: skipped when the step already cleared a human gate or the change was already classified `structural` with authorization.

Conditions 9–10 are **anti-gaming** checks: they re-derive — independently of the actor that produced the change — the two determinations that can loosen oversight (an N/A waiver, an `additive`/`clarifying` self-label). They never run when upstream asked for *more* review; spending verification cost only in the loosening direction is itself the ratchet.

**Pre-merge bounce (overseer):** The overseer re-checks conditions 1–3 and 7b before applying the merge-authority matrix. Failure returns the PR to the worker as a `pr-bounced` event; the per-cid failure counter is untouched. The worker's pre-PR gate (REQ-W-15) checks the same conditions one step earlier as the primary enforcement point; the overseer bounce is the backstop.

**MEDIUM fail-closed (second review):** a MEDIUM+ step cannot proceed without an actual cross-vendor judgment. `run_second_review.sh` exits non-zero in every case where the review did not produce one:
- a fired vendor is **unavailable at pre-check** (agy at MEDIUM+, or both vendors at HIGH+) — the original guard;
- a fired-and-required vendor **errors at runtime** (timeout, rate-limit, crash after the CLI passed pre-check) → aggregate `verdict: error`, exit non-zero. A runtime error must not collapse into `approve`; that would silently convert the mandatory independent review into a PASS (a fail-open).

A fifth verdict, **`unparseable`**, is distinct from `error`: the reviewer *ran and produced a real review* the harness could not auto-structure (e.g. an agentic CLI returned a narrated markdown report instead of strict JSON — HOS#113). The review content exists and is preserved in the output file. `unparseable` must **NOT** be collapsed into `error` (fail-closed — throws away a real independent review) or `approve` (silent pass). `run_second_review.sh` exits 0 on `unparseable` with a loud "a human must read this" notice rather than fail-closed.

The `oversight-evaluator` enforces these independently (a MEDIUM+ second-review file with `verdict: error` or `verdict: skipped` → COMPLIANCE FAIL; `verdict: unparseable` → CONDITIONAL_PROCEED, with a conditional item requiring a human to read the preserved report and confirm its verdict — never COMPLIANCE FAIL and never silent PASS), so the guarantee holds even if the script is bypassed. Document this in project runbooks.

Compliance failure → `ESCALATE` regardless of content evaluation.

---

## 8. Extension points

The contract is versioned (`contract_version`). Extensions:
- Add new role keys (backward compatible — old teams just won't have them, evaluator notes missing)
- Add new required sign-offs to the step manifest
- Add new issue labels (issue_query.py queries by label)
- Add new validator dimensions (uniform output schema, register in run_validators.sh)

Breaking changes (increment contract version):
- Changing the sign-off register file path or format
- Changing required field names in the schema
- Changing the filesystem protocol locations
