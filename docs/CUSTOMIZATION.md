# Customizing the Agent Pipeline Framework

This guide covers what to change when you apply this framework to your own project, what to leave alone, and how to validate your changes before committing.

For installation steps, see `docs/SETUP.md`.

---

## The rule: change content, not structure

The framework's value is in its **structure** — the escalation chains, the dependency ordering, the change-classification taxonomy (clarifying/additive/structural), the validation suite. These should not change between projects.

What changes per-project is **content**: file paths, tech stack specifics, domain knowledge, design system details.

**Safe to change:**
- Spec file paths in agent prompts
- Tech stack references (Django → Rails, HTMX → React, etc.)
- Deployment host/URL details
- Design pack file paths and rules
- `config.sh` values
- The project-specific parts of `pm-agent`, `architect`, `coder`, `infra-reviewer`, `deploy-verify`

**Leave alone:**
- Escalation chains and dependency ordering between agents
- The clarifying/additive/structural change taxonomy
- Loop-exit rules (5-round limits, temp-state files)
- The framework scripts themselves (`check_agents_static.sh`, `validate_agents.sh`, etc.)
- Agent roles and responsibilities

After any change, run `bash scripts/framework/run_framework_validation.sh` to confirm you haven't broken escalation paths.

---

## Changing the tech stack

These agents contain the most Django/HTMX specifics:

### `coder.md`

Replace:
- `docs/design/TECHNICAL-DESIGN.md` build order reference → your project's equivalent
- Django conventions block (ORM managers, migrations, HTMX partials, Argon2) → your stack's idioms
- Build order list (§12 of SPEC-1) → your project's build phases

Keep:
- The security invariants (tenant isolation, PII, CSRF)
- The review chain sequence
- Escalation paths

### `technical-design.md`

Replace:
- GiST exclusion constraint → your equivalent DB constraint mechanism
- `tstzrange` → your datetime/range field approach
- Django ORM scoping strategy → your framework's equivalent
- HTMX partial boundaries → your rendering approach
- `pytest-django`/`mutmut` references → your test tooling

Keep:
- The 10-section outline structure (models, multi-tenant, URLs, views/forms, algorithms, etc.) — this structure is universally applicable
- The iteration protocol with architect
- Escalation paths

### `code-reviewer.md`

Replace:
- Django-specific checks (GiST in migration, `select_for_update`, ORM manager scoping, HTMX response detection) → your framework's equivalents

Keep:
- Every finding format requirement (file/line, severity, description, change required)
- The architectural dispute escalation path

### `unit-test.md`

Replace:
- `pytest-django`, `coverage`, `mutmut`, `factory_boy`, `freezegun` → your stack's test runner, coverage tool, mutation testing tool, factories, time mocking
- The 7 priority test areas → yours (booking gate logic, horizon metric, etc. are CondoParkShare-specific)

Keep:
- The 80% coverage / 75% mutant score targets — these are the framework's quality floor
- The loop-exit rule (5 rounds → escalate to technical-design)

### `system-test.md`

Replace:
- Django test client → your integration test approach
- The 12 primary flows from SPEC-1 §11 → your spec's primary flows

Keep:
- The "tests are based on spec, not code" principle
- The failure routing (code bug → coder; spec gap → pm-agent)

### `infra-reviewer.md` and `deploy-verify.md`

These are the most project-specific agents. Replace all deployment details:
- URLs, hostnames, service names
- Compose service names (if not using Compose, replace entirely)
- Caddy → your reverse proxy
- Backup paths and retention policy
- Browser smoke test URLs and assertions

---

## Changing the design system

If your project has a different design system (or none):

### `ux-designer.md`
- Update all four design pack file paths (DESIGN.md, tokens.css, style-guide.html, feedback-states.html)
- Update `config.sh` → `DESIGN_PACK_PATH` to your main design doc
- If you have no design system, ux-designer will create one from scratch during the initial audit — leave the agent largely unchanged and let it produce the initial pack

### `ui-reviewer.md`
- Replace the CondoParkShare-specific token rules (meadow/clay semantics, `.bay` motif, Spline Sans Mono rules) with your design system's rules
- Keep the format: blockers vs. suggestions, file/line, rule citation
- Keep the escalation to ux-designer for gaps (not architect, not human)

### `a11y-reviewer.md`
- The WCAG AA checks are universal — keep them
- Update contrast ratio checks to reference your actual tokens
- Keep the escalation to ux-designer for new token requests

