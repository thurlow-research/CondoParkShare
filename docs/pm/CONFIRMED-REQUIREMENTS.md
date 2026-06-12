# CondoParkShare — Confirmed Requirements
*PM Q&A session — June 2026. Supplements SPEC-1-pilot.md. All items here take precedence over ambiguous or underspecified language in the spec.*

---

## A — Alignment incentive & standing

**A1 — Owner-cancel penalty mechanism**
There is no separate "standing" field. The only penalty mechanism is: when an owner cancels a booking on their listing, the hours of that cancelled booking are deducted from the owner's elapsed listed hours, directly reducing their earned horizon. No other penalty. No penalty for cancelling a listing that has no booking on it.

**A2 — No-shows**
No-shows are not a concept in the pilot. A booking is assumed taken if not cancelled before its start time. No check-in, no no-show detection, no tracking.

**A3 — Recurring listing patterns**
Deferred. Simple date-range entry is sufficient for launch. Revisit if it proves to be a problem post-launch.

---

## B — Booking behaviour

**B4 — Booking boundary buffer**
A 1-hour buffer is required on both sides of every booking on the same spot. If booking A ends at 14:00, the earliest booking B can start on the same spot is 15:00. Buffer purpose: grace time for late departures. Config field `booking_buffer_hours` exists on `Organization` but is **fixed at 1 hour for the pilot** — the field is reserved for future tenants. See ADR-001 for what a configurable implementation would require.

**B5 — Minimum bookable slot**
Buffer is symmetric (1 hour before + 1 hour after). Minimum available window to accommodate a 1-hour booking = 3 hours. Slots shorter than 3 hours do not appear in search results or get assigned.

**B6 — Early release**
Residents may release a booking at any time with no minimum hold period. Release is in whole-hour increments from the next hour boundary only. The partial hour currently in progress cannot be released. Released hours return immediately to inventory.

---

## C — Notifications

**C7 — Full notification event matrix**
Replaces the partial event list in §5 of SPEC-1.

| Event | Owner receives | Borrower receives |
|---|---|---|
| Booking confirmed | ✓ (spot loaned) | ✓ |
| Booking starts | — | ✓ |
| 30-min warning before end | ✓ | ✓ |
| 15-min warning before end | ✓ | ✓ |
| Booking completed | ✓ | ✓ |
| Borrower cancels their booking | ✓ (spot freed) | ✓ (confirmation) |
| Owner cancels a booking (with optional reason) | ✓ (confirmation) | ✓ (notice + reason) |
| Early release confirmed | ✓ | ✓ |

Notifications fire via three scheduled jobs at `:00` (starts + completions), `:30` (30-min warning), and `:45` (15-min warning) past every hour.

**C8 — Notification defaults and opt-out rules**
- Default: email on, push off.
- Push notifications: fully user-controlled; can be turned off entirely.
- Email notifications: operational emails cannot be turned off. This is legally sound under GDPR Article 6(1)(b) (necessary for performance of contract), CAN-SPAM transactional exemption, and CASL implied consent. Disclosure in privacy notice at signup is required and sufficient.
- Marketing emails: separate opt-in field (`marketing_email_opted_in` on `User`, default `false`). Consent captured at registration. Unsubscribe mechanism required.
- Email backend: configurable via environment variables (`django-anymail`). Provider selected at deployment time; no code changes needed to swap providers.

**Operational emails (non-optional):** all events in the C7 matrix above, plus account invite, TOTP enrollment, recovery codes, HOA admin actions affecting the user.

---

## D — Authentication

**D10/D11 — TOTP enrollment and recovery**
TOTP enrollment is mandatory before a new account is usable. Invited residents must complete enrollment before they can access the app.

Recovery path if TOTP device is lost:
1. User clicks "Lost access to my authenticator" on the login screen.
2. System sends a one-time code to the user's registered email.
3. User enters the email OTP → gains temporary access.
4. User is forced to re-enroll TOTP before full access is restored.

Recovery codes (see D12) are the first-line fallback. Email OTP is the backstop when both TOTP and recovery codes are unavailable.

**D12 — Recovery codes**
10 recovery codes generated at TOTP enrollment. Each code is single-use. Users can regenerate codes (invalidates all previous codes).

**D13 — Registration mode "both"**
`registration_mode = both` means the building accepts either registration path:
- Invite link → auto-active on TOTP enrollment completion.
- Self-registration → account lands in `pending` status, requires HOA admin approval to become active.

