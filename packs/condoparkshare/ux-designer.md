## CondoParkShare UX-design depth

Apply every item below **in addition to** CORE and the django pack. Do not duplicate items already in either — generic UX audit/classification/escalation process lives in CORE, and generic Django/HTMX design-token, form-error, and partial-pattern idioms live in the django pack. This region is the actual CondoParkShare design pack: its concrete brand, the feature inventory, and the file paths.

---

### Design-pack files you own (concrete paths)

CORE resolves these from `config.sh`; for CPS they are:

- `Specs/condoparkshare-design-pack/DESIGN.md` — canonical design rules + the visual brief ("calm trustworthy utility").
- `Specs/condoparkshare-design-pack/css/tokens.css` — design tokens + base component classes.
- `Specs/condoparkshare-design-pack/style-guide.html` — rendered component reference.
- `Specs/condoparkshare-design-pack/feedback-states.html` — error / warning / success / info reference.
- Readiness output: `docs/design/UX-DESIGN-READINESS.md` (you write this at project start).

Spec inputs: `Specs/SPEC-1-pilot.md` (§§3–11 are the user-visible features) and `docs/pm/CONFIRMED-REQUIREMENTS.md` (authoritative requirements supplement — read first if present).

---

### Brand tokens (structural — never change without human approval)

The core palette is the brand. Changing any of these is a structural/brand escalation:

- `--pine` (#204034) — identity/structure only (headers, logo). **Pine is never a parking signal.**
- `--meadow` (#2e9e63) — the availability SIGNAL: available state + primary action. `--meadow-ink` is its accessible-text variant.
- `--clay` (#bc7a4e) — status only: booked/occupied, used sparingly. `--clay-ink` is its text variant.
- Typeface: Hanken Grotesk (body) + Spline Sans Mono (data). Changing either is a brand escalation.

---

### The color firewall (CPS's defining design invariant)

Enforce these separations on every additive token and every audited template — violating them is a structural change, not additive:

- **Signal vs. status vs. identity are disjoint hue families.** Availability signals borrow Meadow; booked/occupied status borrows Clay; identity/structure is Pine. A token in one role must never reuse another role's hue.
- **Admin/lifecycle badges (Active, Listed, Paused, Expired) are neutral + Pine only — never Meadow/Clay.** Live/ongoing uses a filled Pine dot (`--status-live`); these differentiate by lightness, not by borrowing a parking signal.
- **Success deliberately shares the Meadow family** (`--success` = Meadow) and **info shares Pine** (`--info` = Pine) — no new hues for feedback states. Warning/alert is amber, separated from Clay by lightness (L* gap ~16), and is alert-only.
- **Donation framing stays mono/Pine — never Meadow.** Donating spot time is not "availability," so it must not borrow the availability signal.

When a coder/reviewer requests a color that would cross any firewall line, treat it as structural and escalate; do not fill it as additive.

---

### Leaderboard / recognition flourish

- The recognition metals (medallion bronze/silver/gold) are **the one allowed visual flourish**. They are **always a metal gradient on a medallion, never a flat fill** — the gradient finish is itself the firewall that keeps bronze distinct from flat Clay (booked).
- Leaderboard ordering is driven by the earned-horizon metric; the display must show progress, the cold-start grace state, and medals without implying parking availability (no Meadow on leaderboard data).
- Donation framing on the leaderboard stays mono/Pine.

---

### Typography rule: Spline Sans Mono is data-only

- `.mono` / `.spot-id` / `.data` (Spline Sans Mono) is for **data labels only** — spot IDs, times, credits/horizon counts. Never body copy, never headings.
- Bay-bracket styling is **restrained** — the bay/spot card is the product's core object; do not let bracket ornamentation compete with the data it frames. Flag over-styled bays as a pack-conformance issue for ui-reviewer.

---

### Component classes to define/maintain (CPS-named)

Apply and extend these exact classes; derive any new component's name from the same pattern:

- Spot status: `.badge-available` (Meadow family, `e4f4ea` bg) / `.badge-booked` (Clay family, `f6e9df` bg), each with a `.dot`.
- Lifecycle (admin) badges: neutral/Pine, distinct class set from the status badges above.
- Core object: `.spot` card + `.bay`.
- Actions: `.btn-primary` (Meadow), `.btn-secondary` (Pine), `.btn-ghost`.
- Data: `.mono` / `.spot-id` / `.data`.

---

### Feature inventory to audit (SPEC-1 §§3–11 — derive states from spec, fill per CORE)

Walk these CPS flows in the initial audit; each must have every UI state documented before technical-design runs:

- **Spot card** — available, booked, listing-not-yet-available.
- **Booking flow** — confirmation; the three gate-blocked states (horizon-not-met, one-active-booking, full-overlap); success; cancellation.
- **Listing flow** — availability-window creation; active / paused / expired listing.
- **Owner-cancel flow** — penalty acknowledgment + confirmation.
- **Auth** — login, TOTP enrollment, TOTP verification, recovery-code use, locked-out.
- **Onboarding** — invite link, approval-pending, account-activated.
- **Earned-horizon / leaderboard** — progress, cold-start grace, medal display, donation framing.
- **HOA/manager portal** — resident list, booking history, audit-log view.
- **Operator console** — cross-tenant navigation, tenant summary (multi-tenant; one org per condo/HOA, resolved by hostname).
- **Right-to-erasure** — confirmation + post-erasure state.
- **Notifications** — email and web-push copy patterns.

---

### Voice and tone (CPS brief)

- Brief is "calm trustworthy utility": plain active verbs, sentence case, error messages tell the user what to do next.
- The gate-blocked messages (horizon-not-met, one-active-booking, overlap) must explain *why* and the path forward (e.g. list a spot to earn more horizon) — not just deny — consistent with the cooperative, donation-framed product.

---

### CPS escalation specifics

- **Brand escalation (→ human):** any change to `--pine`/`--meadow`/`--clay` core tokens, the Hanken Grotesk / Spline Sans Mono typefaces, or the "calm trustworthy utility" brief.
- **Firewall breach (→ human, structural):** any token or pattern that crosses signal/status/identity hue families, paints a lifecycle badge in Meadow/Clay, flat-fills a leaderboard metal, or puts Meadow on donation data.
- Everything else follows CORE's additive/clarifying classification and the django pack's token/contrast/notification mechanics.
