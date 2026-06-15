## CondoParkShare dependency-mapping depth

Apply every item below **in addition to** CORE (and the django pack if one exists). Do not duplicate items already covered. As of HOS v0.3.1 **no `packs/django/dep-mapper.md` exists**, so the generic CORE grep-based mapper is the only base — it self-reports `Data confidence: LOW` on Django wiring it cannot trace. The depth below is what restores HIGH confidence for the CPS stack: trace these explicitly and report the connections.

CPS apps and their wiring entry points: `accounts/`, `parking/`, `notifications/`, `operator_console/`, `portal/`; project wiring in `parkshare/` (`middleware.py`, `managers.py`, `settings/base.py`, `urls.py`).

---

### Multi-tenant blast radius — `Organization` fan-in is the dominant amplifier

`parking.Organization` is the tenant root. A change to `Organization`, to tenant resolution, or to any org-scoping mechanism is **never contained** — it fans into every org-owned model and every request.

- Trace FK fan-in to `Organization` across all apps, not just `parking`:
  ```bash
  grep -rnE 'ForeignKey\(\s*("parking\.Organization"|Organization)' --include='*.py' accounts parking notifications operator_console portal
  ```
  Known fan-in includes `accounts.User`, `accounts.Invite`/`EmailOTP`/`AdminAuditLog`, and `parking.ParkingSpot`/`AvailabilityWindow`/`Booking`. Any of these in the changed set inherits the tenant-boundary blast radius.
- Note FK `on_delete` semantics in the report: `Organization` is referenced with `PROTECT` (spots, bookings, users) **and** `CASCADE` (some accounts models) — a change altering deletion behavior changes erasure/teardown blast radius, flag it.

---

### Dual-manager scoping — `.objects` vs `.scoped` is a correctness boundary, not a style choice

Org-owned models expose **two managers**: `objects = models.Manager()` (unscoped) and `scoped = OrganizationScopedManager()` (tenant-filtered, from `parkshare/managers.py`).

- When `OrganizationScopedManager` or the default-manager declaration on any model changes, the blast radius is **every call site that relies on automatic scoping**. Trace both:
  ```bash
  grep -rnE '\.scoped\b|OrganizationScopedManager|objects = models\.Manager' --include='*.py' .
  ```
- A change that flips a model's default manager, or a caller switching `.scoped`→`.objects`, is a tenant-isolation blast-radius event even with zero import changes. Report it as **Config/Middleware-tier** amplification (cross-tenant leak surface), never "contained".

---

### Middleware chain — ordering is load-bearing

`MIDDLEWARE` in `parkshare/settings/base.py` runs, in order: `RatelimitMiddleware` → `TenantMiddleware` → (Django session/auth) → `ImpersonationMiddleware` (all in `parkshare/middleware.py`).

- `TenantMiddleware` sets the request's organization that `OrganizationScopedManager` and downstream views depend on. A change to it, to its **position relative to auth/session**, or to `RatelimitMiddleware` (which must precede tenant resolution) hits **every request** → 4× Middleware tier.
- `ImpersonationMiddleware` participates in admin impersonation + audit; changes here fan into `accounts.AdminAuditLog` and the operator/portal consoles. Trace impersonation symbols outward into those apps.

---

### URL routing — five app routers under one root

Routing is `parkshare/urls.py` including `accounts`, `parking`, `notifications`, `portal` urls (plus `operator_console`). CORE's outward grep does not know these names.

- For a changed view/handler, grep the route tables for the symbol and the reverse name:
  ```bash
  grep -rnE 'name=|include\(|<view symbol>' --include='urls.py' .
  grep -rnE "reverse\(|\{% url |redirect\(" --include='*.py' --include='*.html' .
  ```
- A renamed URL `name=` is a blast-radius event: trace every `reverse()` / `{% url %}` / `redirect()` that targets it before reporting contained.

---

### Template inheritance — `templates/base.html` is the root

CPS uses a single shared `templates/base.html` plus error pages (`403/404/429/500.html`). App templates extend it.

- A change to `base.html` (or its blocks) fans into **every rendered page** → treat as Core/Config tier. Trace `{% extends %}` / `{% include %}` references:
  ```bash
  grep -rnE '\{% extends |\{% include ' --include='*.html' .
  ```
- Design-pack hooks (`tokens.css`, component classes per coder.md) loaded in `base.html` mean a base-template change is also a UI-surface blast-radius event — note it for ui-reviewer routing.

---

### Booking-path fan-in — the three gates share state

The booking-creation path enforces three invariants (horizon, one-active-booking, overlap — see coder.md). The overlap gate is a DB-level `tstzrange` GiST exclusion constraint paired with `select_for_update()`.

- A change to `Booking`, `AvailabilityWindow`, the earned-horizon computation, or the exclusion constraint fans into **all three gates at once** plus the availability/search source and the leaderboard metric. Do not report any one of these as locally contained.
- The GiST exclusion constraint lives in a migration: when a booking/availability model or its constraints change, trace the migration that defines/alters the constraint and flag concurrency-sensitive call sites (`select_for_update`) in the blast radius.

---

### Confidence rule for this stack

Because no django dep-mapper pack exists, the only way to report `Data confidence: HIGH` on a CPS change touching tenant scoping, middleware, routing, templates, or the booking path is to have traced the connections above **both ways** (inward fan-in and outward references). If any of these wiring classes is present in the changed set and untraced, report `Data confidence: LOW` per CORE — do not let the absence of a django pack become a silent gap.