---

## E — Admin surfaces

**E14 — Operator impersonation**
Impersonation grants full access — operator can perform any action as the impersonated user (book, list, cancel, update preferences). Prominent warning dialogs required before any destructive action taken during impersonation. All actions taken during impersonation are logged against the operator identity in `AdminAuditLog`, not the resident's. Impersonation is silent to the resident (no notification). Legal basis: disclosed in privacy notice at signup ("platform staff may access your account to resolve issues"). This is standard SaaS practice and does not require per-incident user notification under GDPR.

**E15 — Invite pre-tag and spot registration**
Invite links pre-fill the unit number field at registration. This acts as a verification check to prevent residents from registering under the wrong unit. Residents self-declare their parking spot number(s) during registration. Self-declared spots require HOA admin approval before they can go live and be listed. Residents may own and list multiple spots. This adds a `pending` status to `ParkingSpot` and a spot-approval queue to the HOA portal.

**E16 — Disputes / support tickets**
No formal dispute workflow. Residents have a simple "Contact admin" form. Message is relayed by email to the HOA admin's configured support address (`support_email` on `Organization`). No in-app ticket state, no external ticketing service (GDPR surface and unnecessary complexity for pilot scale). Columbia Hospitality handles tickets in their existing tools.

---

## F — Data & display

**F17 — Parking spot identifier**
`spot_number` is a string field (e.g. `"P3076"`). Accepts alphanumeric values. Shown to the borrower after booking is confirmed so they can locate the spot. An optional free-text `notes` field is retained for admin annotations (e.g. "near elevator, column B") but is not required.

**F18 — Phone number and resident-to-resident communication**
Phone numbers and email addresses are never shown to other residents. Communication between owners and borrowers is via an email relay messaging system:
- Owners may send a message to any resident who has booked their spot.
- Bookers may send a message to the owner of the spot for their booking.
- System sends email on behalf of the sender, hiding real email addresses from both parties.
- Emails include a reply link → reply form in the app → relayed back by the system.
- Rate limited: maximum 10 messages per user per booking.
- Reply tokens expire when the booking ends (cancelled, released, or completed).
- No in-app message history or thread view. Purely email-based relay.

**F19 — Spot discovery and assignment (STRUCTURAL CHANGE)**
**This replaces the search-and-pick flow described in §11 of SPEC-1.**

Discovery is simplified: the user specifies a time window (from → to); the system finds available spots and assigns one. The user does not browse or choose from a list of spots.

Assignment algorithm: rotate across owners as fairly as possible. When multiple spots are available for the requested window, assign the spot belonging to the owner who has gone the longest since their spot was last booked. If an owner has multiple available spots, pick one of theirs.

User sees the assigned spot number and confirms or cancels. If the user cancels the assignment (does not want that spot), the system does not re-assign from a different owner in the same session — the user simply tries a different time window.

Assignment is **tentative** until confirmed. The spot is held for 5 minutes; if the resident does not confirm within that window, the hold is released and the spot becomes available again.

---

## New features (not in original spec)

**NEW-1 — Email relay messaging (from F18)**
A lightweight owner↔borrower communication channel using email relay. No phone exposure. Rate limited. No in-app history. Reply-via-form. See F18 above for full details.

**NEW-2 — "Lost authenticator" recovery flow (from D10)**
Email OTP as fallback when both TOTP device and recovery codes are unavailable. Forces TOTP re-enrollment on use. OTP expires after **15 minutes**. Single-use. See D10/D11 above for full details.

**NEW-3 — Marketing email opt-in (from C8)**
`marketing_email_opted_in` boolean field on `User`, default `false`. Consent captured and displayed at registration. Unsubscribe link in all marketing emails. Operational emails unaffected.

**NEW-4 — Spot pending approval flow (from E15)**
`ParkingSpot.status` includes a `pending` state. Self-declared spots at registration start as `pending`. HOA admin approves via portal before spot goes live. Adds approval queue to HOA portal.

---

## Deferred items

- **Recurring listing patterns (A3)** — deferred to post-launch. Simple date-range entry for pilot.
- **Leaderboard UI** — data tracked, UI deferred (per original spec).
- **Marketing email content** — opt-out mechanism required before any marketing email is sent; content TBD.
