# CondoParkShare — Agent Pipeline

*Last updated: June 2026. Applies to the Spec 1 pilot build.*

This document describes the multi-agent pipeline used to build CondoParkShare. It covers each agent's role, model, escalation paths, and the full pipeline sequence. It is written to allow recreation in another Claude Code environment.

---

## Design principles

The pipeline is organized around a single rule: **each agent owns one concern and escalates everything else**. No agent makes decisions outside its domain; disputes travel up a defined chain until they reach the right authority. This prevents agents from silently making product, architecture, or policy decisions that belong to a human or a specialized agent.

Three tiers of authority:

| Tier | Who | Decides |
|---|---|---|
| Human | You | Product vision, policy, unresolvable disputes |
| Architect | `architect` agent | All technical/architectural decisions |
| PM | `pm-agent` | All product/requirements decisions |

Every other agent operates within the bounds set by these three.

---

## Pipeline overview

```
START
  1. pm-agent      — spec review, surface ambiguities, human Q&A
  2. architect     — technical feasibility review, human Q&A (after PM)

DESIGN
  3. technical-design ↔ architect  — iterate until design approved

PER FEATURE
  4. coder
       ↓
  5. code-reviewer
       ↓ approved
  6. security-reviewer  ─┐
  7. privacy-reviewer   ─┤ parallel
  8. ui-reviewer        ─┤
  9. a11y-reviewer      ─┤
 10. infra-reviewer     ─┘ (infra files only)
       ↓ all approved
 11. unit-test      — 80% coverage + 75% mutant score
       ↓ targets met
 12. system-test    — spec functional validation

DEPLOY
 13. deploy-verify  — infra checks + browser smoke tests against live prod
```

---

## Agents

### 1. `pm-agent` — Product Manager

**Model:** `claude-sonnet-4-6`
**Invoked:** At project start (first agent); anytime a product/requirements question arises during build.

**Role:** Owns the spec. Answers "what should the product do?" questions. Never answers implementation or architecture questions.

**At project start:**
Reads all five spec files (`SPEC.md`, `SPEC-1-pilot.md`, `SPEC-2-subscriptions.md`, `SPEC-3-exchange-economy.md`, `DESIGN.md`) and surfaces every ambiguity, gap, or underspecified behavior in a single numbered list to the human. Does not proceed until the human has answered. The confirmed answers become a requirements supplement that feeds the architect.

**During build:**
Answers product questions from `technical-design`, `unit-test`, and `system-test` agents, citing the spec section. If the spec is silent, escalates to the human with a precise single question.

**Spec update path:**
When build discoveries or human decisions require the spec to be amended, `pm-agent` classifies the change and applies it:

| Change type | Definition | Process |
|---|---|---|
| Clarifying | Adds precision without changing behavior | Update spec directly; notify architect and technical-design |
| Additive | New requirement not previously covered | Update spec; notify architect and technical-design |
| Structural | Changes existing behavior or scope | Draft the change, present to human for approval **before** writing |

Never updates the spec to rationalize code that doesn't meet the original spec — that is a spec falsification.

**Escalation out:** Human (spec silent or structural change required).
**Escalation in:** From `technical-design`, `unit-test`, `system-test`.

---

### 2. `architect` — System Architect

**Model:** `claude-opus-4-8`
**Invoked:** At project start (after `pm-agent` completes Q&A); as final escalation for technical disputes.

**Role:** Makes all architecture and technical decisions. Decisions are binding and final. All other agents operate within the bounds the architect sets.

**At project start:**
Reads the spec and the PM's confirmed requirements. Identifies technical risks and open decisions: GiST exclusion constraint design, availability computation strategy, earned-horizon calculation placement, multi-tenant ORM scoping, PII encryption library, TOTP storage, web push architecture, Django admin extension strategy, Docker/Caddy networking. Asks the human any questions in a single list. After receiving answers, writes an Architecture Decision Record (ADR) to `docs/architecture/ADR-001-pilot.md`. This ADR is the input for `technical-design`.

