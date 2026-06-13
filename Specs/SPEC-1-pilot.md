# CondoParkShare — Spec 1: Pilot (Core)

*v1.0 — June 2026. For Claude Code. Pilot tenant: Bellevue Towers HOA (admin: Columbia Hospitality).*

> **Document set (build in order, each is additive):**
> 1. **Spec 1 — Pilot (Core)** ← this document. Free, no money anywhere. Ship this for Bellevue Towers.
> 2. **Spec 2 — Flat-Fee Subscriptions.** Adds billing (Stripe, payer models). Build only on expansion.
> 3. **Spec 3 — Exchange Economy.** Adds the credit/earn-burn economy. Build only if usage-pricing is wanted.
>
> Each later spec layers onto this one without modifying its behavior. Pair with the design pack (`DESIGN.md` + `tokens.css` + logos).

---

## 1. What we're building (pilot)

A private web app for one condo building: residents list their parking spot when away; other residents book it by the hour. Listing earns extended prebooking privilege (the alignment incentive). **No payments, no credits, no Stripe** — the pilot is free for everyone. Community chat stays in the building's existing channels (Discord); CondoParkShare handles listing and booking.

The app is built **multi-tenant from day one** (so other buildings can be added later without a rewrite), but the pilot runs a single tenant — Bellevue Towers — in **free-forever** mode.

### In scope (pilot)
- Gated resident accounts (invite or approve) with TOTP 2FA + recovery codes
- Multi-tenant foundation (one tenant now; hostname-resolved, row-scoped)
- Spot listing (continuous availability windows); hourly booking with DB-enforced overlap safety
- **Strict one-active-booking limit** (configurable) as the overbooking guard
- **Listing → earned prebooking horizon** (alignment incentive; non-spendable) + leaderboard data
- Cancel / early-release / owner-cancel (free the spot; no money)
- Email + web-push notifications
- Operator console (cross-tenant) + HOA/manager portal (tenant-scoped) + admin audit log
- PII protection: hashed passwords, encrypted PII, right-to-erasure

### Explicitly NOT in the pilot
- **Any billing or payments** — no Stripe, no cards, no subscriptions, no invoices, no promo-expiry, no PCI surface. (→ Spec 2)
- **Any credit/earn-burn economy** — no credits, balances, ledger, expiry, decay. (→ Spec 3)
- SMS; native apps / vendor push; physical access control; self-service tenant signup; tenant theming; leaderboard UI (data only).

## 2. Technology & deployment
- **Framework:** Django (Python) + HTMX (server-rendered, no SPA).
- **Database:** PostgreSQL; `tstzrange` + **GiST exclusion constraints** prevent overlapping bookings at the DB layer.
- **Admin:** Django admin underpins the operator console; tenant-scoped manager portal (§8).
- **Notifications:** pluggable; pilot = email + web push.
- **Styling:** apply the design pack — load `tokens.css` first; green = available, clay = booked; Hanken Grotesk UI + Spline Sans Mono for spot IDs/times.

### Deployment
- **Host:** Docker Compose on `opus` (Ubuntu VM, Hyper-V guest on `nexus`, KumaJyo homelab).
- **Services:** `web` (gunicorn), `db` (Postgres, **named volume**, not host-published), `caddy` (reverse proxy + TLS); `restart: unless-stopped`; VM auto-start + Docker-at-boot.
- **TLS/naming:** canonical `parkshare.kumajyo.com` via `*.kumajyo.com` wildcard (Caddy DNS-01). HOA alias (e.g. `parkshare.bellevuetowers.org`) = CNAME → canonical; Caddy auto-issues via HTTP-01 (needs :80). Both in `ALLOWED_HOSTS`. Canonical name stays constant so a later host move is a DNS repoint.
- **Exposure:** CNAME → DDNS → router forward; app/db internal-only; VLAN isolation; tight UFW.
- **At-rest encryption:** encrypted host volume/DB storage (LUKS on `opus`).
- **Backups:** nightly `pg_dump` → NAS. Encryption keys backed up separately/securely.
- **Portability:** `.env` config + named-volume Postgres + canonical CNAME ⇒ moving to a hosted VPS later (Hetzner EU recommended given the Sweden principal/GDPR) = stand up compose, restore `pg_dump`, repoint canonical CNAME.

## 3. Roles
- **Resident** — books spots; may also own/list.
- **Spot owner** — a resident who lists spots (capability, not a separate login).
- **HOA/manager admin** — manages one tenant via the manager portal (Columbia staff / HOA board).
- **Operator** — platform operator (you); cross-tenant console; manages tenants/keys.

## 4. Listing, booking & the alignment incentive

