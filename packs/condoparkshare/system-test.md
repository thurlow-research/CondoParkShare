## CondoParkShare system-test depth

Apply every item below **in addition to** CORE and the django pack. Do not duplicate items already in either — generic system-test process (spec-derived testing, code-bug-vs-spec-gap routing, the 5-round loop, sign-off register) lives in CORE, and generic Django test-client / `reverse` / `force_login` / `freezegun` / HTMX-fragment / cross-scope-IDOR idioms live in the django pack. This region is only the CPS flows and their exact pass/fail conditions, drawn from `Specs/SPEC-1-pilot.md` §4–§11.

---

### Booking flow — the three gates, in order

Each gate is a distinct scenario with its own expected rejection; do not collapse them into one test:

- **Available-search:** authenticated resident searches a time window → sees only spots available for that window; a spot that is listed but already booked for an overlapping window does **not** appear.
- **Gate 1 (horizon):** a booking whose start is beyond the resident's *earned* horizon is rejected with the horizon error — booking at-or-inside the horizon succeeds.
- **Gate 2 (one-active-booking):** a resident already holding an active booking is rejected on a second booking attempt.
- **Gate 3 (overlap):** a second booking for the same spot at an overlapping time is rejected by the `tstzrange` GiST exclusion constraint (the DB constraint, not just app logic — test that it holds even on a forced concurrent path).
- **Success postconditions:** a confirmed booking creates notification records for **both** borrower and owner, and the spot disappears from available-search for that window.

---

### Listing & earned-horizon accumulation

- Owner creates a single availability window → spot appears in search for exactly that window; owner creates a **recurring** availability → the correct set of windows is created.
- **Elapsed** listed hours accumulate as time passes (drive with the django pack's freeze mechanism); **future** listed hours do not yet accumulate.
- New resident receives the **baseline** horizon (3-day default).
- During the **cold-start grace** period, a resident receives `launch_grace_horizon_days` regardless of listing history.
- A resident with sufficient elapsed listed hours receives the elevated horizon — assert the exact formula `baseline + floor(elapsed / ratio)`, not just "more than baseline".

---

### Cancellation, early-release, owner-cancel

- **Borrower cancels pre-start:** booking voided; spot returns to available-search; the one-active-booking slot is freed (resident can immediately book again).
- **Borrower early-release:** remaining hours are freed and the resident can book again.
- **Owner cancels a booked slot:** booking voided; a borrower notification record is created; an owner standing **penalty** is recorded.

---

### Onboarding — Mode A (invite_only) and Mode B (approve)

- **Mode A:** admin-generated invite link is **single-use** — registration via the link forces TOTP enrollment, shows recovery codes, then activates the account; a **second use** of the same link is rejected; an **expired** invite is rejected.
- **Mode B:** self-registration leaves account status `pending`; HOA-admin **approve** → status `active` (resident can log in); HOA-admin **block** → status `blocked` (login fails).

---

### Authentication — TOTP & recovery codes

- Login without a TOTP code fails; login with a correct TOTP succeeds.
- Login with a **recovery code** succeeds and **consumes** the code; a second login with the same recovery code fails.
- A logged-out resident cannot reach any resident view (redirect to login).

---

### Tenant-scoped portals vs operator console

Beyond the generic cross-scope isolation test (django pack), assert the CPS role hierarchy:

- **HOA/manager portal:** an HOA admin sees only **their building's** residents (another building's resident PK → 404/redirect); can approve/block residents; can view usage reports; **cannot** reach operator-console views.
- **Operator console:** an operator can create/configure a new tenant and access **all** tenants' data; an HOA admin attempting any operator-console view is denied.

---

### Right-to-erasure & admin audit log

- **Erasure:** after an erasure request, the user's PII fields are scrubbed, booking records **remain** but the user FK is anonymized, and the erasure event is present in the admin audit log.
- **Audit log:** an admin-cancel action, a PII-access in the HOA portal, and block/unblock actions each produce an audit-log entry — assert the entry exists for each.