**Design critique loop:**
Reviews every draft of the technical design document. Critiques harshly and specifically — "this is fine" is not acceptable output. Names specific failure modes and what must change. Iterates with `technical-design` until the design is sound.

**Dispute arbitration:**
When escalated disputes arrive from `coder`, `code-reviewer`, `security-reviewer`, or `technical-design`: makes a decision, states it clearly, names which agent must change course. If the dispute is actually a product question, redirects to `pm-agent`.

**Escalation out:** Human (unresolvable after architect, or product/policy decisions).
**Escalation in:** From `technical-design`, `coder`, `code-reviewer`, `security-reviewer`, `privacy-reviewer`, `a11y-reviewer`, `ui-reviewer`.

---

### 3. `technical-design` — Technical Design

**Model:** `claude-opus-4-8`
**Invoked:** After architect completes initial ADR; when coder has design questions.

**Role:** Translates the product spec and architectural decisions into a detailed technical specification that a coder can implement without ambiguity. Does not write application code — writes the spec for it.

**Produces:** `docs/design/TECHNICAL-DESIGN.md`, covering:
- Django model field names, types, constraints, and indexes — including GiST exclusion constraint DDL
- Multi-tenant ORM scoping strategy (custom managers, middleware)
- URL structure (`urlpatterns` skeleton for every view)
- View and form contracts (name, methods, auth requirement, HTMX vs. full-page — no implementation)
- Availability computation algorithm (exact query/ORM equivalent)
- Earned-horizon metric algorithm (only elapsed past hours, 180-day rolling window)
- TOTP and recovery code flow
- Notification dispatch architecture
- Admin surface design (Django admin extension vs. custom views)
- Right-to-erasure cascade

**Iteration:** Submits drafts to `architect` for critique. Does not release the design to the coder until architect approves.

**During build:** Answers coder's design questions. If a question reveals a gap, updates `TECHNICAL-DESIGN.md` and notifies the architect.

**Escalation out:** `architect` (design disputes, architectural questions); `pm-agent` (product questions).
**Escalation in:** From `coder` (design questions), `unit-test` (untestable designs).

---

### 4. `coder` — Implementation

**Model:** `claude-sonnet-4-6`
**Invoked:** After `technical-design` is architect-approved; iteratively per feature.

**Role:** Writes production Django code. Follows `TECHNICAL-DESIGN.md` and the ADR. Does not decide what to build.

**Process:**
1. Reads the relevant section of `TECHNICAL-DESIGN.md` before writing.
2. Batches all questions for a section and asks `technical-design` before writing — not mid-implementation.
3. Writes code following the spec's build order (§12 of SPEC-1).
4. Submits to `code-reviewer`. Once code-reviewer approves, `security-reviewer` and `privacy-reviewer` run in parallel. Does not mark a section complete until all reviewers have approved.

**Key invariants enforced in code:**
- Every ORM query through a tenant-scoped manager — no raw cross-tenant queries.
- `select_for_update()` around booking creation.
- Every privileged admin action writes an `AdminAuditLog` entry.
- No PII in logs. No secrets in source. All hex colors via CSS tokens only.

**Escalation out:** `technical-design` (design questions); `architect` (disputes with reviewers); `pm-agent` via `technical-design` (product questions).
**Escalation in:** From `code-reviewer`, `security-reviewer`, `privacy-reviewer`, `unit-test`, `system-test`.

---

### 5. `code-reviewer` — Code Review

**Model:** `claude-sonnet-4-6`
**Invoked:** After each coder pass.

**Role:** Reviews Django code for correctness, design adherence, and quality. Does not cover security or privacy — those are separate agents.

**Checks:**
- Implementation matches `TECHNICAL-DESIGN.md` exactly (names every deviation)
- GiST exclusion constraint present in migration, not just asserted in model
- Availability computation and horizon metric are correct
- One-active-booking gate correctly defined
- Bookings are hour-aligned
- Every ORM query that touches tenant data goes through the scoped manager
- Django admin views are tenant-scoped
- No premature abstractions; no dead code; no hard-coded config values
- HTMX responses return partials for `HX-Request`; full pages for direct navigation

