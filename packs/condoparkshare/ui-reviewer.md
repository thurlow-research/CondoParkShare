## CondoParkShare UI-review depth

Apply every item below **in addition to** CORE and the django pack. Do not duplicate items already in either — generic conformance process, severity model, iteration/escalation, and the Django/HTMX templating mechanics are not repeated here.

---

### Design-pack sources (read both before reviewing)

- `Specs/condoparkshare-design-pack/DESIGN.md` and `Specs/condoparkshare-design-pack/css/tokens.css` — read both completely. Every finding must trace to a DESIGN.md rule or a `tokens.css` token.
- `style-guide.html` is the canonical component reference. When checking a component's structure, match it against `style-guide.html`.

---

### CPS tokens and the meadow/clay restraint

- Prefer the semantic aliases over raw tokens in application CSS: `--bg`, `--surface`, `--text`, `--text-muted`, `--border`, `--available`, `--booked`. Raw-token use where a semantic alias exists is a finding.
- `--meadow` and `--clay` are **not decorative** — they signal availability state only. Flag any `--meadow`/`--clay` use that is not a status badge, a spot-card availability state, or the primary action button. This is the CPS-specific reading of CORE's "restraint with accents" rule.

---

### Copy & layout restraint (CPS specifics)

- **Sentence case everywhere** — no ALL CAPS headings or Title Case navigation labels (unless a proper noun).
- Primary views should feel **spacious** — generous whitespace, hairline borders (`var(--line)`), few or no shadows. Flag views that have multiple card shadows, thick borders, or dense icon clusters.

---

### Spline Sans Mono — scope of the data typeface

- `.mono` / `.spot-id` / `.data` appear **only** on: spot IDs (e.g. `P2-114`), time windows (e.g. `09:00–12:00`), and credit/permit-like values. Any use on headings, body copy, labels, or navigation is **blocking**.
- Hanken Grotesk is the UI font; no other typeface on UI text.
- Heading weights: 800 display, 700 H2, 600 H3/labels — verify the weight class is applied, not left at browser default. Headings carry tight tracking (`letter-spacing: -.02em`); verify it is present.

---

### CPS component contracts

- **Buttons:** `.btn` + exactly one modifier. `.btn-primary` (meadow, main action) at most **once per view** — two `.btn-primary` in one view is blocking. `.btn-secondary` (pine) for secondary; `.btn-ghost` for cancel/tertiary.
- **Status badges:** `.badge.badge-available` / `.badge.badge-booked` must carry a **text label**, not color alone (color-only state is blocking).
- **Spot card** (`.spot`, `.spot.is-available`): the `.spot-id` tag uses `.mono`/`.tag`; time window shown; owner line present; badge present; Book action present. Match `style-guide.html`.

---

### Signature motifs — the restraint rules

- **`.bay` (parking-bay corner brackets):** allowed in exactly three cases — framing an available spot, an empty state, or the logo. Flag **every** `.bay` use and confirm it is one of these three. Not a generic border or decoration.
- **`.lot` with `<i class="open">` / `<i class="busy">`:** only for lot/level overview grids; not repurposed for other lists or grids.
- "If a screen feels busy, remove an accent before adding one." Flag templates with multiple greens, stacked accents, or several `.bay` uses.

---

### CPS voice and tone (copy)

- Action labels plain and active: "Book this spot", "List your spot", "Cancel booking" — not "Submit booking request", "Manage availability", "Initiate cancellation".
- One name per action through the flow: button "Book this spot" → toast "Booked." (not "Reservation confirmed.").
- Errors say what to do next: "No spots open then. Try a wider window." — not "Error: no availability found for selected period."
- Empty states invite action: "List the first spot in your building." — not "No spots available."
- Banned words — flag if found: **monetize, asset, module, leverage, utilize**. Sentence case everywhere.

---

### Logo usage

- `logo-full.svg` (or the HTML lockup) on light backgrounds; `logo-mark-reversed.svg` on pine/dark backgrounds.
- Logo is never recolored, stretched, shadowed, or outlined; clear space around the mark is maintained.

---

### CPS escalation note

- A needed new token or component in `tokens.css` is a **shared dependency** → route to the **architect** (per CORE), not the coder. `tokens.css` is not coder-owned.
