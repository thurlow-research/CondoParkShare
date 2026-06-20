# CondoParkShare — Session Start Brief

Read this first. Every session. Treat it as the project's institutional memory.

HOS handles auth, git identity, install, and protocol mechanics. This document covers
only what you need to know about *this project specifically* — decisions made, current
state, what's been built, what hasn't, and where things live.

---

## What this project is

**CondoParkShare** is a multi-tenant Django SaaS for HOA condo parking. Residents share
parking spots; HOA admins approve users, manage spots, and control availability. It is
simultaneously a real production product and a living experiment in AI-assisted
development under HOS oversight.

**Current release:** v0.1.0 — the initial pilot, deployed to PPE (faberix). Production
(opus) is not yet live.

---

## Architecture decisions that affect your work

### Authentication flow (ADR-001)
TOTP is mandatory for all users. The enrollment flow is: register →
`pending_approval` (awaiting HOA approval) → HOA approves → `pending_totp`
(enrolls TOTP) → `active`. Status values: `pending_totp`, `pending_approval`,
`active`, `blocked`. Never set `status=active` on a `pending_approval` user — this
was a P0 security bug (#17, fixed).

TOTP secrets are stored encrypted at rest in `EncryptedTOTPDevice` (MTI subclass
of django-otp's `TOTPDevice`), using the same Fernet key as `User.phone`.

### Multi-tenancy
Every model is scoped to an `Organization`. The tenant is determined by the request
domain. `TenantMiddleware` sets `request.organization`. Never query across orgs
except in the operator console (superuser only, all orgs, always audited).

### Booking system
Auto-assignment — residents specify a time window, the system assigns a spot by
owner-rotation. There is no browse-and-pick. Buffer between bookings is
`organization.booking_buffer_hours` (default 1, per-tenant, live-read — was
hardcoded until #84).

### Audit subsystem
`AdminAuditLog` records all operator actions. `audit_healthcheck` management command
is a liveness probe that writes synthetic probe rows to `AuditProbe` and emits
Prometheus metrics. The audit recovery log is a fail-closed JSONL sink — if the DB
write path fails, records land here for later backfill.

### Encryption
- `User.phone`: field-encrypted with `EncryptedCharField` (Fernet, `PII_ENCRYPTION_KEY`)
- `EncryptedTOTPDevice.encrypted_key`: same key, same library
- Backups: `pg_dump | gzip | age -r $BACKUP_ENCRYPTION_RECIPIENT` before writing to NAS

### Reverse proxy / TLS
Caddy runs on **nexus** (Windows, 192.168.1.5) and terminates TLS for all domains.
Django never sees HTTPS — `SECURE_SSL_REDIRECT = False`. Django trusts
`X-Forwarded-Proto: https` (set by Caddy) via `SECURE_PROXY_SSL_HEADER`. If you see
SSL redirect loops, that setting is the culprit.

---

## Deploy topology

| Host | IP | Role |
|---|---|---|
| nexus | 192.168.1.5 | Windows — runs Caddy, terminates TLS |
| faberix | 192.168.1.12 | PPE — tracks `ppe` branch |
| opus | 192.168.1.11 | Production — tracks `prod` branch (not yet live) |

PPE is the staging environment. **All deployment issues must be reproduced and fixed
on faberix before touching opus.** If it breaks on faberix, it would have broken on
opus.

Domains:
- PPE: `ppe.condoparkshare.kumajyo.com` (wildcard cert) / `ppe.condoparkshare.com` (Let's Encrypt)
- Prod: `condoparkshare.kumajyo.com` / `condoparkshare.com`

### Branch / deploy model

| Branch | Deploys to | How to promote |
|---|---|---|
| `main` | nowhere directly | all PRs target here |
| `ppe` | faberix | open PR from `main` → `ppe` |
| `prod` | opus | open PR from `ppe` → `prod` |

All three branches require a PR. ScottThurlow has bypass on all three.

### Service account on servers

The `condoparkshare` system account owns `/opt/condoparkshare/` on each server.
Deploys run as: `sudo -u condoparkshare bash /opt/condoparkshare/scripts/deploy.sh ppe --yes`

The repo is public — no credentials needed for `git pull`.

---

## What's been built and what hasn't

### Built and shipped (v0.1.0)
- Full auth flow: registration, invite-only sign-up, HOA approval, TOTP enrollment, recovery codes, lost-authenticator flow
- Multi-tenant booking: auto-assignment, owner-rotation, earned-horizon metric, booking buffer
- Audit subsystem: AdminAuditLog, audit_healthcheck liveness probe, fail-closed recovery log
- PII encryption: phone (field), TOTP secret (EncryptedTOTPDevice), backup dumps (age)
- Operator console: superuser-only, impersonation with audit trail, PII erasure

### Not yet live / day-2
- **Production (opus)** — service account and Docker not yet configured on opus
- **Redis / CACHE_URL** — not set on faberix; rate limiting is per-worker (locmem). All `manage.py` commands require `--skip-checks` on PPE as a result (#147)
- **BACKUP_ENCRYPTION_RECIPIENT** not in faberix `.env` — backups will fail without it
- **Audit-recovery log backup** — not included in pg_dump; tracked in #120
- **SMS pager** — Twilio procurement pending (#98, #102)
- **`condoparkshare` service account on faberix** — setup in progress; `/opt/condoparkshare/` exists but repo clone may be incomplete

---

## Project-specific conventions

### Bug fix workflow
File a GitHub issue → dispatch coder → open PR. Never edit files directly to fix bugs.
This matters because it preserves the author≠reviewer independence that HOS is built on.

### CPS overrides from HOS defaults
Full details in `docs/hos-overrides.md`. Two active:

1. **`portability_check.sh` line 10** — forward-slash Windows example to avoid
   self-match. HOS CORE still has the buggy backslash form. Re-apply after each HOS
   upgrade. Tracked in HOS#352.

2. **`a11y-reviewer` all-tools grant** — tools restriction removed so the agent can
   reach Chrome DevTools MCP. Re-applied by `scripts/hos/postinstall-restore-frontmatter.sh`
   after every HOS upgrade.

### Release promotion
Requires the GitHub three-part signal on the release issue (add `release-authorized`
label, remove `needs-human`, re-assign to worker) — all by ScottThurlow, all after
the worker posts validation results. A chat message does not authorize a release cut.
See `METHODOLOGY.md` §17 RELEASE AUTH.

---

## Where to find things

| What | Where |
|---|---|
| Product spec | `Specs/SPEC-1-pilot.md`, `Specs/CONFIRMED-REQUIREMENTS.md` |
| Technical design | `docs/design/TECHNICAL-DESIGN.md` |
| Architecture decisions | `docs/architecture/ADR-001-pilot.md` (auth/encryption), `docs/architecture/ADR-002-host-ingress-monitoring-security.md` (monitoring) |
| CPS overrides from HOS | `docs/hos-overrides.md` |
| Deployment crontab | `docs/deploy/CRONTAB.md` |
| Runbooks | `docs/runbooks/` |
| Open issues | https://github.com/thurlow-research/CondoParkShare/issues |
| HOS issues filed from this project | https://github.com/thurlow-research/HumanOversightSystem/issues (filter: field-report label) |

---

## People

| Person | Role |
|---|---|
| ScottThurlow | Human owner, admin, final authority. HIGH-risk approvals. Gate suspension removal. |