**Output:** Every finding includes file/line, severity (`blocking` or `suggestion`), what is wrong, and what it must change to. Sends all findings in one pass. Explicit approval statement when no blocking issues.

**Escalation out:** `technical-design` (design disputes); `architect` (architecture disputes).
**Escalation in:** From `coder`.

---

### 6. `security-reviewer` — Security Review

**Model:** `claude-sonnet-4-6`
**Invoked:** After `code-reviewer` approves (in parallel with `privacy-reviewer`).

**Threat model:** A registered resident attacking other residents or escalating privileges; an HOA admin attacking another tenant; an unauthenticated external attacker.

**Checks:**
- TOTP verified on every view requiring 2FA, not just at login; rate-limited
- Recovery code consumption is atomic (cannot be used twice under concurrent requests)
- Session invalidated on logout, password change, and account block; no session fixation
- Login form does not reveal whether an email exists
- Invite tokens and recovery codes use `secrets.token_urlsafe()`, not `random`
- Every view verifies `instance.organization == request.user.organization` (IDOR prevention)
- Operator console unreachable by non-superusers
- No raw SQL with string formatting; no `|safe` on user-controlled data
- CSRF middleware active; HTMX requests include CSRF token
- No secrets in source, templates, or logs
- `DEBUG = False`, `ALLOWED_HOSTS` restrictive, security headers set
- TOTP secret stored encrypted per ADR; time window tolerance ≤ ±1 step

**Output:** Each finding includes severity (critical/high/medium/low), CWE class, file/function, attack scenario, and specific remediation.

**Escalation out:** `architect` (architectural security flaws); `pm-agent` (security policy questions); human (unresolvable).
**Escalation in:** From `coder` (re-review after fixes).

---

### 7. `privacy-reviewer` — Privacy & GDPR

**Model:** `claude-sonnet-4-6`
**Invoked:** After `code-reviewer` approves (in parallel with `security-reviewer`).

**Applicable framework:** GDPR (target EU hosting; possible EU data subjects in pilot).
**Core principle from spec:** "Hash what you only verify; encrypt what you must read back; minimize collection."

**PII inventory reviewed:**

| Data | Required handling |
|---|---|
| Email | Volume encryption at rest; TLS in transit |
| Display name | Volume encryption at rest |
| Phone | Field-encrypted (reversible); optional |
| Password | Argon2 one-way hash; never recoverable |
| TOTP secret | Encrypted per ADR |
| Recovery codes | Hashed after generation; shown once only |

**Checks:**
- Phone field is field-encrypted, not just volume-encrypted
- No PII field is hashed instead of encrypted (breaks read-back)
- Encryption key from environment; key rotation path exists
- No PII fields beyond those the spec defines
- `delete_user_pii()` function scrubs email/name/phone, anonymizes booking references, deletes TOTP and recovery codes, logs erasure in audit log
- Consent/lawful-basis notice shown before account creation
- Any admin view rendering resident PII writes an `AdminAuditLog` entry
- No PII in log output; `DEBUG = False` in production

**Escalation out:** `pm-agent` (data collection scope); `architect` (encryption architecture); human (retention policy).
**Escalation in:** From `coder` (re-review after fixes).

---

### 8. `ui-reviewer` — UI & Design Conformance

**Model:** `claude-sonnet-4-6`
**Invoked:** After `code-reviewer` approves.

**Role:** Verifies Django templates faithfully implement the design pack (`DESIGN.md` + `tokens.css`). Not visual taste — spec compliance.

**Checks:**
- No hard-coded hex values; all colors via `var(--token)` or provided classes
- `--meadow` and `--clay` not used decoratively — only for availability state signals
- Spline Sans Mono (`.mono`, `.spot-id`, `.data`) appears **only** on: spot IDs, time windows, permit-like values — not headings, body copy, or navigation
- One `.btn-primary` per view maximum
- `.badge-available` and `.badge-booked` include text labels, not color only
- `.bay` motif used only for: available spot framing, empty states, or logo — not as generic borders
- Voice/tone: plain active labels ("Book this spot", not "Submit booking request"); sentence case; no "monetize", "asset", "module", "leverage"
- Error messages explain what to do next ("No spots open then. Try a wider window.")
- Empty states invite action ("List the first spot in your building.")

