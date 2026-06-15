## CondoParkShare privacy-review depth

Apply every item below **in addition to** CORE and the django pack. Do not duplicate items already in either — the generic obligations (encrypt-vs-hash, erasure-path-exists, consent-before-signup, PII-access logging, log hygiene) live in CORE, and the Django mechanics (encrypted field types, key-from-env, `on_delete` cascade/anonymization, session flush, admin/logging surfaces) live in the django pack. This file is only the CPS product/domain specifics.

---

### Governing references (read before reviewing)

- `Specs/SPEC-1-pilot.md` **§7** — the data-handling section, your primary reference. Its principle is stated as: *"Hash what you only verify; encrypt what you must read back; minimize collection."*
- `docs/architecture/ADR-001-pilot.md` — the binding encryption/erasure approach (which field-encryption mechanism, TOTP-secret-at-rest, key handling).
- `docs/design/TECHNICAL-DESIGN.md` **§9** — the authoritative data model (the field-by-field source of truth for "is this field in scope?").

---

### Lawful basis & jurisdiction (CPS pilot)

- Target hosting is **EU** (Hetzner EU is the future path; pilot runs on the `opus` homelab with EU-path data subjects possible) — review against **GDPR**.
- Lawful basis for the pilot is **legitimate interest** (residents of a building using a building's own parking service) **plus an explicit consent notice at signup**. A finding that the consent notice is missing or buried is `blocking` because CPS relies on it alongside legitimate interest — it is not optional belt-and-suspenders here.

---

### CPS PII inventory (the in-scope field list — flag any field not on it)

This is the closed set of personal data CPS may hold. Verify each is handled as its row requires; flag any collected PII field absent from this table as a data-minimization finding.

| Data | Classification | Required handling |
|---|---|---|
| Email | PII — must read back (login lookup) | Volume-encrypted at rest; field-encryption (blind-index) is a *future* item, not required for pilot; TLS in transit |
| Display name | PII — must read back | Volume-encrypted at rest |
| Phone | PII (sensitive) — must read back | **Field-encrypted (reversible)**; optional; droppable on erasure |
| Password | Secret — verify only | Argon2 one-way hash; never recoverable |
| TOTP secret | Secret — verify only | Encrypted at rest per ADR (never plaintext in DB) |
| Recovery codes | Secret — verify only | Hashed after generation; shown once to the user only |
| Unit number | Quasi-identifier | Minimal; building-context only |
| Booking history | Behavioral | Retained; **anonymized** on erasure (not deleted) |
| Listing history / earned-horizon metric | Behavioral | Retained anonymized, or deleted, on erasure |
| Audit-log entries | Operational | Actor identity retained; **target** anonymized on erasure |

- Phone is the one field that requires **field-level** encryption (not merely volume encryption) — confirm it is not left to disk-encryption alone.
- Email/display-name/phone must **never** be hashed: §7 explicitly prohibits hashing fields that must be read back.

---

### Right-to-erasure — CPS model targets (`delete_user_pii()`)

Beyond CORE's "an erasure path exists," verify the CPS erasure function touches exactly these models/fields:

- **Scrubs** `email`, `display_name`, `phone` on the `User` record — but does **not** delete the `User` row itself (kept for referential integrity).
- **Anonymizes** `Booking` and `AvailabilityWindow` user FKs (null/anonymous placeholder) — the operational records survive; they are not deleted or orphaned.
- **Anonymizes** the **target** reference on `AdminAuditLog`, while **retaining the actor** reference (accountability is preserved; the erased subject's appearance *as a target* is anonymized).
- **Deletes** the TOTP secret and recovery codes.
- The erasure event is itself written to the audit log.
- Erasure is **operator-console-only** for the pilot — there is no self-service deletion endpoint. Flag a self-service erasure path as out-of-scope-for-pilot (route the scope decision to pm-agent).

---

### Admin PII-access logging (CPS audit-log contract)

CORE requires that admin PII views are logged; CPS pins the exact entry shape and the surfaces:

- Any view rendering a resident's email, name, or phone to an admin/operator must write an `AdminAuditLog` entry with: **actor, action=`pii_access`, target user ID, organization, timestamp**.
- **Org scoping is mandatory** on the entry — CPS is one organization per condo/HOA (resolved by hostname in middleware), so every PII-access log row must carry its `organization`. An access-log write missing the org field is a `blocking` finding.
- **Bulk** PII exposure (e.g. the resident list / operator console with emails shown) must also be logged, not just single-record views.

---

### Retention posture (CPS pilot gap to flag)

- SPEC-1 does **not** define explicit retention periods for the pilot (booking history, listing history, audit entries). Per CORE, the absence of a retention policy is a gap — raise it as a `recommendation` and route the retention-period decision to `pm-agent → human`. Do not invent a period.
