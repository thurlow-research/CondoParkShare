# UX Design Readiness

*HOS re-validation sweep completed: 2026-06-13. Branch: validation/hos-resweep.*
*This document evaluates the existing build against the design pack — it does not regenerate the pack.*

---

## Coverage summary

| Feature area | States documented in pack | Gaps found | Status |
|---|---|---|---|
| Spot card | available, booked | none | Covered |
| Booking flow — search / request | form, inline errors | none | Covered |
| Booking flow — confirmation / hold | tentative hold (book_confirm) | none | Covered |
| Booking flow — gate blocks (horizon, one-active, overlap) | alert-danger pattern exists | copy patterns not named | Covered by alert-danger |
| Booking flow — success | toast-success + alert-success | none | Covered |
| Booking flow — cancellation (borrower) | btn-danger + alert-danger pattern | none | Covered |
| Booking flow — owner-cancel penalty | warning-notice (custom class) | `.warning-notice` not in pack | **GAP — see item 2** |
| Booking flow — early release | bare HTML (no design) | unbranded template | **GAP — see item 5** |
| Listing flow — add availability window | form field, inline errors | none | Covered |
| Listing flow — remove availability window | btn-danger, alert-danger | none | Covered |
| Spot card lifecycle states (Listed/Active/Inactive/Pending/Completed) | badge-active, badge-pending, badge-complete, badge-inactive | none | Covered |
| Authentication — login form | field, btn-primary, alert-danger | none | Covered |
| Authentication — TOTP enroll | field, steps-list (custom), qr-frame (custom) | `.steps-list`, `.qr-frame`, `.manual-entry` not in pack | Functionally OK; not documented |
| Authentication — TOTP verify | field, btn-primary, alert-danger | none | Covered |
| Authentication — recovery code | field, recovery-notice (custom) | `.recovery-notice` not in pack | Functionally OK; not documented |
| Authentication — lost authenticator (email OTP) | field, security-notice (custom) | `.security-notice` not in pack | Functionally OK; not documented |
| Onboarding — invite register | bare HTML (no design) | unbranded template | **GAP — see item 5** |
| Onboarding — self-register (approve mode) | bare HTML (no design) | unbranded template | **GAP — see item 5** |
| Onboarding — approval pending | bare HTML (no design) | unbranded template | **GAP — see item 5** |
| Onboarding — invite invalid | bare HTML (no design) | unbranded template | **GAP — see item 5** |
| HOA portal — resident list, approve, block, unblock | bare HTML for most | unbranded templates | **GAP — see item 5** |
| HOA portal — invite list, invite create | bare HTML | unbranded templates | **GAP — see item 5** |
| HOA portal — bookings, spot list, reports | bare HTML | unbranded templates | **GAP — see item 5** |
| HOA portal — resident detail | extends base.html, badge-active/pending/danger | badge-danger for "Blocked" user | **GAP — see item 1** |
| Notifications — message send/sent/error/reply | bare HTML | unbranded templates | **GAP — see item 5** |
| Earned-horizon / leaderboard | medal tokens, podium-first | leaderboard UI explicitly deferred | Not in scope per SPEC §1 |
| Right-to-erasure | no template found | no template exists yet | Not yet built |
| Operator console | no templates in build | Django admin only | Not yet built |
| Error pages — 404, 403, 500 | no custom templates found | unbranded Django defaults | **GAP — see item 4** |
| Impersonation banner | `.impersonation-banner` in base.html | not documented in pack | **GAP — see item 3** |
| `.badge-pending` / `.badge-inactive` border rendering | border-color only set in tokens.css | `border-style` and `border-width` missing | **GAP — see item 6** |
| `.badge-inactive` contrast | fixed to `--status-ink` (6.5:1) in both tokens files | canonical claude.ai source not yet fixed | **GAP — see item 7** |

---

## Gaps — detailed findings

### Item 1 — `badge-danger` used for "Blocked" user status (design pack inconsistency)

**Where:** `accounts/templates/accounts/profile.html` line 31, `portal/templates/portal/resident_detail.html` line 54.

**What:** Templates use `<span class="badge badge-danger">` to show the "Blocked" user account status. The design pack does not define this usage. The pack's rules state: "Use `--danger` red only for failures and destructive actions" (feedback-states.html Rules section). A blocked resident is a lifecycle/administrative state, not a system failure — by the pack's own classification it belongs in the neutral lifecycle tier (`.badge-inactive` or a new `.badge-blocked`).