### Booking
- Whole 1-hour increments, hour-aligned starts.
- **One active booking at a time** (`max_concurrent_bookings`, default **1**). **Strict:** a booking counts from creation until its end passes (or cancel/release); a resident may not hold or queue a second while one is active. This is the credit-less overbooking guard.
- **Max length:** `max_booking_hours` (default **168** = 7 days).
- **Overlap safety:** Postgres exclusion constraint on (`spot`, range) — DB rejects overlaps (race-safe).
- **Availability is computed, not stored:** an `AvailabilityWindow` is one continuous range; available = windows − union of bookings, computed live; bookings fragment windows automatically.
- **Availability is binary — no "blocked" state:** a spot is either available or booked; there is no separate "blocked", "hold", or "buffer" state exposed to searchers or the spot owner. Any internal booking status that holds the spot — including the tentative 5-minute checkout hold — is treated as **booked** for (a) any resident searching for a spot and (b) the spot owner viewing their spot's occupancy. Concretely: a spot is unavailable whenever a booking with status `tentative`, `confirmed`, or `active` overlaps the queried window (this is exactly what the Postgres exclusion constraint already enforces). The borrower sees their booking end at the time they specified; any internal hold around that window is not surfaced to them as a distinct lifecycle state. The `tentative`/`confirmed`/`active`/`completed`/`cancelled_*` enum values are internal lifecycle states only and are never presented directly to searchers or owners as availability categories.

*Clarified 2026-06-12 (authorized Scott Thurlow): There is no "blocked" state. Availability is strictly binary from the perspective of searchers and the spot owner. Any internal hold or buffer (including the tentative checkout hold) counts as booked to them. The borrower sees their booking end at their specified end time; the buffer is not surfaced to them.*

### Listing
- Low-friction "I'm away [dates]" + recurring patterns → continuous `AvailabilityWindow`(s), immediately bookable.

### Alignment incentive — listing → prebooking horizon (non-spendable)
- Baseline `booking_horizon_baseline_days` (default **3**).
- Every `listing_to_horizon_ratio` **elapsed listed hours** grants +1 horizon hour (default ratio **10**), over rolling `tier_metric_window_days` (default **180**). `horizon = baseline + floor(elapsed_listed_hours / ratio)`, clamped to `booking_horizon_max_days` (default **30**).
- **Counts only elapsed listed hours** (genuinely available, already passed) — future listings contribute nothing until they pass (prevents list-then-bail gaming). 180-day window bridges list-while-away → need-later.
- **Cold-start grace:** for `launch_grace_days` (default 14) after a tenant goes live, grant `launch_grace_horizon_days` (default 14) to all residents.
- **Leaderboard:** same elapsed-listed-hours basis; data tracked now, UI later.
- Chronic no-shows / owner-cancels demote standing.

### Cancellation / release (no money)
- Borrower pre-start → booking voided, window freed.
- Borrower early release → remaining hours freed back to inventory; the one-booking slot frees so they can book again.
- Owner cancels a booked slot → booking voided, borrower notified, owner standing penalty.

### Other
- **Timezone:** one per tenant (`Organization.timezone`; BT = America/Los_Angeles). Store UTC, display local.
- **Discovery:** primary = search by time needed; browse secondary.
- **Completion:** a booked hour completes when its end passes without cancellation (trust-based, no check-in).

## 5. Notifications
Channel-agnostic; per-user per-event prefs. Pilot = email (default) + web push (PWA; iOS needs add-to-home-screen; email is the reliable floor). Events: booking confirmed; spot loaned (owner); loan ending soon; cancelled; owner-cancelled; early-release confirmation.

## 6. Authentication
- **2FA: standard TOTP only** (RFC 6238); QR-enroll any authenticator; on-device, free, vendor-neutral. No vendor push.
- **Recovery codes** at enrollment (required; no SMS fallback).
- **Gated registration:** invite (Mode A) or self-register-then-approve (Mode B); `registration_mode` (BT default `invite_only`). Never open public signup.

## 7. PII & data protection
**Hash what you only verify; encrypt what you must read back; minimize collection.**
- **Passwords:** one-way hash (argon2/bcrypt). Never recoverable.
- **PII (email, name, phone): encrypted (reversible) — never hashed** (must be readable to use). Layered: volume encryption at rest (all) + TLS in transit (all) + selective field encryption for **phone** (most sensitive). Email relies on volume encryption (field-encryption breaks login lookup; blind-index = future hook). Names = volume encryption.
- **Phone:** optional, field-encrypted; for owner↔borrower coordination. Droppable if unused.
- **Keys:** in `.env`/secrets (ideally YubiKey-derived), never in DB/repo; rotation documented; backed up separately from `pg_dump`.
- **Access:** PII scoped per admin surface; admin PII access logged.
- **Retention & right-to-erasure (GDPR):** scrub User PII + anonymize references; consent/lawful-basis notice at signup; EU hosting.

