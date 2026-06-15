## CondoParkShare spec-red-team depth

Apply every item below **in addition to** CORE. Do not duplicate items already in CORE. (spec-red-team has no django pack — reference only CORE.) CORE owns the generic process: agy invocation, spec-gap issue fields, output format, and what-you-do-not-do. The items below are the concrete CondoParkShare attack surface you must actually probe and pose to agy.

---

### Project inputs (read before probing)

- `Specs/SPEC-1-pilot.md` — the product spec under review (this is CORE's `{SPEC_FILE}`).
- `docs/design/TECHNICAL-DESIGN.md` — the approved design for the build step, including the earned-horizon curve and cold-start grace formula (do not re-derive the curve; probe whether the spec's *rules around it* are gameable).
- Prior `spec-gap` issues — to avoid duplicating findings.

---

### Earned-horizon gaming vectors (probe every one)

The horizon metric rewards *listing availability*; the failure mode is crediting horizon without real contribution. Concretely pose to agy:

- **Churn-listing**: can a resident list a spot for 1 second every hour (list → unlist → relist) to accrue "listed hours" without ever genuinely sharing? Is credited horizon based on *bookable* availability or on the act of listing?
- **Phantom availability**: can an owner list a spot they will never actually free (e.g. listing windows that overlap their own intended use), earning horizon for availability no resident can use?
- **Threshold-zero**: what happens when `earned_horizon` is exactly zero, and what is the behavior for a user with no listing history **and** no remaining cold-start grace — locked out, or silently granted baseline?
- **Cold-start abuse**: can a user farm the cold-start grace by leaving and rejoining (or by org/account churn) to keep claiming the new-resident baseline?
- **Leaderboard gaming**: the metric also orders the leaderboard — does any horizon-farming vector above also let someone top the leaderboard without genuine contribution? Are listing and leaderboard credit computed from the same source of truth?

---

### Booking-gate bypass (probe each gate independently and in combination)

Every booking-creation path must enforce the horizon gate, the one-active-booking gate, and the overlap gate. Probe the spec for paths that skip one:

- **Horizon gate** — does the spec credit horizon for a booking whose window starts inside the horizon but *ends* beyond it? Is the horizon evaluated against booking start, end, or both? Can clock skew or timezone assumptions push a booking past the earned horizon undetected? (The spec never states the clock is trusted — flag it.)
- **One-active-booking gate** — what counts as "active"? Can a borrower hold one active booking plus N pending/future bookings, defeating the intent? What happens at the seam where one booking ends and the next begins?
- **Overlap gate** — the `tstzrange` exclusion constraint is the final arbiter, but probe the spec's *boundary semantics*: when one booking's end time exactly equals another's start time, is that an overlap or adjacency? The spec must state the half-open vs closed convention or the constraint and the UI will disagree.
- **Cancel-and-retry** — can an owner cancel bookings repeatedly just before start time to dodge a penalty while still stranding the resident? Does early-release / owner-cancel reissue or refund horizon, and can that refund be farmed?

---

### Multi-tenant isolation gaps (CPS is one org per condo/HOA, hostname-resolved)

- **Cross-org leakage**: can a resident of org A enumerate, search, or book spots belonging to org B? Probe every list/search/booking path for an org scope the spec leaves implicit.
- **Hostname spoofing / ambiguous resolution**: what does the spec say happens when the request hostname matches no organization, or matches more than one? Unspecified means a default-org fallthrough is possible — flag it.
- **Leaderboard / horizon scope**: is the leaderboard and the horizon metric scoped per-org, or could one org's listings inflate standing visible to another?

---

### Invite & role-boundary abuse

- **Invite abuse**: registration is invite + approve. Can an invite be reused, forwarded, or replayed by someone other than the invitee? Does an invite carry org membership such that a forwarded invite grafts an outsider into a condo?
- **Role scope creep**: can a resident reach owner-only features (listing management, availability config) or operator/HOA-portal features? Can an owner reach operator-console or admin-audit functions? Probe each role boundary the spec defines (resident / owner / operator / HOA-manager / admin).

---

### What this role does NOT cover

These are real CPS concerns but belong to other roles, not spec red-team — do not create spec-gap issues for them:

- The `tstzrange` GiST constraint *implementation* and `select_for_update()` race handling → coder / django pack.
- PII encryption, right-to-erasure, TOTP/recovery-code handling → privacy-reviewer / security-reviewer.
- Design-pack token/component conformance → ui-reviewer / ux-designer.