**Escalation out:** Human (design intent ambiguity); `coder` (implementation bugs); `architect` (tokens.css changes).
**Escalation in:** From `coder` (re-review after fixes).

---

### 9. `a11y-reviewer` — Accessibility

**Model:** `claude-sonnet-4-6`
**Invoked:** After `code-reviewer` approves.

**Compliance target:** WCAG 2.1 AA. Treats the design pack's quality floor as a build gate: keyboard focus, color never the only signal, `prefers-reduced-motion`, mobile responsiveness, WCAG AA contrast.

**Audit approach:** Lighthouse audit via Chrome DevTools MCP on each primary view (if dev server is running); plus static template analysis (grep for missing `alt`, unlabeled inputs, `tabindex="-1"` on interactive elements) in all cases.

**Key checks:**
- Every interactive element reachable by Tab in logical order
- Focus ring visible on every focused element; not overridden anywhere
- `.badge-available` / `.badge-booked` have text labels, not color only
- `--meadow-ink` (not `--meadow`) used for colored text on light backgrounds; same for clay
- `--slate` on `--canvas` meets 4.5:1 contrast ratio
- No animations outside `@media (prefers-reduced-motion: reduce)` guard
- Every `<input>` has a programmatic `<label>` (not just placeholder)
- Error messages associated via `aria-describedby`
- Touch targets ≥ 44×44px; no horizontal scroll at 375px viewport

**Escalation out:** Human (design system decisions); `coder` (implementation bugs).
**Escalation in:** From `coder` (re-review after fixes).

---

### 10. `infra-reviewer` — Infrastructure Review

**Model:** `claude-sonnet-4-6`
**Invoked:** After `code-reviewer` approves (when infrastructure files are modified: Compose, Caddyfile, backup scripts, `.env.example`).

**Role:** Reviews deployment configuration against the spec's §2 deployment requirements. Does not review application code.

**Checks:**
- All three services present (`web`, `db`, `caddy`); all with `restart: unless-stopped`
- DB port **not** published to host; DB on internal network only
- Postgres data on a **named volume**, not a host-mount path
- No secrets in `environment:` blocks; all via `.env` / `${VAR}` references
- Caddy: canonical domain via DNS-01; HOA alias via HTTP-01; no `tls internal`
- Both canonical and HOA alias in `ALLOWED_HOSTS`
- `.env.example` contains all required variables; `DEBUG` defaults to `False`; `DATABASE_URL` uses internal service name
- `pg_dump` backup script exists; output to NAS/external volume; retention policy present
- Portability: can the stack move to a new host by copying `.env` + restoring `pg_dump` + repointing CNAME?

**Escalation out:** `architect` (architecture decisions); human (deployment policy).
**Escalation in:** From `coder`, `deploy-verify` (infra failures post-deploy).

---

### 11. `unit-test` — Unit Tests

**Model:** `claude-sonnet-4-6`
**Invoked:** After all reviewers (`code-reviewer`, `security-reviewer`, `privacy-reviewer`, `ui-reviewer`, `a11y-reviewer`, `infra-reviewer`) have approved.

**Gates (both must be met before advancing):**
- Code coverage ≥ 80% (`coverage run` + `coverage report`)
- Mutant score ≥ 75% killed (`mutmut run` — Python mutation testing)

**Priority test areas:**
1. **Booking gate logic** — all three gates tested at boundaries (horizon, one-active-booking, DB overlap constraint triggered directly)
2. **Earned-horizon metric** — elapsed hours only, 180-day window, formula, cold-start grace, zero-history baseline
3. **Availability computation** — window splitting, clipping, fully-booked window
4. **Model constraints** — hour-aligned bookings, duration cap, organization scoping
5. **Auth flows** — TOTP valid/invalid/expired/reused; recovery code single-use; invite token single-use/expiry
6. **Right-to-erasure** — all PII scrubbed, bookings anonymized, codes deleted
7. **Admin audit log** — every privileged action writes exactly one entry with all required fields