---

## Adding a new agent

When your project needs a domain not covered by the existing agents (e.g., a data-pipeline reviewer, a mobile-specific reviewer, an ML model reviewer):

1. **Create the agent file** in `.claude/agents/your-agent.md`:
   ```markdown
   ---
   name: your-agent
   description: One sentence: when to invoke, what it reviews. Include "Invoked by X; escalates to Y."
   model: claude-sonnet-4-6
   tools:
     - Read
     - Bash
     - Grep
     - Glob
   ---

   You are the [role] for [project]. [One paragraph: what you do and what you don't do.]

   ## What you check
   [Concrete checklist]

   ## Output format
   [Finding structure]

   ## Iteration
   - Send all findings in one pass.
   - Loop exit: after 5 rounds without approval, escalate to [agent/human].
   - Temp state: write to `.claudetmp/reviews/your-agent-{step}-{YYYYMMDDTHHMMSS}.md`.

   ## Escalation
   - [Situation] → [target]
   ```

2. **Wire it into the pipeline** — update the relevant existing agent to invoke yours:
   - If it runs after code-reviewer: add it to `coder.md`'s post-review chain
   - If it handles escalations from another agent: update that agent's escalation section
   - Add it to `post-change-sweep.md`'s domain routing table

3. **Update `docs/AGENTS.md`**:
   - Add a new numbered section for the agent
   - Add it to the escalation map
   - Add it to the pipeline diagram if it's a pipeline stage

4. **Run validation**:
   ```bash
   bash scripts/framework/run_framework_validation.sh
   ```
   The static checker will verify the new agent's name resolves in any file that references it.

---

## Modifying escalation paths

Escalation paths are the load-bearing structure of the pipeline. Change them carefully.

**Before changing any escalation path**, ask:
- Does the new target agent exist and have the tools to handle the escalation?
- Does changing this create a loop? (A → B → A)
- Does removing this leave a dead end? (A escalates to nothing for this scenario)

**After changing**, the static checker will catch:
- Broken escalation targets (agent name doesn't resolve)
- The AI review (agy) will flag loops and dead ends

A loop is a hard error. A dead end where nothing handles a class of problem is a hard error. Always run the full validation after touching escalation chains.

---

## Modifying the change-classification taxonomy

`pm-agent` uses clarifying/additive/structural for spec changes.
`ux-designer` uses the same taxonomy for design pack changes.
`post-change-sweep` and `framework-validator` use it for routing decisions.

If you add agents that make decisions about changes (e.g., a data-schema manager that classifies schema changes), use the same three-tier taxonomy for consistency. The human escalation gate should only trigger for **structural** changes.

Do not change the taxonomy itself — it is shared across agents and the classification logic in the framework scripts uses it.

---

## Updating `config.sh`

`config.sh` is the only file you should edit directly to change project-specific values (or re-run `install.sh`).

```bash
# Re-run install.sh to update config interactively:
bash scripts/framework/install.sh
# It reads existing values and only prompts for new/changed fields.
```

Never add project-specific values to `check_agents_static.sh` or `validate_agents.sh` directly — those scripts are generic framework code. If you need to suppress a false positive in the static checker, add the token to `PROJECT_NON_AGENT_TOKENS` in `config.sh`:

```bash
# config.sh
PROJECT_NON_AGENT_TOKENS="myserver|mydb|my-service-name"
```

---

## Keeping up with framework updates

When the framework source repo (CondoParkShare) releases updates:

```bash
# Pull updates from the source into your project:
bash scripts/framework/install.sh \
  --source /path/to/CondoParkShare \
  --target .

# install.sh will:
# - Update the framework scripts (always — these are framework infrastructure)
# - SKIP agent files you have already created (never overwrites customizations)
# - Preserve your config.sh values
# - Prompt only for new config fields introduced in the update
```

After updating, run the full validation to confirm nothing was broken:

```bash
bash scripts/framework/run_framework_validation.sh
```

---

## Validation quick reference

| When | Command |
|---|---|
| Before committing any change | `bash scripts/framework/run_post_change_sweep.sh` then invoke `post-change-sweep` agent |
| Before committing agent/doc changes | `bash scripts/framework/run_framework_validation.sh` |
| Quick structural check only | `bash scripts/framework/run_framework_validation.sh --static-only` |
| After installing in a new repo | Invoke `framework-setup-validator` agent |
| Troubleshooting a broken setup | Invoke `framework-setup-validator` agent |
