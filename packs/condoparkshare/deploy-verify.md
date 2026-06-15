## CondoParkShare deploy-verify depth

Apply every item below as the deploy-verification contract for CondoParkShare. This is the CPS-specific depth-addendum for the deploy-verify agent — there is no HOS template or django pack for deploy-verify (it is a CPS-only agent), so the items here are the full deployment/verification contract, not a supplement to a generic base.

---

### Deployment context (read before running — from SPEC-1 §2)

- **Host:** `opus.kumajyo.com` — Ubuntu VM running Docker Compose with three services: `web` (gunicorn), `db` (Postgres), `caddy`.
- **Canonical URL:** `https://parkshare.kumajyo.com`.
- **HOA alias:** `https://parkshare.bellevuetowers.org` — CNAME → canonical.
- **TLS:** wildcard `*.kumajyo.com` via Caddy DNS-01; HOA alias via HTTP-01.
- **DB:** internal-only — no external port; named volume.
- You run after `docker compose up` on opus and are the last gate before announcing the deployment successful. Do not announce success until every Phase 1 and Phase 2 check passes (or only carries non-blocking WARNINGs).

---

### Required environment (read from project `.env`; abort with a clear error if any is missing)

| Variable | Example | Purpose |
|---|---|---|
| `AGENT_SSH_KEY` | `~/.ssh/opus_agent` | Path to the `parkshare-agent` private key |
| `AGENT_COMPOSE_PATH` | `/opt/parkshare/docker-compose.yml` | Path to compose file on opus |
| `AGENT_BACKUP_DIR` | `/mnt/nas/backups/parkshare` | Path to backup directory on opus |

- Set a reusable SSH alias at the start of every Phase 1 remote check:
  ```bash
  SSH="ssh -i $AGENT_SSH_KEY -o BatchMode=yes -o StrictHostKeyChecking=no parkshare-agent@opus.kumajyo.com"
  ```
- Remote docker commands require `sudo` per the `parkshare-agent` sudoers entry — prefix them.

---

### Phase 1 — infrastructure, remote (SSH to opus)

- **Docker services:** `sudo docker compose -f $AGENT_COMPOSE_PATH ps` — all three of `web`, `db`, `caddy` must be `Up`. Any `Exited` or `Restarting` = FAIL.
- **`web` logs:** `... logs --tail=20 web` — check for gunicorn startup errors or Django exceptions.
- **`caddy` logs:** `... logs --tail=20 caddy` — check for TLS/ACME errors or certificate-issuance failures.
- **Backup verification:** `ls -lht $AGENT_BACKUP_DIR/*.sql.gz | head -5` — newest backup must exist and be non-zero. Newest file older than 48h = WARNING. No files at all = FAIL (blocking — deployment is not complete without a verified backup).

---

### Phase 1 — infrastructure, local (this machine, no SSH)

- **DNS — canonical:** `dig +short parkshare.kumajyo.com` must return an IP, not NXDOMAIN.
- **DNS — HOA alias:** `dig +short parkshare.bellevuetowers.org` must CNAME to the canonical host or resolve to the same IP.
- **TLS cert:** `openssl s_client -connect parkshare.kumajyo.com:443 -servername parkshare.kumajyo.com </dev/null | openssl x509 -noout -dates -subject -issuer` — `notAfter` must be > 30 days out; issuer must be Let's Encrypt, not self-signed.
- **HTTPS reachability (both domains):** `curl -sI` against canonical and HOA alias must return 200 or 30x — not a connection error or cert warning.
- **HTTP → HTTPS redirect:** `curl -sI http://parkshare.kumajyo.com | grep -i location` must redirect to the `https://` version.
- **Security headers:** `curl -sI https://parkshare.kumajyo.com` must carry all four — `Strict-Transport-Security`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy`.
- **DB not exposed:** `nc -zv -w 3 $(dig +short parkshare.kumajyo.com) 5432` must time out or refuse — Postgres must be unreachable from outside the host.

---

### Phase 2 — browser smoke tests (Chrome DevTools MCP against the live canonical URL)

These cover the critical path only:

1. **App loads** — navigate to canonical URL; page loads without 500; login form present; logo visible; no console errors.
2. **Design system loaded** — Hanken Grotesk loaded on `body`; `getComputedStyle(document.documentElement).getPropertyValue('--pine')` returns `#204034` (confirms `tokens.css`); favicon is the CondoParkShare mark via `<link rel="icon">`.
3. **Bad login handled gracefully** — submit `test@example.com` / `wrongpassword`; error state shown; no 500; no Django debug page exposed.
4. **HOA alias enforces HTTPS** — navigate to `http://parkshare.bellevuetowers.org`; redirected to `https://` with no cert error.
5. **PWA manifest** — `curl -s .../manifest.json | python3 -m json.tool` is valid JSON with `name`, `short_name`, `icons`, `start_url`.
6. **Static files served** — `curl -sI .../static/css/tokens.css` returns 200, not a Django 404.
7. **Admin page** — navigate to `/admin/`; Django admin login loads (not 500) with CondoParkShare branding applied.

---

### Report format (emit verbatim structure)

```
## Deployment Verification Report
**Host:** opus.kumajyo.com
**Target:** parkshare.kumajyo.com

### Phase 1: Infrastructure
| Check | Location | Status | Notes |
|---|---|---|---|
| Docker services up    | remote | PASS/FAIL | |
| Backup file exists    | remote | PASS/FAIL | newest: [filename/age] |
| DNS — canonical       | local  | PASS/FAIL | |
| DNS — HOA alias       | local  | PASS/FAIL | |
| TLS — canonical       | local  | PASS/FAIL | expires: [date] |
| TLS — HOA alias       | local  | PASS/FAIL | |
| HTTP → HTTPS redirect | local  | PASS/FAIL | |
| Security headers      | local  | PASS/FAIL | missing: [list] |
| DB not exposed        | local  | PASS/FAIL | |

### Phase 2: Smoke Tests
| Test | Status | Notes |
|---|---|---|
| App loads             | PASS/FAIL | |
| Design system loaded  | PASS/FAIL | |
| Bad login handled     | PASS/FAIL | |
| HOA alias HTTPS       | PASS/FAIL | |
| PWA manifest          | PASS/FAIL | |
| Static files          | PASS/FAIL | |
| Admin page            | PASS/FAIL | |

### Overall: PASS / FAIL
[Blocking issues with specific remediation steps]
```

---

### Escalation targets

- **SSH connection fails** → confirm the `parkshare-agent` account exists on opus, the key is installed, and the sudoers entry is in place. If still failing → human.
- **Infrastructure failure** (TLS, DNS, Docker, firewall) → infra-reviewer + human immediately.
- **Functional failure** (smoke tests, app 500s) → coder + system-test.
- **Backup missing or stale** → human immediately. Never mark the deployment successful without a verified backup.
- **Unresolvable** → human.
