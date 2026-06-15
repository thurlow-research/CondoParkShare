## CondoParkShare infra-review depth

Apply every item below **in addition to** CORE and the django pack. Do not duplicate items already in either — generic deploy/config obligations live in CORE, and generic Django/Postgres/Compose deploy idioms (service topology, named volume, secrets-in-`environment`, `.env.example` basics, migrations-on-deploy, healthchecks, pg_dump targeting `db`) live in the django pack and are not repeated here.

The deployment spec for this project is **`Specs/SPEC-1-pilot.md` §2 (Technology & Deployment)** — read it completely before reviewing. Every requirement there must be verifiable in the configuration files.

---

### Caddy multi-site: canonical + per-HOA aliases

CPS is multi-tenant: one canonical domain plus a separate site block per HOA. Verify the Caddyfile has:

- **Canonical domain `parkshare.kumajyo.com`**, served via TLS, using the **wildcard cert `*.kumajyo.com`** — which requires the **DNS-01** challenge. Confirm the ACME provider and the DNS-plugin module are configured (DNS-01 needs API credentials, not just an open `:80`).
- **Each HOA alias** (e.g. `parkshare.bellevuetowers.org`) as its **own site block**, using the **HTTP-01** challenge (so `:80` must be externally reachable for those domains — this is why port 80 stays open beyond the canonical wildcard's needs).
- **Both** the canonical host **and** every HOA alias must appear in Django's `ALLOWED_HOSTS`. A new HOA alias in the Caddyfile that is missing from `ALLOWED_HOSTS` is a **blocking** finding (Django will reject the host).
- Caddy reverse-proxies to the `web` service **only**, never to `db`.

---

### Deploy host `opus` behind Caddy + DDNS

- Production runs on the home host **`opus`** behind Caddy, reached via **dynamic DNS (DDNS)**. Because the public IP is dynamic, confirm nothing in the config hard-codes the host's current IP — TLS, `ALLOWED_HOSTS`, and proxy upstreams must all be name-based, not IP-based.
- HTTP-01 on the HOA aliases depends on DDNS keeping those A/CNAME records pointed at `opus`. If the deploy docs or config reference a fixed IP for an HOA alias, flag it — it will break on the next IP change.

---

### Backups: nightly pg_dump → NAS

Beyond the django pack's "pg_dump exists, off-container, rotated, restore documented", CPS pins the target and protection:

- The backup destination is the **NAS** (a mounted path or pushed off-host), **not** a directory on `opus` itself — a backup co-located with the only production host does not survive that host's loss. Flag a backup that writes only to local `opus` storage.
- The dump (or the NAS at rest) must be **encrypted**. CPS stores encrypted PII; an unencrypted `pg_dump` on the NAS leaks that PII outside the application's encryption boundary. Flag **blocking** if neither the dump nor the NAS is encrypted at rest.
- Cadence is **nightly** — confirm the cron/schedule actually fires nightly, not a one-shot.

---

### CPS-specific `.env.example` variables

In addition to the django-pack `.env.example` checks, these CPS-required vars must each be present (placeholders only):

- **PII encryption key(s)** — CPS encrypts resident PII at the application layer; a missing key var means the example is incomplete and the app will fail to boot or, worse, run with PII unprotected.
- **VAPID keys** — required for the web-push notification channel (the email→web-push escalation path). Note them in findings if absent.
- **Email backend credentials** — for the first-tier (email) notification path.

Flag any of these missing from `.env.example`.

---

### Portability: Hetzner EU VPS target

CPS's portability requirement is concrete (SPEC-1 §2): the stack must move to a **Hetzner EU VPS** by (1) copying `.env`, (2) restoring the `pg_dump`, (3) repointing the **canonical CNAME** (`parkshare.kumajyo.com`). Verify nothing requires manual state outside those three steps — in particular:

- The DDNS/`opus`-specific assumptions above must not leak into portable config (no `opus` hostname or home-LAN IP baked into the stack).
- HOA-alias HTTP-01 challenges must re-provision automatically on the new host once DNS points there — no manual cert copy from `opus`.
