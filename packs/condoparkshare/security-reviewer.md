## CondoParkShare security-review depth

Apply every item below **in addition to** CORE and the django pack. Do not duplicate items already in either — generic session/enumeration/PRNG checks live in CORE; generic Django TOTP, org-scoped queryset isolation, `select_for_update` single-use atomicity, settings/headers/CSP/CSRF-HTMX, ORM/template/shell injection, and metric/log neutralization (incl. the CPS#108 field instance) live in the django pack and are not repeated here.

---

### Threat model (drive every review from this)

Review adversarially from these concrete actors — they set the severity bar:

- **Primary attacker — a registered resident** who knows the app, can create valid bookings, and wants to abuse other residents, read their data, or escalate privilege. Treat any cross-resident data read or booking-gate bypass by an authenticated resident as **high** minimum.
- **Secondary — an HOA admin / manager at one building** reaching into another building's data (cross-tenant). CPS is **one organization per condo/HOA**, resolved by hostname in middleware — so also check that the *hostname→org* resolution itself cannot be spoofed (forged `Host`/`X-Forwarded-Host`) to land a request in the wrong org context.
- **External — unauthenticated** credential stuffing, account/email enumeration, CSRF from a malicious site.
- **Out of scope (do not raise):** physical access, network infrastructure, host OS — those are deployment/infra-reviewer concerns.

---

### Booking-authorization bypass tests (CPS core invariants)

Every booking-creation path must enforce all three gates server-side; an attacker who skips the UI must still be stopped. Verify each gate cannot be bypassed:

- **Horizon gate** — a borrower can only book within their *earned* booking horizon. Confirm the horizon is recomputed/verified server-side at booking time, not trusted from a client-supplied value, a hidden form field, or a stale cached number. A resident forging a later date than their earned horizon is a **high** authorization finding.
- **One-active-booking gate** — at most one active booking per borrower. Probe the concurrent path: two simultaneous create requests must not both succeed (pairs with the django pack's `select_for_update`/atomic rule — here confirm it is actually applied to *this* invariant, not just to overlap).
- **Overlap gate** — the `tstzrange` GiST exclusion constraint is the final arbiter; verify no code path inserts a booking by a route that bypasses the constraint (raw insert, bulk op, or a manager that skips it).

The earned-horizon metric is itself a privilege surface: check it cannot be inflated by self-dealing (e.g. listing then immediately cancelling to farm horizon, or a cold-start grace path that grants horizon without the earning being real). Route a *policy* question ("should farming lock the account?") to `pm-agent`; flag an *implementation* hole that lets horizon be inflated without listing as a finding.

---

### Invite & registration-flow abuse

- **Invite single-use:** an invite token must be consumable exactly once even under concurrent redemption, and must bind to the issuing organization — a token from building A must not register an account into building B. (CORE covers crypto-PRNG generation; django pack covers atomic consumption; **here** confirm the org-binding and single-use scope of the *invite* specifically.)
- **Approve-registration gate:** self-registration must not auto-activate; verify a pending account cannot perform resident actions before HOA approval, and cannot escalate itself to approved/admin by replaying or tampering the approval request.

---

### Operator console & portal reachability

- The **operator console** must be unreachable by any non-superuser — including a fully-authenticated HOA admin of another building. Verify with the cross-tenant admin actor, not just an anonymous one.
- The **HOA/manager portal** must be scoped to that manager's single organization; a manager must not navigate to another org's objects by ID (this is the CPS instance of the django pack's IDOR rule — confirm it holds on the portal's own views, including the admin audit-log views).

---

### Right-to-erasure & PII-bearing flows (security angle only)

- After a right-to-erasure run, confirm no security-relevant residue lets the erased identity still authenticate or be enumerated (orphaned session, un-revoked invite/recovery code, login form that now reveals the address was deleted).
- (Lawful-basis, minimization, and encryption *correctness* are `privacy-reviewer`'s lane — note and route, do not block. Your angle is only: does erasure leave an exploitable authentication/enumeration residue.)
