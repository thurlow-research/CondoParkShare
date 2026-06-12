# CondoParkShare — Design Instructions for Claude Code

This pack is the **visual/UX source of truth** for the CondoParkShare build. Pair it with `SPEC.md` (functional spec). When building any UI, follow this document; do not invent a separate visual language.

## What's in this pack
```
condoparkshare-design-pack/
├── DESIGN.md            ← you are here (how to apply the system)
├── style-guide.html     ← rendered visual reference (open in a browser)
├── feedback-states.html ← rendered reference for error / warning / success / info
├── css/
│   └── tokens.css       ← design tokens + base component classes (load first)
└── logo/
    ├── logo-mark.svg          ← mark only (favicon/app icon, tight spaces)
    ├── logo-mark-reversed.svg ← mark for dark/pine backgrounds
    ├── logo-full.svg          ← full wordmark lockup
    └── favicon.svg            ← mark on a pine tile (browser tab / PWA icon)
```

## The brief in one line
A **calm, trustworthy utility** that feels **warm and neighborly**, for residents of high-end condo buildings. Restrained and precise like good signage — never flashy, salesy, or cute. Utility first; community close behind.

## Core idea: color carries meaning
The one rule that runs through everything: **Meadow green = available, Clay = booked.** Use these consistently on badges, spot cards, the lot grid, and any availability state. Keep everything else neutral so these signals never compete. Don't use green or clay decoratively.

## Setup

### 1. Fonts
Load **Hanken Grotesk** (UI) and **Spline Sans Mono** (data) — self-host in `static/fonts/` for production, or during early dev use Google Fonts:
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@400;500;600;700;800&family=Spline+Sans+Mono:wght@400;500;600&display=swap" rel="stylesheet">
```

### 2. Tokens
Copy `css/tokens.css` to `static/css/tokens.css` and load it **before** any page CSS. Every color, font, radius, and base component class comes from here. **Never hard-code hex values in templates** — always reference the CSS variables (`var(--pine)`, `var(--meadow)`, etc.) or the provided classes.

### 3. Favicon / PWA icon
Use `logo/favicon.svg` for the browser tab and the PWA/home-screen icon.

## Color tokens
| Token | Hex | Use |
|---|---|---|
| `--pine` | #204034 | Primary. Headers, logo, trust. |
| `--pine-700` | #2c5848 | Primary hover/elevated. |
| `--eucalyptus` | #5e9079 | Secondary, focus rings. |
| `--meadow` | #2e9e63 | **Available** + primary action. |
| `--meadow-ink` | #1d6b43 | Meadow as text (accessible). |
| `--clay` | #bc7a4e | **Booked** / occupied (sparingly). |
| `--ink` | #18211c | Primary text. |
| `--slate` | #586059 | Secondary text. |
| `--line` | #dbe3d9 | Borders / hairlines. |
| `--mist` | #e9efe6 | Subtle fills, tags. |
| `--canvas` | #f3f6f0 | Page background. |
| `--white` | #ffffff | Cards / surfaces. |

Semantic aliases also exist: `--bg, --surface, --text, --text-muted, --border, --available, --booked`. Prefer these in app code.

## Feedback & system states (error / warning / success / info)
Feedback colors are added so they **never compete with the availability signal**. See `feedback-states.html` for the rendered reference. Each state has a base, an accessible `-ink` (text/AA), and a `-surface` + `-line` (banner fill/border).

| State | Tokens | Notes |
|---|---|---|
| **Error / danger** | `--danger #c43d2f`, `--danger-ink`, `--danger-surface`, `--danger-line` | The one **new hue** — a warm brick red for failures + destructive actions. Nothing else in the palette reads "stop". |
| **Warning** | `--warning #e0ad3a`, `--warning-ink #8a5d0c`, `--warning-surface`, `--warning-line` | A light gold, separated from **Clay by lightness, not just hue** (L\* gap ~16) so it stays distinct under every color-vision type incl. tritanopia, and in grayscale. White on the light base fails contrast → the warning icon-chip sits on `--warning-ink`. |
| **Success** | `--success` = `--meadow`, `--success-ink` = `--meadow-ink`, `--success-surface #e4f4ea`, `--success-line` | Reuses the **Meadow** family on purpose — green already means good/go, so "Booked." is a green moment. |
| **Info** | `--info` = `--pine`, `--info-ink` = `--pine`, `--info-surface #eaeef0`, `--info-line` | Stays **neutral** (no foreign blue) so the green availability lane never competes. |

**Rules.** Clay is **status-only** (booked); amber is **alert-only** (warning) — never interchangeable. Never use red decoratively (if it's not an error, it's not red). Always pair a state with an icon + text label, never color alone. Components in `tokens.css`: `.alert` (`.alert-danger/-warning/-success/-info`), `.btn-danger`, `.badge-warning/.badge-danger`, `.field.has-error` + `.error-text`, `.toast` (`.toast-success/-danger`).

## Administrative / lifecycle statuses (Active, Pending, Completed, Inactive, Listed)
These are **metadata, not parking availability** — so they must **never borrow Meadow/Clay**. They're built only from neutrals + **Pine** (Pine is identity/structure, never a signal) and differentiated by **weight + dot shape**, not hue — so the tier reads in grayscale and under color blindness. Four treatments carry every label:

| Lifecycle energy | Statuses | Class · treatment |
|---|---|---|
| **Live / ongoing** | Active, **Listed** | `.badge-active` — filled **pine** dot on mist |
| **Waiting** | Pending approval | `.badge-pending` — hollow **ring** dot (`<span class="ring">`), hairline border, white fill |
| **Done** | Completed | `.badge-complete` — a **check** glyph (`<span class="chk">✓</span>`) in slate on mist |
| **Dormant** | Inactive | `.badge-inactive` — lowest contrast, hollow dot, no fill |

