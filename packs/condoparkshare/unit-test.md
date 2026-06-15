## CondoParkShare unit-test depth

Apply every item below **in addition to** CORE and the django pack. Do not duplicate items already in either — the 80%/75% targets, iteration loop, sign-off register, mutmut/coverage/pytest-django invocation, `django_db` markers, factory_boy/baker, freezegun, `IntegrityError`/`ExclusionConstraint`/`full_clean` patterns, scoped-manager and transaction idioms, and the generic test-file layout all live there and are not repeated here. The targets are the CORE floor, not a CPS override — CPS does not raise or lower them.

---

### Booking gates — test all three at the boundary

Every booking-creation path enforces three gates in order; each gets boundary tests (the value that just passes and the value that just fails):

- **Horizon gate** — a booking whose start exceeds `now + earned_horizon` is rejected; one within is accepted. Test at the boundary (start exactly at the edge).
- **One-active-booking gate** — a resident with an active booking cannot create another. Distinguish the three states: *active* = created but not ended; *ended* = past end time (frees the resident); *cancelled* = frees the slot.
- **Overlap gate** — concurrent bookings for the same spot at overlapping times are rejected. Assert the DB-level `tstzrange` GiST exclusion constraint directly (attempt overlapping inserts, expect `IntegrityError`); pair with the `select_for_update()` path so the failure is deterministic, not a race.

---

### Earned-horizon metric

- `elapsed_listed_hours` counts only *past* listed hours — not future availability windows; hours outside the 180-day window are excluded.
- `horizon = baseline + floor(elapsed / ratio)`, clamped to `max`. Test the clamp.
- Cold-start grace: during `launch_grace_days`, every resident gets `launch_grace_horizon_days` regardless of listing history.
- A resident with zero listing history gets baseline only.
- Implement the curve to `docs/design/TECHNICAL-DESIGN.md` — do not invent thresholds. The metric feeds both the horizon gate and the leaderboard ordering; test both consumers.

---

### Availability computation

Availability = owner listings minus existing bookings over a range:

- A window with no bookings returns the full range.
- A booking in the middle of a window splits it into two available slots.
- A booking at the start/end of a window clips it correctly.
- Overlapping bookings (shouldn't exist — test defensively) are handled.
- A fully booked window returns empty.

---

### CPS model constraints

- `Booking.tstzrange` is hour-aligned: start on the hour, whole hours only.
- Booking duration ≤ `max_booking_hours`.
- `AvailabilityWindow` cannot be zero-length.
- `Organization` FK is enforced cross-tenant: a spot from org A cannot be booked by a resident of org B (CPS is one organization per condo/HOA, resolved by hostname).

---

### Authentication flows (TOTP, recovery, invite, registration)

- **TOTP:** valid code passes; invalid fails; expired fails; already-used code fails.
- **Recovery code:** valid code consumed on use; same code rejected on second use; all codes exhausted = login fails.
- **Invite token:** single-use; expired rejected; already-consumed rejected.
- **Registration mode:** `invite_only` rejects self-registration; `approve` creates a pending account.

---

### Right-to-erasure (`delete_user_pii()`)

- After `delete_user_pii()`: `User.email`, `display_name`, `phone` are null/scrubbed.
- Booking records remain (anonymized); the user FK on a booking is nulled or repointed to a placeholder.
- TOTP secret and recovery codes are deleted.

---

### Admin audit log

- Every privileged action (block, admin-cancel, PII access, override) writes *exactly one* `AdminAuditLog` entry.
- The entry carries actor, target, organization, action, timestamp — assert no field is missing.