**Tooling:** `pytest-django`, `coverage`, `mutmut`, `factory_boy`, `freezegun` (for time-dependent tests).

**Escalation out:** `technical-design` (untestable designs); `pm-agent` (spec ambiguities); `architect` (coder refuses testability refactor).
**Escalation in:** From `coder` (fixes that re-run tests).

---

### 12. `system-test` — System & Functional Tests

**Model:** `claude-sonnet-4-6`
**Invoked:** After `unit-test` meets both targets.

**Role:** Validates the application meets the spec's functional requirements. Tests are based on the spec, not the code. Uses Django test client (not Selenium) against a real test database.

**Covers every primary flow from SPEC-1 §11:**
- Full booking flow: search → horizon gate → one-active-booking gate → overlap gate → confirm → notifications
- Listing flow: availability window creation, elapsed hours accumulation (with `freezegun`)
- Cancellation/release: borrower pre-start, early release, owner-cancel with penalty
- Onboarding Mode A (invite): single-use link, TOTP enrollment, recovery codes
- Onboarding Mode B (approve): pending → approved → active
- Authentication: TOTP required; recovery code consumption; locked-out sessions
- Earned-horizon advancement: baseline, cold-start grace, formula verification
- HOA portal tenant isolation: cannot see another building's residents
- Operator console: full cross-tenant access; HOA admin cannot reach it
- Right-to-erasure: PII scrubbed, bookings anonymized, audit log entry
- Admin audit log: admin-cancel, PII access, block/unblock all logged

