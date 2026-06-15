## CondoParkShare domain depth

This region adds CondoParkShare's product-specific build rules to the stack-neutral CORE and the `django` pack. Apply every item below **in addition to** CORE and the django pack. Do not duplicate items already in either — generic Django ORM/transaction/HTMX/settings/audit idioms live in the django pack and are not repeated here.

---

### Project inputs (read before writing any code)

- `docs/design/TECHNICAL-DESIGN.md` — your implementation contract (authoritative build guide).
- `docs/architecture/ADR-001-pilot.md` — architectural decisions (binding).
- `Specs/SPEC-1-pilot.md` — product spec (reference).
- `Specs/condoparkshare-design-pack/DESIGN.md` + `Specs/condoparkshare-design-pack/css/tokens.css` — UI/visual rules (apply exactly).

---

### Build order (SPEC-1 §12 — do not skip ahead)

Each step depends on the prior; implement in sequence:

1. Scaffold: Django + Postgres + Compose (`web`/`db`/`caddy`), `.env`, named volume; load the design-pack `tokens.css`.
2. `Organization` + multi-tenant middleware + hostname resolution + org-scoped managers.
3. Auth: accounts with encrypted PII, invite + approve registration, TOTP + recovery codes.
4. Data model + migrations: the `tstzrange` GiST exclusion constraint for booking overlap.
5. Owner listing + availability computation.
6. Resident search + booking — enforce the three booking gates (below).
7. Listing → earned-horizon metric + cold-start grace; leaderboard data.
8. Cancellation / early-release / owner-cancel.
9. Notifications (email → web push).
10. Operator console + HOA/manager portal + admin audit log.
11. Right-to-erasure; deploy config for `opus` behind Caddy/DDNS; nightly `pg_dump` → NAS.

---

### Booking gates (the domain's core invariants)

Every booking-creation path must enforce, in order:

- **Horizon gate** — a borrower may only book within their *earned* booking horizon (see metric below).
- **One-active-booking gate** — a borrower may hold at most one in-flight booking (status `tentative`, `confirmed`, or `active` — not only `active`) at a time.
- **Overlap gate** — no two bookings may overlap the same spot. The `tstzrange` GiST exclusion constraint is the final arbiter; pair it with `select_for_update()` (per the django pack) so the failure is deterministic, not a race.
- **Duration cap** — reject bookings longer than `max_booking_hours` (SPEC-1 §4/§10: 168h / 7 days); validate at the form layer.

A booking counts as **booked** for availability/search whenever its status is `tentative`, `confirmed`, or `active` (SPEC-1 §4) — availability computation and the one-active query must use all three, not just `active`. The `Organization` model carries a `payer_model` field (default `free_forever`, SPEC-1 §9) — include it for Spec-2 forward-compat even though billing is inert in the pilot.

---

### Earned-horizon metric

- A spot owner earns booking horizon by *listing* their spot as available; the more they contribute availability, the further ahead they may book others' spots.
- Apply a **cold-start grace**: new residents get a baseline horizon before they have earned any, so they are not locked out at signup. The grace and earning formula are specified in `TECHNICAL-DESIGN.md` — implement to that, do not invent the curve.
- The metric feeds both the horizon gate and the leaderboard ordering.

---

### Availability & multi-tenancy specifics

- Availability is computed from owner listings minus existing bookings over a time range; expose it as the source for resident search.
- Every model touching org data is `Organization`-scoped via its manager (django pack covers the mechanism); CPS has **one organization per condo/HOA**, resolved by hostname in middleware.

---

### Design-pack components (apply exactly as named)

- Load `tokens.css` before any page CSS; reference `var(--meadow)` etc. — never hard-code hex.
- Apply the design-pack component classes exactly: `.badge-available` / `.badge-booked` (spot status), `.spot`, `.bay`, `.btn-primary`, `.mono`.
- **Spline Sans Mono (`.mono`) is for data labels only** — not body copy. Bay-bracket styling is restrained (ui-reviewer / ux-designer enforce these).

---

### Deploy specifics

- Production host `opus` behind Caddy + dynamic DNS (DDNS); TLS via Caddy.
- Nightly `pg_dump` → NAS backup.
- All secrets via `.env` (django pack covers the settings-hardening mechanics).
