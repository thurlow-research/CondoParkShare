# Prompt artifact — Production hardening (#170)

- **Issue:** thurlow-research/CondoParkShare#170
- **Risk tier:** MEDIUM (security + infra; production config, auth brute-force surface)
- **Model:** claude-sonnet-4-6
- **Shadows:** `docker-compose.yml`, `parkshare/settings/production.py`,
  `parkshare/settings/base.py`, `requirements.txt`, `.env.example`,
  `.gitignore`, `Caddyfile`, `tools/setup-run.sh`

## Generation prompt (orchestrator → coder)

Implement five production-readiness hardening items from #170. Make the minimal,
surgical change for each; do not refactor surrounding code. Preserve the #147
design decision that PPE intentionally runs per-worker locmem (no shared cache).

1. **Shared cache for rate limiting.**
   - Add a `redis` service to `docker-compose.yml` (internal network, restart
     unless-stopped, redis-cli ping healthcheck). Add `redis` to web's
     `depends_on` with `condition: service_healthy`.
   - `production.py`: when `CACHE_URL` is set, use `env.cache(...)` (unchanged).
     When unset AND `ENVIRONMENT == "prod"`, raise `ImproperlyConfigured`
     (shared cache mandatory on prod). When unset on any other env (ppe), keep
     the existing `warnings.warn(...)` + locmem fallback.
   - `requirements.txt`: add the `redis` python client (Django 5.1's builtin
     `RedisCache` backend needs it at runtime; tests use locmem so it isn't
     imported during the suite).
   - `.env.example`: document `CACHE_URL=redis://redis:6379/1`, required on prod.

2. **Static via WhiteNoise.**
   - `requirements.txt`: add `whitenoise`.
   - `base.py`: insert `whitenoise.middleware.WhiteNoiseMiddleware` immediately
     after `SecurityMiddleware`.
   - `production.py` only: set `STORAGES["staticfiles"]` to
     `whitenoise.storage.CompressedManifestStaticFilesStorage`. Do NOT set
     manifest storage in base.py — it would break `{% static %}` in tests where
     collectstatic has not run.

3. **.gitignore parity.** Ignore `.env.*` while keeping committed templates:
   add `.env.*` plus `!`-negations for every currently-tracked `.env*` file
   (check `git ls-files '.env*'`; at minimum `.env.example`).

4. **Caddy request-body limit.** Add `request_body { max_size 10MB }` to each of
   the two `reverse_proxy` site blocks (prod + ppe). Redirect-only blocks unchanged.

5. **DB readiness.** In `tools/setup-run.sh`, replace the `sleep 10` with a
   bounded poll loop on `docker compose exec -T db pg_isready -q` (cap ~60s,
   exit 1 on timeout) before the `exec web ... migrate`.

After implementing, run `bash scripts/framework/run_tests_inner_loop.sh` and fix
any failures (e.g. a test asserting the MIDDLEWARE list). Report the diff and
the test result.
