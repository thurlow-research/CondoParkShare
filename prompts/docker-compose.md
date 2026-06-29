# Prompt Artifact — docker-compose.yml + Dockerfile (CPS#165)

| Field | Value |
|---|---|
| **Generated files** | `docker-compose.yml`, `Dockerfile` |
| **Description** | Container & deploy hardening: non-root user, migrate-before-serve, pinned image digests, worker-count drift fix, healthchecks + readiness gate |
| **Date** | 2026-06-29 |
| **Model** | claude-sonnet-4-6 |
| **Risk level** | HIGH |
| **Human review status** | ⬜ Pending |

---

## Prompt

```
Implement CPS#165 (Container & deploy hardening) in Dockerfile and
docker-compose.yml only:

1. Run as non-root. Add an unprivileged user (uid 1001 appuser), chown /app,
   and USER appuser. gunicorn binds :8000 (>1024), so no root is required.
2. Migrate before serving. Change the web command to run
   `python manage.py migrate --no-input` to completion, then exec gunicorn, so
   no request is served against an unmigrated schema.
3. Pin base images to digest. python:3.12-slim and postgres:16 are mutable
   tags — pin both to their multi-arch index digest.
4. Worker-count drift. Dockerfile CMD has --workers 3, compose has --workers 4.
   Make compose authoritative; drop --workers from the Dockerfile CMD.
5. Healthchecks / readiness. Add a pg_isready healthcheck to db, a healthcheck
   to web, and depends_on: db: {condition: service_healthy} so cold start does
   not race DB readiness.

Match existing file conventions. Keep the change to these two files.
```

## Constraints Specified

- **Scope:** `Dockerfile` and `docker-compose.yml` only — no app code, no `setup-run.sh`/`deploy.sh` edits (those overlap CPS#167/#170).
- **Digests:** multi-arch *index* digests (arch-portable), resolved via
  `docker buildx imagetools inspect <image>`:
  - `python:3.12-slim` → `sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf`
  - `postgres:16` → `sha256:fe03a7605299a34ddf5e4f285dff78c3d7190a576b3c6b46f2fcff69f4bffd54`
- **Non-root + audit volume:** `/app/logs` (the `audit_logs` named-volume mountpoint)
  is created and chowned to `appuser` in the image so a *freshly created* volume
  inherits appuser ownership and the JSONL recovery sink stays writable.
- **Worker authority:** compose `command` keeps `--workers 4`; the Dockerfile CMD
  drops the count (single-worker fallback for bare `docker run`).
- **web healthcheck:** TCP-connect probe via `python` (slim image has no curl). A
  full HTTP `/healthz` was deliberately *not* added — see review notes.
- Must NOT: change app behavior, publish new ports, or alter the existing
  `WEB_BIND_IP`-scoped publish from CPS#158.

## Refinement History

First attempt worked; verified end-to-end with `docker compose up` (see below).

## Human Review Notes

Reviewer should focus on these (review for correctness + deploy-safety):

1. **migrate-before-serve couples web startup to a valid prod config.** Because
   `migrate` runs Django's system checks, web now **hard-fails at boot** (and,
   with `restart: unless-stopped`, restart-loops) if `CACHE_URL` is unset in
   production — the `django_ratelimit.E003` error (this is the CPS#147
   condition). This was reproduced during verification. It is arguably *correct*
   fail-fast behavior (you should not serve prod without the shared cache), but
   it changes a misconfig from "serves with per-worker locmem" to "never
   starts." Confirm production `.env` always sets `CACHE_URL`, and consider
   whether CPS#147's env-conditional silencing should land alongside this.
2. **Named-volume ownership applies to *fresh* volumes only.** `chown appuser
   /app/logs` seeds ownership at first volume creation. An **existing**
   `audit_logs` volume created under the old root image keeps root ownership;
   the non-root container may then fail to write the recovery sink. For existing
   deploys, chown the volume once (e.g. `docker run --rm -v
   worker_audit_logs:/v alpine chown -R 1001:1001 /v`) or recreate it. Review
   for correctness on the upgrade path.
3. **Digest pinning freezes security updates.** Pinned digests must be bumped
   deliberately; they will not pick up upstream patch releases automatically.

Verification performed (separate `worker` compose project, real stack untouched):
- Image runs as `uid=1001(appuser)`; `/app/logs` owned `appuser:appuser`.
- `db` reaches `healthy` via `pg_isready`; web `Waiting` → starts only after db
  `Healthy` (readiness gate confirmed).
- With `CACHE_URL` set: migrations applied to completion **before** gunicorn;
  gunicorn boots **4 workers** as PID 1; web healthcheck → `healthy`.

- Reviewed by: _pending_
- Status: _pending_

---

## Reproducibility Check

To verify this prompt still produces equivalent output in a new session:
1. Open a fresh Claude Code session in the repo.
2. Paste the prompt above verbatim.
3. Compare against `Dockerfile` and `docker-compose.yml`.
4. Note any drift in a new version artifact (`docker-compose.v2.md`).
