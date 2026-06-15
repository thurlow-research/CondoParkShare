## CondoParkShare technical-design depth

Apply every item below **in addition to** CORE and the django pack. Do not duplicate items already in either — the generic design process (architect loop, routing hub, sign-offs) lives in CORE, and generic Django design idioms (model-contract format, scoped-manager mechanism, URL/view/form contract format, algorithm-spec format, TOTP/notification/admin/erasure/migration *templates*) live in the django pack. This file is only CPS's concrete models, algorithms, and paths.

---

### Project inputs and output path

- Write the technical design to `docs/design/TECHNICAL-DESIGN.md` — this is CPS's authoritative implementation contract.
- Read before designing: `Specs/SPEC-1-pilot.md` (product spec), `docs/architecture/ADR-001-pilot.md` (architect's binding decisions), and the pm-agent's confirmed Q&A output.
- Cover every item in SPEC-1 §12 build order, in sequence — the spec build order is the design's table of contents.

---

### The CPS model set to design

The design must specify these concrete models (apply the django pack's model-contract format to each):

- **Organization** — one row per condo/HOA. Tenancy root; resolved by hostname in middleware. Every other tenant model carries `organization` FK.
- **accounts** — resident/owner/operator identities with encrypted PII; invite + approve registration state; TOTP secret + hashed recovery codes.
- **parking** — the spot/bay inventory owned within an organization, plus owner `AvailabilityWindow` listings (`tstzrange`).
- **bookings** — a resident's reservation of a spot over a `tstzrange`, with the lifecycle states (active / cancelled / early-released / owner-cancelled) and the derived `booking_horizon` it is checked against.

State per model: which carry `organization` and use the org-scoped manager, the `on_delete` for every FK in the spot→window→booking hierarchy, and which side owns the deletion gate.

---

### Availability computation (the exact contract)

- Result = owner `AvailabilityWindow` ranges **minus the union of overlapping active `Booking` ranges**, over a requested time range, scoped to one organization.
- Specify it as PostgreSQL range arithmetic on the `tstzrange` columns (the django pack covers the `__overlap` / `DateTimeTZRangeField` mechanics) — write out the actual queryset/SQL, not "compute availability."
- This computation is the single source for resident search; name where it runs (manager method) and whether/where its result is materialized.
- Edge cases the design must pin down: empty windows, a window fully consumed by bookings, a range crossing a DST boundary, and zero-duration requests.

---

### Booking gates (design them as enforced invariants)

Every booking-creation path enforces, in order — the design must specify each as a checkable contract:

1. **Horizon gate** — the booking's start must fall within the borrower's *earned* `booking_horizon` (see metric below).
2. **One-active-booking gate** — a borrower holds at most one `active` booking; specify the query that detects an existing active booking and the failure response.
3. **Overlap gate** — no two bookings overlap the same spot. The `tstzrange` GiST **exclusion constraint is the final arbiter**; the design must pair it with `select_for_update()` on the spot/window row so the outcome is deterministic, and specify that the form surfaces the resulting `IntegrityError` as a `ValidationError`.

---

### Earned-horizon metric (design the algorithm, not just the format)

- An owner **earns booking horizon by listing their spot as available**: elapsed *past* listed hours, counted over a **rolling 180-day window**, drive how far ahead they may book others' spots. Only hours already elapsed count — never future-listed hours.
- **Cold-start grace**: a new resident receives a baseline horizon at signup so they are not locked out before earning any. The grace value and the earning curve are fixed in `TECHNICAL-DESIGN.md`/SPEC-1 — design to the specified curve; do not invent it.
- Specify where the metric runs (signal / cron / on-demand), how `booking_horizon` is derived, and the cache/materialization contract (when written, what triggers recompute, staleness behavior).
- The same metric feeds both the horizon gate and the leaderboard ordering — design one computation, two consumers.

---

### URL structure (CPS areas)

Group `urlpatterns` by CPS's four role areas — **resident, owner, admin, operator** — with per-area `include()` prefix and `app_name` namespace. (django pack covers the per-entry format.)

---

### Cancellation lifecycle and notifications

- Design the three release paths distinctly: resident **cancellation**, resident **early-release**, and **owner-cancel** — state for each what booking state results and which freed range returns to availability.
- Notification events to wire (django pack covers the dispatch-chain format): booking confirmed, cancellation/early-release/owner-cancel, registration invite + approval. Channel order is **email first, web push second** (SPEC-1 §12 step 9).

---

### Right-to-erasure (CPS specifics)

Beyond the django pack's erasure-contract format, specify for CPS: scrub the encrypted PII on `accounts` and delete the TOTP secret + recovery codes, while **anonymizing rather than deleting `bookings`/availability history** retained for HOA audit — state the cascade `accounts → bookings/availability scrub` and which fields are nulled vs. which rows are hard-deleted.
