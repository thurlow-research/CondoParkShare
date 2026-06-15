## CondoParkShare accessibility-review depth

Apply every item below **in addition to** CORE and the django pack. Do not duplicate items already in either — generic WCAG AA review process lives in CORE, and Django/HTMX a11y mechanics (form label/error association, messages framework, partial-swap focus, `aria-live`, `hx-indicator`) live in the django pack and are not repeated here.

---

### Primary references (read before reviewing)

- `Specs/condoparkshare-design-pack/DESIGN.md` — the **Quality floor** section is your CPS checklist baseline; its accessibility requirements are non-negotiable build gates.
- `Specs/condoparkshare-design-pack/css/tokens.css` — the authoritative definitions for the focus ring, motion guard, and the color tokens whose contrast you must verify.

---

### CPS token contrast (verify the *-ink variants are used for text)

These specific design-pack tokens fail or sit near AA as text on light backgrounds; flag any text using the base token where the `-ink` variant is required (WCAG 1.4.3):

- `--meadow` (#2e9e63) on `--white` — **fails AA** for normal text. Colored text on light backgrounds must use `--meadow-ink` (#1d6b43).
- `--clay` (#bc7a4e) on `--white` — same failure mode; text must use `--clay-ink`.
- `--slate` (#586059) on `--canvas` (#f3f6f0) — check the ratio is ≥4.5:1 for normal text.
- No inline `style="color:…"` anywhere — tokens only. An inline color bypass is a blocking finding even if it happens to pass contrast.

---

### Color-is-never-the-only-signal on CPS components (named, exact)

The design pack explicitly requires a non-color signal on each of these; verify the text/icon is present, not just the color fill:

- `.badge-available` / `.badge-booked` (spot status) — both must carry a **text label**, not a color fill alone.
- The lot grid — `.lot` with `<i class="open">` / `<i class="busy">` must carry a non-color label or icon accompanying the color state.
- Form error states — error **text** must appear alongside any red border, never the border-color change alone.

---

### CPS view-by-view live audit

When the dev server is running, walk each primary CPS view (Lighthouse + manual tab-through): **spot search, booking form, listing form, login, TOTP enrollment, HOA/manager portal.** Each must be usable at a 375px viewport (iPhone SE) with no horizontal scroll — pay particular attention to the **spot cards, booking form, and the lot grid** at that width.

---

### TOTP and `.mono` specifics

- The TOTP code input must be clearly labeled and use an input type that surfaces a **numeric keypad on mobile** (e.g. `inputmode="numeric"` / `type` allowing numerics), since the enrollment and login flows depend on it.
- **Spline Sans Mono (`.mono`) is for data labels only** — if it is applied to body copy or to text that must be read at length, raise it: condensed mono at small sizes degrades legibility (defer brand-conformance specifics to ui-reviewer, but flag the readability impact).

---

### Reduced-motion guard tied to tokens.css

`prefers-reduced-motion` is handled centrally in `tokens.css`. Verify **no** CSS animation or transition is defined *outside* `tokens.css` without its own `@media (prefers-reduced-motion: reduce)` guard — including any HTMX swap animation. The centralized guard does not cover styles declared elsewhere.

---

### CPS escalation specifics

- **Design-system ambiguity** the design pack cannot settle (e.g. "should the lot grid carry a text legend?") → **human** (design decision).
- **Token/CSS fix** → coder; do **not** modify `tokens.css` without architect/ux-designer approval.