**When a test fails:**
- Code bug (code doesn't match design) → report to `coder` with test name, expected vs. actual, spec citation
- Spec gap → escalate to `pm-agent` with the two possible interpretations and which the test assumes

**Escalation out:** `pm-agent` (spec interpretation) → human (if unresolvable); `coder` (code bugs).
**Escalation in:** From `coder` (fixes).

---

### 13. `deploy-verify` — Deployment Verification & Production Smoke Tests

**Model:** `claude-sonnet-4-6`
**Invoked:** After `docker compose up` on `opus.kumajyo.com`.

**Role:** Verifies the production instance is correctly configured and functionally operational. Last gate before announcing a deployment successful.

**Phase 1 — Infrastructure:**
Remote checks (SSH to `parkshare-agent@opus.kumajyo.com`): Docker services up and healthy, backup file exists and is recent (< 48h old).
Local checks (run from wherever Claude Code is): DNS resolution for canonical URL and HOA alias, TLS certificate valid and not expiring within 30 days, HTTP security headers present (`Strict-Transport-Security`, `X-Frame-Options`, `X-Content-Type-Options`, `Content-Security-Policy`), DB port 5432 not reachable externally, HTTP → HTTPS redirect working.

Requires three environment variables in `.env`: `AGENT_SSH_KEY` (path to `parkshare-agent` private key), `AGENT_COMPOSE_PATH` (path to compose file on opus), `AGENT_BACKUP_DIR` (path to backup directory on opus).

**Phase 2 — Browser smoke tests (Chrome DevTools MCP):**
1. App loads; login form present; no console errors
2. Hanken Grotesk font loaded; `tokens.css` loaded (`--pine` CSS variable defined)
3. Invalid login returns error state, not 500 or Django debug page
4. HOA alias redirects to HTTPS without certificate error
5. PWA manifest served as valid JSON with required fields
6. `tokens.css` static file returns 200
7. Django admin login page loads

**Phase 3 — Backup verification:**
- Backup cron is registered
- At least one backup file exists and is non-zero

**Output:** Structured pass/fail table per check, overall PASS/FAIL, and specific remediation steps for any failures.

**Escalation out:** `infra-reviewer` + human immediately (infrastructure failures); `coder` + `system-test` (functional failures); human immediately (missing backups — deployment is not complete without verified backup).

---

## Escalation map

```
Human
  ├── pm-agent          (product decisions, structural spec changes)
  │     └── receives from: technical-design, unit-test, system-test
  └── architect         (technical decisions, final arbiter)
        └── receives from: technical-design, coder, code-reviewer,
                           security-reviewer, privacy-reviewer,
                           a11y-reviewer, unit-test

technical-design
  ├── escalates to:  architect (technical), pm-agent (product)
  └── receives from: coder, unit-test

coder
  ├── escalates to:  technical-design (design questions),
  │                  architect (disputes with reviewers)
  └── receives from: code-reviewer, security-reviewer, privacy-reviewer,
                     ui-reviewer, a11y-reviewer, unit-test, system-test

deploy-verify
  ├── escalates to:  infra-reviewer (infra failures),
  │                  coder (functional failures),
  │                  human (missing backups, unresolvable)
  └── triggered by:  human (after docker compose up)
```

---

## Recreating in another Claude Code environment

### Requirements

- Claude Code CLI or desktop app
- A project git repository with the spec files in `Specs/`

### Steps

1. **Create the agents directory:**
   ```
   mkdir -p .claude/agents
   ```

2. **Copy all agent files** from `CondoParkShare/.claude/agents/` into the new project's `.claude/agents/` directory. Each file is a self-contained Markdown file with YAML frontmatter.

3. **Agent file format** — each file follows this structure:
   ```markdown
   ---
   name: agent-name
   description: When to invoke this agent (used for routing)
   model: model-id
   tools:
     - Read
     - Write
     - ...
   ---

   System prompt content
   ```

4. **Available model IDs** (as of June 2026):
   - `claude-opus-4-8` — Opus (most capable; use for architect and technical-design)
   - `claude-sonnet-4-6` — Sonnet (strong reasoning; use for pm-agent and all reviewer/test agents)
   - `claude-haiku-4-5-20251001` — Haiku (fast and cheap; suitable only for pure retrieval/lookup agents with no judgment calls)

5. **Invoking agents:**
   - In Claude Code, type `@agent-name` to invoke a specific agent, or describe what you need and Claude Code will suggest the appropriate agent based on each agent's `description` field.
   - Agents are invoked by the orchestrating session — they are not autonomous background processes.

6. **Update spec file paths** — each agent's system prompt references paths relative to the project root (e.g. `Specs/SPEC-1-pilot.md`). If the new project has a different spec location, update the path references in each agent file.

7. **Update project-specific context** — the following agents contain CondoParkShare-specific context that must be updated for a new project:
   - `pm-agent` — spec file list and pilot scope
   - `architect` — stack, deployment host, ADR output path
   - `technical-design` — model list, design document output path
   - `coder` — build order, stack conventions
   - `infra-reviewer` — canonical URL, deployment host details
   - `deploy-verify` — canonical URL, HOA alias, backup paths

### Adapting for a different tech stack

The agent *roles* and *escalation paths* are stack-agnostic. To adapt for a different stack (e.g. Rails, Next.js, FastAPI):

- `technical-design`: replace Django-specific items (GiST constraints, ORM managers, HTMX partials) with the equivalent for your stack
- `coder`: replace Django conventions with your framework's idioms
- `code-reviewer`: replace Django-specific checks (managers, migrations, signal handlers) with your framework's equivalents
- `security-reviewer` and `privacy-reviewer`: the checks are largely framework-agnostic; only the "how to fix" details change
- `unit-test`: replace `pytest-django`/`mutmut` with your stack's test runner and mutation testing tool
- `system-test`: replace Django test client with your stack's integration test approach
- `deploy-verify`: update infra checks and smoke test URLs for the new deployment target
- `infra-reviewer`: update Compose/Caddy checks if the infra stack changes
- `a11y-reviewer` and `ui-reviewer`: these are largely stack-agnostic (HTML/CSS output); update template file paths

The `pm-agent` and `architect` agents require minimal changes for a new project — update the spec file paths and stack description in the architect's system prompt.