**Classification:** Additive gap — the pack documents four lifecycle badges (active, pending, complete, inactive) but has no variant for the blocked state, so implementors reached for the nearest red badge.

**Recommendation:** Add a `.badge-blocked` variant to `tokens.css` and document it in DESIGN.md §Administrative/lifecycle statuses. It should use danger tokens since blocking is a punitive action distinguishable from mere dormancy, but must be documented explicitly so it is not confused with system errors. Alternatively, accept the current usage and add a rule to DESIGN.md stating "blocked accounts use `.badge-danger`; this is the one administrative state that borrows the danger tier because blocking is an action taken on a user, not a parking event."

---

### Item 2 — `.warning-notice` class used in booking_cancel but undefined in design pack

**Where:** `parking/templates/parking/booking_cancel.html` line 18.

**What:** `<div role="alert" class="warning-notice">` is used for the owner-cancel penalty acknowledgment. This class is not defined anywhere in `tokens.css`, `static/css/tokens.css`, or any design pack file. The template also has no `{% extends "base.html" %}`, so `tokens.css` is not loaded at all — the warning-notice div renders with no styling in the current state.

**Classification:** Two problems: (a) undocumented class name, and (b) the template is completely unbranded.

**Recommendation:** The owner-cancel penalty warning should use `.alert.alert-warning` from the existing feedback system. Document the owner-cancel penalty acknowledgment pattern in DESIGN.md Voice & Tone section with the copy pattern: "Cancelling this booking will reduce your listing standing. Only cancel if it's necessary." This is a warning, not a danger — the `.alert-warning` component is the correct fit. The template also needs to be converted to extend `base.html`.

---

### Item 3 — `.impersonation-banner` in base.html is undocumented in the design pack

**Where:** `templates/base.html` lines 224–250.

**What:** The impersonation banner is a fully styled component (warning-surface background, warning-ink text, pine-color link) implemented as inline CSS in base.html. It is not documented in `tokens.css` as a component class, and DESIGN.md has no mention of the impersonation state.

**Classification:** Additive gap — the component exists and works, but has no design pack documentation.

**Recommendation:** Add a `.impersonation-banner` component class to `tokens.css` (it already uses existing tokens correctly) and add a rule to DESIGN.md stating its purpose: "A top-of-page persistent warning strip, always `.warning-surface` background, for operator impersonation sessions. Use `role="alert"` and `aria-live="polite"`."

---

### Item 4 — No custom 404, 403, 500 error pages

