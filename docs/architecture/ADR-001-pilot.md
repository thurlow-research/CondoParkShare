# ADR-001 — CondoParkShare Pilot Architecture
*June 2026. Covers Spec 1 (pilot) only. Supplements SPEC-1-pilot.md and docs/pm/CONFIRMED-REQUIREMENTS.md.*

---

## Stack

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.12 | Current stable; best Django ecosystem support |
| Framework | Django 5.1 + HTMX | Server-rendered; no SPA complexity; Django's ORM, admin, and auth fit the domain exactly |
| Database | PostgreSQL 16 | `tstzrange` + GiST exclusion constraints; row-level scoping; proven |
| Task scheduling | Django management commands + cron | No broker required; bookings are hour-aligned so :00/:30/:45 crons cover all notification windows |
| Email | `django-anymail` | Unified interface for Brevo, Postmark, Mailgun, SendGrid; swap providers via one `.env` change |
| Rate limiting | DB-backed (`django-ratelimit` or custom) | Postgres is sufficient at pilot scale; no Redis dependency |
| Deployment | Docker Compose — three services only: `web`, `db`, `caddy` | Lean stack; Redis deferred until caching/WebSocket/Celery is genuinely needed |

---

## Django app structure

```
parkshare/          ← project root
  accounts/         ← User, TOTP, recovery codes, invite, registration
  parking/          ← Organization, ParkingSpot, AvailabilityWindow, Booking
  notifications/    ← notification dispatch, email relay messaging
  portal/           ← HOA/manager portal views
  operator/         ← operator console (extends Django admin)
```

---

## Multi-tenancy

**Pattern:** custom ORM manager on every model that carries an `organization` FK.

```python
class OrganizationScopedManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(
            organization=get_current_organization()
        )
```

`get_current_organization()` reads from a thread-local set by `TenantMiddleware`, which resolves the tenant from `request.get_host()` matched against `Organization.hostname`.

**Operator console bypass:** the operator console uses Django's built-in `ModelAdmin` on the default admin site (superuser only). It does not use the scoped manager — it queries all tenants directly. All operator actions are logged to `AdminAuditLog`.

**HOA portal:** separate views (not Django admin) with an explicit `organization=request.organization` filter on every queryset. HOA admin users cannot reach the operator console.

---

## Booking overlap and buffer

### GiST exclusion constraint
Enforces raw overlap prevention at the DB layer (race-safe):
```sql
ALTER TABLE parking_booking
ADD CONSTRAINT booking_no_overlap
EXCLUDE USING gist (
    spot_id WITH =,
    time_range WITH &&
);
```

### Buffer enforcement (per-tenant configurable)
The symmetric buffer (N hours before + N hours after every booking) is enforced at the **application layer** inside a `SELECT FOR UPDATE` transaction:

```python
with transaction.atomic():
    conflicts = Booking.objects.select_for_update().filter(
        spot=spot,
        time_range__overlap=buffered_range,  # expanded by N hours each side
        status__in=['active', 'confirmed']
    )
    if conflicts.exists():
        raise BufferConflict
    Booking.objects.create(...)
```

The DB constraint catches true overlaps; the app layer catches buffer violations. This is a deliberate two-layer approach.

**Buffer is per-tenant and read from the DB on each query.** `booking_buffer_hours` is a field on `Organization` (default 1). The application reads `spot.organization.booking_buffer_hours` at booking time — there is no hardcoded constant. Changing this value for a tenant affects all future bookings; existing bookings are unaffected.

---

## Availability computation and spot assignment (F19)

**F19 structural change:** users specify a time window; the system assigns one spot. No browse, no user selection.

### Availability query
A spot is available for `[requested_start, requested_end]` if:
1. An `AvailabilityWindow` covers the entire requested range.
2. No existing `Booking` with status `active`/`confirmed` has a `time_range` overlapping the **buffered** range `[requested_start − Nh, requested_end + Nh]` where N = `organization.booking_buffer_hours`.

### Owner rotation algorithm
When multiple spots are available, assign the spot belonging to the owner who has gone longest since their spot was last booked. Implementation:

```python
available_spots = (
    ParkingSpot.objects
    .filter(organization=org, status='active')
    .annotate(
        last_booked=Max('bookings__time_range',
                        filter=Q(bookings__status='completed'))
    )
    .filter(/* availability and buffer conditions */)
    .order_by('owner__last_booking_at')  # see below
)
spot = available_spots.first()
```

`User.last_booking_at` — denormalized field, updated when a booking on the owner's spot completes. Avoids a correlated subquery on every assignment.

### Tentative hold (race condition mitigation)
Assignment is tentative until the resident confirms:
1. System assigns spot → creates a `Booking` with `status='tentative'`, expiry = now + 5 minutes.
2. Resident confirms → status becomes `confirmed`.
3. If confirmation doesn't arrive within 5 minutes → a cron at `:00` cleans up expired tentative bookings and releases the spot.

Tentative bookings are included in the buffer/overlap check so two residents can't be offered the same spot simultaneously.

---

## Earned-horizon metric

### Formula
`horizon = booking_horizon_baseline_days + floor(elapsed_listed_hours / listing_to_horizon_ratio)`
Clamped to `booking_horizon_max_days`.

### Elapsed listed hours calculation
`elapsed_listed_hours` = SUM of hours from `AvailabilityWindow` ranges that have passed (upper bound < now), within the rolling 180-day window — MINUS SUM of `penalty_hours` from owner-cancelled bookings within the same window.

`penalty_hours` is stored as a field on `Booking`, set to the booking duration when an owner cancels. This avoids re-deriving the penalty from booking history on every horizon calculation.

### Computation
Computed **on-demand** when a resident requests a booking (to check Gate 1). Not cached for the pilot — at single-building scale a per-user query is fast. If performance degrades at scale, add a `cached_horizon` field updated by a nightly job.

### Cold-start grace
For `launch_grace_days` (14) after `Organization.launched_at`, all residents receive `launch_grace_horizon_days` (14 days) regardless of listing history. A simple date comparison — no separate grace state needed.

---

## PII and encryption

| Field | Storage | Encryption |
|---|---|---|
| `email` | `User.email` | Volume encryption only (LUKS on opus). Field encryption breaks login lookup. Blind-index = future hook. |
| `display_name` | `User.display_name` | Volume encryption only |
| `phone` | `User.phone` | Field-encrypted using `django-encrypted-model-fields`. Optional. |
| `password` | `User.password` | Argon2 one-way hash (Django `Argon2PasswordHasher`) |
| `totp_secret` | `EncryptedTOTPDevice.encrypted_key` (accounts app) | Field-encrypted (Fernet via `django-encrypted-model-fields`, same `PII_ENCRYPTION_KEY`). Not stored on `User`. |
| `recovery_codes` | `User.recovery_codes` (JSON array) | Each code individually hashed (Argon2). Shown to user once at enrollment; never stored in plaintext. |

**Encryption key:** loaded from `PII_ENCRYPTION_KEY` environment variable. Never in DB or repo. Key rotation path: `django-encrypted-model-fields` supports key rotation via a secondary key list.

---

## Authentication

**Library:** `django-otp` for TOTP (RFC 6238, 30-second window, ±1 step tolerance).

**Enrollment flow:**
1. Account created (invite or self-register) → `status = pending_totp`.
2. User logs in with password → redirected to TOTP enrollment if `status = pending_totp`.
3. TOTP QR code displayed; user scans with authenticator app.
4. User enters first valid code → TOTP verified → 10 recovery codes generated and displayed **once**.
5. User acknowledges codes → `status = active`.

**Login flow:**
1. Email + password → verified.
2. TOTP code prompt → verified (±1 step).
3. Session established.

**Recovery code flow:** user enters a recovery code instead of TOTP → code consumed (marked used) → session established → user prompted to re-enroll TOTP before accessing app features.

**Lost authenticator flow (NEW-2):**
1. User clicks "Lost access to my authenticator."
2. Email OTP generated (cryptographically random, 6-digit), stored in `EmailOTP` table with `expires_at = now() + 15 minutes`, `consumed = false`.
3. OTP emailed to user's registered address via Brevo/anymail.
4. User enters OTP → verified (not consumed, not expired) → marked consumed.
5. Session granted with `totp_reset_required = True` flag.
6. User forced through TOTP re-enrollment before any other action.

