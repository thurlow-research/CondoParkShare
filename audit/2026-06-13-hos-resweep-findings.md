# HOS Re-Validation Sweep — Findings

**Date:** 2026-06-13
**Branch:** `validation/hos-resweep`
**Trigger:** Oversight agents/process churned (HOS sync); full re-validation of the whole app.
**Mode:** Reviewers did full re-reviews; `pm-agent` (spec) and `technical-design` ran in evaluate-the-delta mode; `ux-designer` ran its first readiness audit.

## Overall result

**Every domain returned CHANGES_REQUESTED / CHANGES NEEDED. No sign-off stamps written — the sign-off gate stays red.** Unit/system tests not yet run (need Postgres; baseline run errors with `django.db.OperationalError` because the DB isn't up locally).

| Agent | Verdict |
|---|---|
| code-reviewer | CHANGES_REQUESTED (8 blocking) |
| security-reviewer | CHANGES_REQUESTED (1 critical, 3 high) |
| privacy-reviewer | CHANGES_REQUESTED (7 blocking) |
| ui-reviewer | CHANGES_REQUESTED (10 blocking) |
| a11y-reviewer | CHANGES_REQUESTED (7 blocking) |
| infra-reviewer | CHANGES_REQUESTED (5 blocking) |
| technical-design (eval) | CHANGES NEEDED (6 doc deltas) |
| pm-agent / spec (eval) | CHANGES NEEDED (8 clarifying amendments) |
| ux-designer (audit) | CHANGES NEEDED (7 items) |

## Cross-corroborated (multiple independent agents — highest confidence)

1. **~20–23 templates are bare HTML stubs with no design pack** — `ui`, `a11y`, `ux-designer`. All `portal/*`, `notifications/*`, `accounts/register*`, `parking/booking_cancel.html`, `parking/booking_release.html`. No `tokens.css`, no `{% extends "base.html" %}`, no focus ring / skip link / viewport meta / `lang`. This is the dominant finding.
2. **`tokens.css` `.badge-pending` / `.badge-inactive` border bug** — `ui`, `ux-designer`. `border-color` set with no `border-style`/`width`, so no border renders. Fix: `border:1px solid var(--status-line)` (both `static/css/tokens.css` and the pack copy).
3. **TOTP secret stored plaintext** though ADR-001 says field-encrypted — `security`, `privacy`. Needs architect (encryption approach + migration).
4. **`AdminAuditLogAdmin.get_readonly_fields` broken** — `code`, `privacy`. Includes reverse relations / bad `_meta` access → admin `FieldError` and potential audit-log tampering.

## Blocking — Security
- **[CRITICAL] HOA approval bypass** (`accounts/views.py` lost_authenticator→totp_enroll): a `pending_approval` self-registrant can reach `totp_enroll`, which sets `status='active'`, bypassing HOA review.
- **[high] Operator console TOTP not enforced** (`parkshare/admin_site.py`): `/admin/login/` accepts password only; `SuperuserAdminSite` should extend `OTPAdminSite`.
- **[high] TOTP secret plaintext** (see corroborated #3).
- **[high] Recovery-code consumption non-atomic** (`accounts/views.py`): concurrent requests can consume the same code twice; wrap in `select_for_update()`.

## Blocking — Privacy / GDPR
- Operator `UserAdmin` and portal `portal_bookings`/`portal_reports` render PII with **no `pii_access` audit log** (spec §7 requires it).
- `erase_user_pii` leaves `RelayMessage.from_user`/`to_user` populated (code comment promises null), leaves `ParkingSpot.owner` populated, and doesn't reset `marketing_email_opted_in`.
- Consent notice references a **non-existent Privacy Policy**; no data-controller identity (GDPR Art. 13).

## Blocking — Code correctness
- **Earned-horizon** drops availability windows that span the rolling-window boundary instead of clamping them (`parking/horizon.py`) — undercounts elapsed hours.
- Multi-tenant scoping: `spot_list` uses unscoped `ParkingSpot.objects`; `AvailabilityWindowForm` spot queryset has no org filter.
- `lost_authenticator_verify` OTP match not tied to the requesting user (cross-user OTP consumption within an org).
- Admin-cancel fires `booking_cancelled_by_owner` (owner gets "you cancelled your own booking").
- **No root URL `/`** (`home` view missing) → 404 at site root.
- `release_booking`/`EarlyReleaseForm` don't enforce minimum 1 retained hour.
- `parking/availability.py` `is_spot_available`/`get_available_slots` are dead code.

## Blocking — UI / A11y
- 20+ unstyled templates (corroborated #1); badge border bug (#2).
- WCAG AA contrast: `.btn-primary` white-on-meadow **3.39:1**; `.badge-booked` **4.42:1** (need ≥4.5:1) — `tokens.css` (architect approval to change tokens).
- Spline Sans Mono misused on email/phone/date; clay-ink used decoratively on cancel links; raw `--meadow` instead of `--success`; `alert-info` wrong background token; alerts are color-only (no icon).
- Touch targets <44px (nav, action buttons); HTMX `availability_add` error path drops focus; portal table buttons lack subject-specific accessible names; registration form errors not announced.
- Voice: "Submit request", "Confirm cancellation"; missing 404/403/500 templates; logo SVG `aria-label="CondoCondoParkShare"` typo.

## Blocking — Infra
- **`ALLOWED_HOSTS` mismatch**: `.env.example` lists `parkshare.kumajyo.com`/`parkshare.bellevuetowers.org` but the Caddyfile routes `condoparkshare.kumajyo.com`/`condoparkshare.com` — Django would 400 **every** production request.
- `docker-compose.yml` has no `caddy` service (Nexus front-proxy topology) — spec §2 deviation; needs architect acceptance + documented portability procedure.
- `scripts/backup.sh` writes the PII dump to NAS unencrypted; NAS-at-rest encryption unverified (GDPR).
- `scripts/deploy.sh` hostname guard uses substring match (`octopus` would pass the `opus` prod guard) — change to exact match.
- HSTS depends on `SECURE_PROXY_SSL_HEADER` in `production.py` (behind Caddy) — verify it's set.

## Evaluate-only (documentation deltas — not code defects)
- **Technical design** (6): `operator/`→`operator_console/` rename in §1/§12 (+ delete stale empty `operator/` dir); document Gate 2 enforced atomically inside `assign_spot`; align `assign_spot` `Exists` subquery listing; note `parking/leaderboard.py`; add `impersonate_action` + `AdminAuditLog.log()`; note tentative-cleanup redundancy.
- **Spec** (8, mostly already captured in CONFIRMED-REQUIREMENTS.md): "demote standing"→horizon-hours deduction; User.status 4 values; auto-assignment replaced browse (F19); ParkingSpot pending/inactive; notification matrix cross-ref; reports booked-hours vs count; horizon formula penalty term; 1-hour buffer description.

## Architect escalations
- TOTP field-encryption approach + migration.
- `caddy`-not-in-compose topology acceptance + portability procedure.
- Token contrast changes (`--btn-primary`, `--clay-ink`) — design-token edits.

## Notes
- Many UI findings stem from templates that were created as bare stubs (the "18 missing templates" work) and never had the design pack applied — a coder adoption task, not new design.
- `scripts/deploy.sh` findings are about code added this session (sign-off-gate PR).
