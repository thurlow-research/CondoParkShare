# CondoParkShare — Release Plan & Triage Guide

This document defines the milestone structure and the rules the worker uses to
triage issues into releases. It is the companion to the per-issue priority/domain
labels: **labels describe severity; milestones describe scheduling.**

> **Worker rule (recap of `.claude/agents/worker.md`):** only pick up issues
> assigned to the **active milestone**. When that milestone's backlog is
> exhausted, stop and report — do not range into future milestones without
> explicit human authorization.

The active milestone is currently **0.2.0**.

---

## Grouping principle: priority **and** theme

Releases are carved by a **combination of priority and thematic relatedness**,
not by priority alone:

1. **Priority sets the tier.** P0 → earliest, then P1, then P2/ops.
2. **Theme can pull an item forward.** An issue critical to a release's *theme*
   belongs in that release even if its severity label is lower. The clearest
   case: **0.2.0's theme is "it deploys and a human can test it,"** so every
   deploy-blocker lands in 0.2.0 regardless of whether it is labelled P0, P1, or P2.
3. **Theme groups within a tier.** Within a release, related issues (privacy,
   correctness, reliability, deploy/infra, ops) are worked together.

When a release's theme and an issue's priority disagree, **the theme wins for
scheduling** and the label still records the true severity.

---

## Releases

### 0.2.0 — Minimum Deployable Product  *(active)*
**Theme:** the first build that **deploys to PPE and a human can exercise it**.

**Exit criteria — 0.2.0 is done when all of these hold:**
- The app **deploys to PPE** (faberix) behind Nexus TLS/Caddy per ADR-002 — no
  direct `0.0.0.0` gunicorn exposure.
- It **boots cleanly** (no settings/system-check failures) and **serves static
  assets** (styled pages), with DB-readiness and migrate-before-serve handled.
- A human can **log in** (first-run admin bootstrap exists) and exercise the
  core flows.
- Outbound dependencies (email, web-push) **can't hang the app** (timeouts), and
  **blocked/erased accounts cannot access** the system.
- The **inner-loop baseline test suite is green** (the worker's gate passes).

**Scope:** P0 blockers + every deploy-blocker (any priority) + the pipeline-green
test fixes. Deliberately *excludes* anything not required to stand up a testable
instance (backups, PII-scrubbing refinements, edge-case reliability, monitoring).

### 0.3.0 — Security, privacy & correctness hardening
**Theme:** the substantive risk-reduction work, including defects a human will
surface while testing 0.2.0.
**Scope:** P1 (and thematically-related P2) defects across privacy, correctness,
reliability, plus backups (deferred from 0.2.0 — not test-blocking, complicates
setup). Exit: no open P1 security/privacy/correctness defects; backups functional
and scheduled.

### 0.4.0 — Ops, monitoring & day-2
**Theme:** operability and the observability tail.
**Scope:** P2 hardening, monitoring/alerting, audit-log backup, backup tooling
migration, framework validator sweeps. Exit: the system is observable and
operable without manual babysitting.

---

## Triaging a new issue

1. **Severity label** — `P0` (breaks prod / critical security), `P1`
   (security/privacy/correctness), `P2` (medium). Add domain labels: `security`,
   `privacy`, `correctness`, `reliability`, `infra`, `ui`, `a11y`, `ux`, `docs`.
   Add `bug` for defects, `enhancement` for new capability.
2. **Milestone** — apply the grouping principle:
   - **Does it block deploy or human testing of the current build?** → **0.2.0**,
     regardless of severity.
   - Otherwise **P0/P1 security·privacy·correctness·reliability** → **0.3.0**.
   - Otherwise **P2 / ops / monitoring / day-2 / framework** → **0.4.0**.
   - A `needs-architect` or `needs-human` item is spread across releases so a
     human/architect gate never stalls an entire release.
3. **Never rename a milestone title.** The worker cron resolves its target by
   **exact title match** (`HOS_TARGET_RELEASE` → milestone title). Titles must
   stay bare versions (`0.2.0`, `0.3.0`, …); put theme/scope in the milestone
   *description*, not the title.

---

## Housekeeping notes

- **Closed historical milestones:** Phase 1–5 (the prior domain-ordered
  structure) are 100% complete and have been closed. The version milestones
  (0.2.0+) supersede them.
- **Duplicate labels to consolidate (do not delete without checking HOS
  automation that may match the exact strings):** `needs_human`
  ("queued for human review") vs `needs-human` ("awaiting a human decision").
  Pick one canonical spelling and migrate, then update any tooling that
  references the other.

## Related

- Environment/bootstrap gaps that this plan's 0.2.0 deploy items depend on are
  tracked upstream in HOS: `thurlow-research/HumanOversightSystem#953` (venv
  preflight), `#954` (tmp-leak cleanup), `#956` (end-to-end build-env bootstrap).
- CPS pipeline-green test fixes for 0.2.0: #173, #174, #175.