**Rules.** `Listed` ≠ `Available`: *Listed* is lifecycle (neutral, `.badge-active`), *Available* is the Meadow signal — the same spot shows both at once, so neither can be green. Don't promote `Pending` to amber; reserve amber for genuine cautions. Tokens: `--status-live` (pine), `--status-ink` (slate), `--status-surface` (mist), `--status-line`, `--status-off`.

## Recognition & leaderboard (gamification)
Recognition is a **third category** and gets the system's *one* allowed flourish. The palette is **metal + Pine**, never the signal hues. The firewall is **finish, not hue**: metals are **always a gradient on a medallion disc, never a flat fill** — that's how bronze stays distinct from flat Clay (booked) and gold from flat Warning amber.

| Rank | Class | Gradient (hi → mid → lo) · text |
|---|---|---|
| **Champion** | `.medal.m-gold` | `--gold-hi #f0d98a → --gold #c9a227 → --gold-lo #9c7b16` · `--gold-ink` |
| **2nd** | `.medal.m-silver` | `--silver-hi → --silver #c7ced3 → --silver-lo` · `--silver-ink` |
| **3rd** | `.medal.m-bronze` | `--bronze-hi → --bronze #b97f4e → --bronze-lo` · white |

**Rules.** Top 3 only get metal; everyone below stays **neutral** (mono rank numbers, Pine stats) so the podium glows by contrast — one celebratory moment per screen, mirroring "spend boldness in one place." Donation data stays **mono/Pine, never Meadow** ("124 hrs shared"). The champion is framed with the **bay-bracket** signature (`.podium-first`) — on-brand prestige, not party graphics. Tone: frame it as *"the neighbors who shared the most"* and *hrs/spots shared*, not points or "winners" — community esteem, not competition.

## Typography
- **Hanken Grotesk** for everything in the UI. Weights: 800 display, 700 H2, 600 H3/labels, 500 emphasis, 400 body. Headings use tight tracking (`letter-spacing:-.02em`), sentence case.
- **Spline Sans Mono** for data the product labels: **spot IDs** (`P2-114`), **times** (`09:00–12:00`), **credits** (only if Part B is enabled), permit-like values. Apply via `.mono` / `.spot-id` / `.data`. This is the one place mono appears — it makes parking data unmistakable and evokes permits/stencilled bay numbers.

## Components (classes in tokens.css)
- **Buttons:** `.btn` + `.btn-primary` (green, the main action — "Book this spot"), `.btn-secondary` (pine — "List my spot"), `.btn-ghost` (cancel/tertiary). One primary action per view.
- **Status badges:** `.badge.badge-available` and `.badge.badge-booked`. Always pair the color with a text label (accessibility — never rely on color alone).
- **Spot card** (`.spot`, `.spot.is-available`): the product's core object. Shows the mono spot ID in a `.tag`, the time window, owner line, status badge, and a Book action. Mirror the markup in `style-guide.html` → Components.
- **Forms:** `.field` with label + input; green focus ring.
- **Signature motifs:**
  - `.bay` — the parking-bay corner brackets. Use to frame an available spot, an empty state, or the logo. Don't overuse — it's a punctuation mark, not a border on everything.
  - `.lot` with `<i class="open">` / `<i class="busy">` — the garage availability grid. Use for level/lot overviews.

## Logo usage
- Default: `logo-full.svg` (or build the lockup in HTML: the mark SVG + wordmark text where "Park" is weight 800 and "Share" is `--meadow-ink`).
- Dark/pine backgrounds: `logo-mark-reversed.svg` or the reversed wordmark (brackets in `#8fe0b3`, spot in meadow).
- Tight spaces / icons: `logo-mark.svg`; app/tab icon: `favicon.svg`.
- Keep clear space around the mark equal to the height of the inner green spot. Don't recolor the wordmark beyond the defined scheme, stretch, or add effects.

## Voice & tone (write all UI copy this way)
- **Plain, active, neighborly.** Name things by what people do: "List your spot while you're away," "Book this spot," not "Submit availability" or "Utilize the booking module."
- **One name per action, kept through the flow:** the button "Book this spot" → toast "Booked."
- **Errors** explain what to do next, in the interface's voice, no apologizing: "No spots open then. Try a wider window." not "Oops! Something went wrong."
- **Empty states invite action:** "List the first spot in your building."
- **Never** salesy or system-y: avoid "monetize," "asset," "module," "leverage." Sentence case everywhere.

## Quality floor (non-negotiable)
- Responsive down to mobile (the building's residents are on phones).
- Visible keyboard focus (`:focus-visible` ring is in tokens.css — keep it).
- Respect `prefers-reduced-motion` (handled in tokens.css; don't override).
- Color is never the only signal — pair status colors with text/icon.
- Meet WCAG AA contrast: use `--meadow-ink`/`--clay-ink` for colored text on light backgrounds, not the base `--meadow`/`--clay`.

## Restraint
Spend boldness in one place: the green availability signal and the bay-bracket signature are the memorable elements. Keep everything else quiet — generous whitespace, hairline borders, few shadows. If a screen feels busy, remove an accent before adding one.

## Change log
<!-- ux-designer appends one line per additive/clarifying change: YYYY-MM-DD | what was added | requested by -->
<!-- Structural changes require human approval and are recorded here after approval. -->
