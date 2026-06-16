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

## Key docs

- `docs/design/TECHNICAL-DESIGN.md` — implementation contract
- `docs/architecture/ADR-001-pilot.md` — architectural decisions
- `Specs/SPEC-1-pilot.md` — product specification
- `AGENTS.md` — AI-assisted development oversight protocol

## License

[Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/)