**Where:** No custom error templates found anywhere in the project (only Django's defaults in `.venv`).

**What:** Django's default error pages use no application branding. For a deployed product, residents who hit a 404 or 500 will see an unstyled Django page.

**Classification:** Additive gap — the design pack documents empty states ("List the first spot in your building") but not system error pages.

**Recommendation:** Add `templates/404.html`, `templates/403.html`, and `templates/500.html` extending `base.html`. Copy pattern per DESIGN.md Voice & Tone rules: errors explain what to do next. Examples: "That page doesn't exist — try searching for a spot." (404); "You don't have access to that page." (403); "Something went wrong on our end. Try again in a moment." (500). These should use `.alert.alert-danger` for the 500 and `.alert.alert-info` for 404/403.

---

### Item 5 — 23 templates are completely unbranded (no `tokens.css`, no design system)

**Where:** See the full list in the Coverage Summary above. Includes all portal management pages, most notification templates, both registration flows, booking_cancel, booking_release, and invite-related pages.

**What:** These templates render as bare `<h1>` + `<form>` with no `tokens.css` load, no `base.html` extension, and no design pack classes. They are functionally correct but visually inconsistent with the designed surfaces.

**Classification:** This is a gap in build completeness, not a design pack gap. The design pack already provides all necessary patterns for these views (forms use `.field`, admin tables use `.card`/`.panel`, confirmations use `.alert-warning`/`.alert-danger`, buttons use `.btn`). No new design pack tokens are needed to address this.

**Recommendation:** Each unbranded template should be converted to extend `base.html`. This is a coder task. The design pack is complete for these views; no new tokens are needed before conversion can begin.

---

### Item 6 — `.badge-pending` and `.badge-inactive` in tokens.css set `border-color` without `border-style`/`border-width`

**Where:** `Specs/condoparkshare-design-pack/css/tokens.css` lines 158 and 161. Also `static/css/tokens.css` same lines.

**What:**
```css
/* tokens.css */
.badge-pending{background:var(--white);color:var(--status-ink);border-color:var(--status-line)}
.badge-inactive{background:transparent;color:var(--status-ink);border-color:var(--status-line)}
```

The base `.badge` rule does not set `border` at all. Setting only `border-color` on an element with no prior `border-style`/`border-width` declaration means no border renders — `border-color` alone has no visual effect without `border-style:solid` and a non-zero `border-width`.

By contrast, `feedback-states.html` (the reference file) correctly uses `border:1px solid var(--status-line)` for `.badge-pending`.

**Classification:** This is a bug in `tokens.css`. The design intent (a visible 1px hairline border distinguishing these from filled badges) is documented and correct; only the CSS implementation is wrong.

**Recommendation:** Fix both rules in `tokens.css` (and keep `static/css/tokens.css` in sync):
```css
.badge-pending{background:var(--white);color:var(--status-ink);border:1px solid var(--status-line)}
.badge-inactive{background:transparent;color:var(--status-ink);border:1px solid var(--status-line)}
```

This is a design pack correction, not a structural change.

---

### Item 7 — `.badge-inactive` contrast fix applied in-repo but canonical claude.ai pack not updated

**Where:** Memory note from session `bb3add26` (2026-06-12). Both `Specs/condoparkshare-design-pack/css/tokens.css` and `static/css/tokens.css` have already been corrected to use `color:var(--status-ink)` (#586059, 6.5:1 contrast on white). This fix is present on the current branch.

**What:** The original `--status-off` (#8b938b) value gives only 3.16:1 contrast on white — below the 4.5:1 WCAG AA minimum for the 12px/600 badge text. The in-repo fix is correct. However, per the memory note, the canonical claude.ai Design System project has not been updated, and the next DesignSync pull would silently revert this fix.

**Classification:** Informational — the in-repo state is correct, but the fix is at risk of being overwritten.

**Recommendation:** Before any future DesignSync pull from the canonical claude.ai source, verify that `.badge-inactive` uses `--status-ink` (or a darkened `--status-off` ≥ 4.5:1, e.g. #767676 or darker) in the incoming pack. Do not accept a sync that reverts this fix.

---

## Additional observations (not gaps but noted)

**Auth-flow component classes not in pack:** `.steps-list`, `.qr-frame`, `.manual-entry` (TOTP enroll), `.recovery-notice`, `.security-notice` (recovery/lost authenticator flows) are inline-CSS components in their respective templates. They use design tokens correctly. They are not in `tokens.css` as reusable classes, which is fine for one-off auth flows — but if the auth shell ever needs to be restyled, these will need to be located across multiple template files. Low priority.

**`.form-error` class used in bare templates without style:** Several unbranded templates output `<p class="form-error">{{ error }}</p>`. This class is not in `tokens.css`. Since these templates also lack `tokens.css`, the form-error paragraphs render with no styling. When these templates are converted to extend `base.html` (Item 5), a `.form-error` rule should be added to `tokens.css` (equivalent to `.error-text` but for `<p>` wrappers rather than `.field` children).

**`booking_cancel.html` owner-cancel path is both unbranded and uses `.warning-notice` (undefined class):** This is the most functionally impactful unbranded template because it is the gate for a consequential user action (owner-cancel with standing penalty). It should be prioritized for design adoption.

---

## Open structural questions

None. All gaps are additive or clarifying. No core brand tokens, component removals, or paradigm changes are required.

---

## Design pack status

The design pack as of 2026-06-13 covers all user-visible states in SPEC-1, with the following caveats:

1. **Item 6 (badge border bug) must be fixed in `tokens.css`** before unbranded templates are converted, or the ring/hollow badge visual distinction will not render.
2. **23 templates are unbranded** — the design pack is complete for these views, but the build has not yet adopted it for them. This is a coder task.
3. **Item 1 (`badge-danger` for Blocked)** needs a documented rule added to DESIGN.md before the portal templates are fully styled — implementors need an authoritative answer on whether "Blocked" borrows danger or gets its own lifecycle badge.

The architect and technical-design agents may proceed. The coder may proceed on all already-styled surfaces. Before converting unbranded templates, the badge border fix (Item 6) should be applied.
