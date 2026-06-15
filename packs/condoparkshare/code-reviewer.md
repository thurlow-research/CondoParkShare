## CondoParkShare code-review depth

Apply every item below **in addition to** CORE and the django pack. Do not duplicate items already in either.

---

### Project review inputs

Review the code against these documents (the technical design is the standard; the spec is background):

- `docs/design/TECHNICAL-DESIGN.md` — the implementation contract.
- `docs/architecture/ADR-001-pilot.md` — architectural decisions.
- `Specs/SPEC-1-pilot.md` — product spec (reference for intent).

---

### Booking-gate correctness (the domain's core invariants)

Every booking-creation path must enforce all three gates; verify each is enforced **in code**, not asserted in a comment:

- **Horizon gate** — a borrower may book only within their *earned* booking horizon. Verify the booked range's far edge is checked against the borrower's current horizon, including the cold-start grace value for residents who have not yet earned any.
- **One-active-booking gate** — at most one active booking per borrower. Pin down *when* a booking counts as "active" (the design defines the point); a gate that checks the wrong lifecycle state lets a borrower hold two.
- **Overlap gate** — the `tstzrange` GiST exclusion constraint is the final arbiter, and it must live in the migration (not only `Meta.constraints`). It must be paired with `select_for_update()` so a concurrent double-book fails deterministically on the constraint rather than racing. A booking path that relies on an application-level overlap check without the DB constraint is a blocking finding.

---

### Earned-horizon metric semantics

- The metric must count **only elapsed (past) listed hours** — availability the owner has already provided — not hours listed in the future. A calculation that credits not-yet-elapsed listed time inflates horizon and is a blocking finding.
- Do not re-implement the earning curve or cold-start grace; verify the code matches the formula and grace value in `TECHNICAL-DESIGN.md`.
- The same metric value must feed both the horizon gate and the leaderboard ordering — flag any divergent computation between the two call sites.

---

### Availability computation

- Availability must be computed as owner listing windows **minus** existing bookings over the requested range. Check the range subtraction at the boundaries: a booking that abuts or partially overlaps a window must remove exactly the booked sub-range, leaving the remainder available.
- All booking time ranges must be **hour-aligned** — start on the hour, whole-hour increments. Flag any path that can persist a non-hour-aligned range.

---

### Tenancy shape (CPS-specific)

- CPS runs **one `Organization` per condo/HOA**, resolved by **hostname in middleware**. Beyond the django pack's scoped-manager checks, verify the org is taken from the resolved request context — never from a user-supplied parameter, body field, or path arg.

---

### Design-token check (note for ui-reviewer)

- Templates must reference `var(--token)` / the design-pack component classes (`.badge-available`, `.badge-booked`, `.spot`, `.bay`, `.btn-primary`, `.mono`) — never hard-coded hex. Spline Sans Mono (`.mono`) is for data labels only, not body copy. This is now the `ui-reviewer` lane: name any violation you happen to see and move on; do not block on it.