## 8. Admin surfaces
### Operator console (cross-tenant — you; Django superuser)
- Create/configure tenants: `timezone`, `registration_mode`, `unit_count`, hostname, all §10 config. (No billing config in pilot.)
- Tenant lifecycle; cross-tenant oversight; support impersonation (logged).

### HOA/manager portal (tenant-scoped — Columbia/HOA; own tenant only)
- Users: view residents, approve pending registrations, block/unblock, resend invites.
- Invites: generate links/codes; see joined/pending.
- Spots: add/edit, assign owners, deactivate.
- Reports: usage incl. **booked-hours/spot** (demand signal).
- Bookings: view, disputes, admin-cancel (logged).
- **Cannot:** see other tenants; access operator console.

### Admin audit log
- Every privileged action (block, admin-cancel, PII access, override) logged with admin identity, target, tenant, timestamp. No silent privileged changes.

## 9. Data model (pilot)
All domain rows carry `organization` FK + timestamps.
- **Organization** — `timezone`, `registration_mode`, `unit_count`, hostname, all §10 config. (Pilot tenant = Bellevue Towers; `payer_model` exists as a field defaulting to `free_forever` but billing is inert — see Spec 2.)
- **User** — `email`(enc), `display_name`(enc), `phone`(opt, field-enc), `password`(hashed), `totp_secret`, `recovery_codes`, `notification_prefs`, `status`(pending/active/blocked), `organization`.
- **ParkingSpot** — `spot_number`, `owner`, `organization`, location/notes, active.
- **AvailabilityWindow** — `spot`, continuous `tstzrange`.
- **Booking** — `spot`, `borrower`, `tstzrange` (hour-aligned), `status` (`tentative`/`confirmed`/`active`/`completed`/`cancelled_borrower`/`cancelled_owner`/`cancelled_admin`); GiST exclusion constraint. Status values are internal lifecycle only — searchers and owners see a binary available/booked signal (see §4 Booking).
- **Invite** — `code`, `organization`, `issued_by`, single-use/capped, expiry, `consumed_by/at`, optional unit/spot pre-tag.
- **AdminAuditLog** — `actor`, `action`, `target`, `organization`, timestamp.

## 10. Per-tenant configuration (pilot)
| Key | Default / BT | Meaning |
|---|---|---|
| `timezone` | America/Los_Angeles | Tenant TZ (store UTC) |
| `registration_mode` | invite_only | invite / approve / both |
| `unit_count` | (per tenant) | Recorded now; billing denominator later |
| `payer_model` | free_forever | Field present; only free_forever used in pilot |
| `max_concurrent_bookings` | 1 | Strict one-at-a-time guard |
| `max_booking_hours` | 168 | Max single booking |
| `booking_horizon_baseline_days` | 3 | Baseline advance booking |
| `booking_horizon_max_days` | 30 | Hard cap |
| `listing_to_horizon_ratio` | 10 | Elapsed listed hrs per +1 horizon hr |
| `tier_metric_window_days` | 180 | Rolling listing window |
| `launch_grace_days` / `launch_grace_horizon_days` | 14 / 14 | Cold-start grace |

## 11. Primary flows (pilot)
- **Booking:** auth (TOTP) → search by time → pick spot + hours → Gate 1 horizon (≤ now + earned horizon) → Gate 2 one-active-booking → Gate 3 DB overlap → confirm → notify borrower + owner → reminder → completion.
- **Listing:** "I'm away [dates]" / recurring → `AvailabilityWindow`(s) → bookable → elapsed listed hours feed horizon/leaderboard.
- **Cancellation/release:** free the spot; owner-cancel notifies + penalty; no money.
- **Onboarding A (invite):** admin generates → resident registers (email, password, TOTP, recovery codes) → auto-active.
- **Onboarding B (approve):** resident submits (email, unit) → pending → admin approves → active.

## 12. Build order (pilot)
1. Scaffold: Django + Postgres + Compose (`web`/`db`/`caddy`), `.env`, named volume, volume encryption; load `tokens.css`.
2. `Organization` + multi-tenant middleware + hostname resolution + scoped managers.
3. Auth: accounts (encrypted PII), invite + approve registration, TOTP + recovery codes.
4. Data model + migrations: `tstzrange` exclusion constraint.
5. Owner listing + availability computation.
6. Resident search + booking (Gates: horizon, one-active-booking, overlap).
7. Listing→horizon metric + cold-start grace; leaderboard data.
8. Cancellation / early-release / owner-cancel (free the spot).
9. Notifications (email → web push).
10. Operator console + HOA/manager portal + admin audit log.
11. Right-to-erasure; deploy to `opus` behind Caddy/DDNS; nightly `pg_dump` → NAS.