---

## Notification system

### Cron schedule
Three Django management commands, registered in crontab on opus:

```
0  * * * *   python manage.py notify_bookings --event=starts,completions
30 * * * *   python manage.py notify_bookings --event=warning_30
45 * * * *   python manage.py notify_bookings --event=warning_15
```

### Full event matrix

| Event | Owner | Borrower |
|---|---|---|
| Booking confirmed | ✓ (spot loaned) | ✓ (booking confirmed) |
| Booking starts | — | ✓ |
| 30-min warning | ✓ | ✓ |
| 15-min warning | ✓ | ✓ |
| Booking completed | ✓ | ✓ |
| Booking cancelled (any party) | ✓ | ✓ |
| Owner-cancel (with optional reason) | — | ✓ |
| Early release confirmed | ✓ | ✓ |

### Channels
- **Email:** always sent for operational events (non-optional). Via `django-anymail` → Brevo (or configured provider).
- **Web push:** sent only if user has enabled push AND has an active `WebPushSubscription`. Library: `pywebpush`. VAPID keys in `.env`.

### Email relay messaging (NEW-1)
`RelayMessage` model: `from_user`, `to_user`, `booking`, `body`, `reply_token` (UUID, random), `token_expires_at` (= booking end time), `created_at`.

Rate limit: DB-backed counter — max 10 messages per user per booking. Enforced at the view layer before message creation.

Reply link in email: `https://parkshare.kumajyo.com/messages/reply/{token}/`. Token validated on load (exists, not expired). Reply form POSTs to the same URL. No threading, no history stored beyond the `RelayMessage` rows (which are retained for audit purposes until the booking is archived).

---

## HOA portal

Custom Django views (not Django admin) at `/portal/`. HOA admin users have `is_hoa_admin = True` on their `User` record, scoped to their `Organization`. Every view enforces `request.user.organization == object.organization`.

Key portal views:
- Resident list + approval queue (pending registrations)
- Spot approval queue (pending `ParkingSpot` records from NEW-4)
- Invite generation and management
- Booking overview + admin-cancel
- Usage reports (booked-hours/spot demand signal)
- Contact admin message routing (E16 — relays to `support_email`)

All admin actions write to `AdminAuditLog`. PII-displaying views log a `pii_access` entry.

---

## Operator console

Extends Django's built-in admin site (superuser only). Custom `ModelAdmin` classes for:
- `Organization` — tenant lifecycle, all config fields, `support_email`
- `User` — cross-tenant user management, impersonation entry point
- `AdminAuditLog` — read-only; all entries

**Impersonation:** operator clicks "Impersonate" on a User → session stores `{'impersonating': user_id, 'real_operator': operator_id}`. All subsequent requests run as the impersonated user. Middleware logs every action to `AdminAuditLog` with `actor = operator, on_behalf_of = user`. Destructive actions (cancel, block, delete) require a confirmation modal with explicit warning text. "End impersonation" button always visible in a top banner.

---

## Docker Compose

Three services:

```yaml
services:
  web:
    # gunicorn; internal only
  db:
    # Postgres 16; named volume; no host port binding
  caddy:
    # reverse proxy + TLS; ports 80 + 443 only
```

`db` has no `ports:` binding — internal network only. All traffic enters via Caddy.

Caddy handles:
- `parkshare.kumajyo.com` → DNS-01 wildcard cert (`*.kumajyo.com`)
- `parkshare.bellevuetowers.org` → HTTP-01 cert (needs port 80)
- Both in Django `ALLOWED_HOSTS`

---

## Deferred / future hooks

| Item | When to address |
|---|---|
| ~~Per-tenant configurable `booking_buffer_hours`~~ | **Done** — implemented in #84; `Organization.booking_buffer_hours` is live. |
| Blind-index on email for field-level encryption | If LUKS-only email storage is judged insufficient |
| Redis + Celery | If caching, WebSockets, or complex async tasks are needed |
| Leaderboard UI | Post-launch, data already tracked |
| Recurring listing patterns | If residents request it post-launch |
| RFC 3161 external timestamping (Spec 3) | Only if credit economy is enabled |
