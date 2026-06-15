## CondoParkShare architecture depth

Apply every item below **in addition to** CORE and the django pack. Do not duplicate items already in either — generic architect process (initial review, ADR production, harsh critique, loop-exit, escalation arbitration, product-boundary checkpoint) lives in CORE, and generic Django architecture idioms (GiST exclusion constraints, scoped managers, encrypted PII libraries, TOTP/recovery-code storage, web push/VAPID, Django Admin extension, Compose topology, settings/secrets) live in the django pack and are not repeated here.

---

### Project inputs (paths are concrete for this project)

- `Specs/SPEC-1-pilot.md` — the pilot product spec; build *now* against this. `Specs/SPEC.md` is the index.
- The pm-agent's confirmed Q&A output — authoritative requirements supplement; read it before any architecture decision.
- ADR output path is fixed: write the initial ADR to **`docs/architecture/ADR-001-pilot.md`**. This is the binding input to `technical-design`.

---

### CPS stack (binding — do not change without human approval)

The stack is decided by the spec, not open for architecture re-litigation. Confirm conformance; do not re-derive:

- Django (Python) + **HTMX**, server-rendered, **no SPA**. (The django pack's HTMX-vs-SPA-vs-DRF decision tree is already resolved here in favor of HTMX — do not reopen it.)
- PostgreSQL with `tstzrange` + GiST exclusion constraint for booking-overlap safety.
- Docker Compose: `web` (gunicorn), `db` (Postgres named volume, internal-only), `caddy` (reverse proxy + TLS).
- Notifications: email + web push (PWA).

A proposal to swap any of these is a human-approval gate, not an architect call.

---

### Earned-horizon metric (the CPS-unique design problem)

This is the domain mechanic the generic packs do not know about. Resolve it explicitly in the ADR:

- A spot owner earns booking horizon by *listing* their spot as available — the more availability they contribute, the further ahead they may book others' spots. The metric feeds both the **horizon gate** on booking creation and the **leaderboard** ordering.
- **Where it runs** is an architecture decision you must make and record: DB function vs. Django signal vs. scheduled `manage.py` command. Per the django pack, long/periodic work does not run synchronously in a view — but *which* mechanism, and how it stays correct, is CPS-specific.
- **180-day rolling window:** elapsed *listed* hours count only past hours within a rolling 180-day window. The computation must be efficient at this window size and must not count future-listed availability.
- **Cold-start grace:** new residents get a baseline horizon at signup so they are not locked out before earning any. Specify the grace and earning curve in the ADR — do not leave it to `technical-design` to invent.

---

### CPS booking invariants (name these in the ADR and the critique)

The django pack establishes *that* exclusion/overlap must be DB-enforced. CPS adds *which* invariants every booking-creation path must satisfy, in order:

- **Horizon gate** — book only within the borrower's earned horizon (metric above).
- **One-active-booking gate** — a borrower holds at most one active booking at a time.
- **Overlap gate** — the `tstzrange` GiST exclusion constraint is the final arbiter, paired with `select_for_update()` so the failure is deterministic, not a race.

When critiquing `technical-design`, verify all three are enforced on *every* booking path, that horizon math counts only past listed hours over the rolling 180 days, and that overlap safety is the DB constraint — not an app-layer check.

---

### CPS multi-tenancy shape

The django pack covers the scoped-manager mechanism. The CPS-specific fact: **one `Organization` per condo/HOA**, resolved by **hostname** in middleware. The operator console and the HOA/manager portal are separate surfaces over the same org-scoped data. Verify the hostname→org resolution is the single tenancy boundary and that no query escapes it.

---

### CPS deploy topology (concrete target)

- Production host **`opus`** (Ubuntu VM, Hyper-V guest on `nexus`), reached at **`parkshare.kumajyo.com`** via the **`*.kumajyo.com` wildcard**, behind Caddy + **dynamic DNS (DDNS)**; TLS via Caddy. The HOA portal is served via a host alias under the same wildcard.
- **Nightly `pg_dump` → NAS** backup is a required operational obligation (route it through the CORE product-boundary checkpoint as an operational burden).

---

### Convergence-failure issue (CPS label/format)

When the design loop hits the CORE round cap, file the issue before escalating, using the CPS convention:

```bash
gh issue create \
  --title "Design concern: [step/area] — convergence failure after 5 rounds" \
  --body "**Step:** [build step]\n**Rounds:** 5\n**Sticking point:** [what did not converge]\n**Attempted approaches:**\n[one line per round]\n**Impact:** [what this blocks]" \
  --label "design-concern"
```

Then escalate per CORE (iteration count, per-round critique/response summary, the unresolved sticking point).
