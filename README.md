# CondoParkShare

A Django web application that lets residents of a condominium community share
their parking spots with neighbours who need guest parking.

## Why this project exists

CondoParkShare serves two purposes:

1. **A utility for Bellevue Towers residents** — coordinates and shares the
   building's scarce guest parking, reducing friction for the whole community.
2. **A proving ground for the Human Oversight System (HOS)** — a non-trivial
   production build used to exercise and harden the
   [HumanOversightSystem](https://github.com/ScottThurlow/HumanOversightSystem)
   governance framework under realistic conditions.

## Features

- Multi-tenant HOA model — one organisation per condo, resolved by hostname
- Account registration with admin approval; invite flow for operators
- TOTP two-factor authentication and recovery codes
- Spot listing — owners mark availability windows; listings earn booking horizon
- Resident search and booking with three enforced gates (horizon, one-active, overlap)
- Cancellation, early-release, and owner-cancel flows
- Email and web-push notifications (Brevo + VAPID)
- Operator console, HOA/manager portal, and full audit log

## Stack

| Component | Detail |
|-----------|--------|
| Runtime | Python 3.12 / Django 5.x |
| Database | PostgreSQL 16 (`tstzrange` GiST exclusion for booking overlap) |
| Auth | django-otp (TOTP), Argon2 password hashing |
| PII | django-encrypted-model-fields (Fernet at rest) |
| Front proxy | Caddy (TLS termination, reverse-proxy to gunicorn on :8001) |
| Email | Brevo via django-anymail |
| Web push | pywebpush / VAPID |
| Containers | Docker Compose (`web`, `db`); `pgdata` + `audit_logs` named volumes |

## Running locally

```bash
cp .env.example .env
# Edit .env — at minimum set SECRET_KEY, POSTGRES_PASSWORD, DATABASE_URL,
# and PII_ENCRYPTION_KEY.  Set DEBUG=True for local dev.
docker compose -f docker-compose.dev.yml up -d
python manage.py migrate
python manage.py runserver
```

See `.env.example` for all available variables and generation commands.

## First-run admin bootstrap

A fresh deployment needs one operator admin before anyone can log in. The
`bootstrap_admin` management command creates it in one idempotent step — no
manual `createsuperuser` or TOTP enrollment required:

```bash
ADMIN_PASSWORD='your-strong-password' python manage.py bootstrap_admin \
  --email admin@example.com \
  --org-name "Maple Court HOA" --org-hostname maplecourt.example.com \
  --password-from-env ADMIN_PASSWORD
```

It creates (on the first run only):

- the **Organization** (get-or-created by hostname; an existing org's name is
  never overwritten),
- the **superuser admin** (`is_superuser`, `is_staff`, `is_hoa_admin`,
  `status='active'`),
- a **confirmed TOTP device** so you can sign in to the operator console right
  after enrolling 2FA.

Required env / flags:

- `--email`, `--org-name`, `--org-hostname` are required.
- `--org-support-email` is optional and defaults to `--email`.
- `--display-name` is optional and defaults to the email's local-part.
- `--password-from-env VARNAME` reads the password from that env var (errors if
  unset/empty). Omit it entirely to have a strong password **generated and
  printed once**.
- `--print-totp-uri` adds a bare, labeled `otpauth://` line for scripted use.

The **generated password and the otpauth:// 2FA URI are printed to stdout once
and never shown again** — capture them immediately. Re-running the command is a
safe no-op once the admin exists (it does not reset the password, status, or
TOTP device).

`tools/setup-run.sh` runs this automatically when `ADMIN_EMAIL`, `ORG_NAME`, and
`ORG_HOSTNAME` are set in the environment (with `ADMIN_PASSWORD` for the
password); it is skipped otherwise. On hosts where `django_ratelimit` raises
system-check `E003` (#147), pass `--skip-checks`.

## Key docs

- `docs/design/TECHNICAL-DESIGN.md` — implementation contract
- `docs/architecture/ADR-001-pilot.md` — architectural decisions
- `Specs/SPEC-1-pilot.md` — product specification
- `AGENTS.md` — AI-assisted development oversight protocol

## License

[Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/)
