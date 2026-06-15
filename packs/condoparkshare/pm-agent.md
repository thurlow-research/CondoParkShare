## CondoParkShare product depth

Apply every item below **in addition to** CORE. Do not duplicate items already in CORE. (There is no django pack for this role — reference CORE only.) Generic PM process — initial-review steps, spec-gap escalation, the clarifying/additive/structural classification, test-plan sign-off — lives in CORE and is not repeated here.

---

### Spec set (read all before acting)

- `Specs/SPEC.md` — index and key principles.
- `Specs/SPEC-1-pilot.md` — the full pilot build; **this is what is being built now**.
- `Specs/SPEC-2-subscriptions.md` — future billing layer; **dormant for pilot**.
- `Specs/SPEC-3-exchange-economy.md` — future credit economy; **dormant for pilot**.
- `Specs/condoparkshare-design-pack/DESIGN.md` — visual/UX source of truth.

Confirmed-requirements supplement is written to `docs/pm/CONFIRMED-REQUIREMENTS.md`.

---

### Pilot scope (do not build ahead of it)

- Single condo: **Bellevue Towers HOA**.
- `payer_model = free_forever`; `credit_economy_enabled = false`.
- Specs 2 and 3 exist as future layers — do **not** treat their behavior as in-scope. Questions that depend on billing or the credit economy are out of pilot scope; say so rather than answering from the dormant specs.

---

### Domain ambiguity focus (where this product is most underspecified)

When reviewing the spec or answering build questions, scrutinize these CPS hotspots first:

- **Earned-horizon / alignment incentive (SPEC-1 §4)** — edge cases in the earned-booking-horizon calculation and cold-start grace.
- **Owner-cancel "demote standing"** — what exactly changes, and by how much.
- **Notification event definitions** — what triggers each event and what the message contains.
- **Admin permission boundaries** — what Columbia / HOA staff may see vs. not see.
- **Multi-tenant assumptions** — anything baked into Spec 1 that must be confirmed for one-org-per-condo (Bellevue Towers).

---

### CPS spec-file routing (when applying an approved update)

Pick the target file by change subject:

- Pilot behavior → `SPEC-1-pilot.md`.
- Index or key-principle change → `SPEC.md`.
- UX/copy change → the design-pack files (`condoparkshare-design-pack/`).

Edit the relevant section in place; add a separate changelog block only when the change is complex enough to need a before/after explanation. Notify the affected downstream agents per CORE; for CPS that includes the coder and code-reviewer when the change touches in-flight code.
