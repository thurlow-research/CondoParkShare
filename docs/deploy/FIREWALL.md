# CondoParkShare — Host Firewall Configuration

## Overview

The `scripts/setup_firewall.sh` script applies idempotent UFW rules on the app
host (opus for prod, faberix for ppe) to support the monitrix monitoring stack.
It is called automatically by `deploy.sh` on every deploy and is safe to re-run
manually at any time.

The script does **not** touch the Postgres port (5432), which must remain
container-internal only and is never published to the host network.

---

## What it opens

### Inbound — Prometheus metrics scrape

| Direction | Source subnet | Protocol | Port | Purpose |
|---|---|---|---|---|
| inbound | `MONITRIX_SCRAPE_SRC_V4` (default `192.168.1.0/24`) | tcp | `AUDIT_METRICS_PORT` (default `9108`) | monitrix Prometheus scrapes the audit-metrics exporter |
| inbound | `MONITRIX_SCRAPE_SRC_V6` (default `fd74:a5b1:bbd1:1::/64`) | tcp | `AUDIT_METRICS_PORT` (default `9108`) | same, IPv6 path |

One UFW rule is created per address family.

### Outbound — Loki log push

| Direction | Destination | Protocol | Port | Purpose |
|---|---|---|---|---|
| outbound | `MONITRIX_HOST` (default `monitrix.kumajyo.com`) | tcp | `LOKI_PORT` (TBD) | app host ships structured logs to Loki on monitrix |

The outbound rule is **skipped with a warning** if `LOKI_PORT` is blank. Once
the Loki push port is confirmed, set `LOKI_PORT` in `.env` and re-run
`sudo scripts/setup_firewall.sh` (or trigger a full deploy).

> **Skipping this rule does not block outbound Loki traffic.** UFW's default
> outbound policy on standard Ubuntu is `ALLOW`, so omitting the rule means
> Loki pushes are still permitted — they are just not explicitly scoped.
> Conversely, if the host's default-outbound policy has been changed to `DENY`,
> Loki pushes will fail silently (dropped without an error surfacing to Django)
> unless Django's Loki handler is configured with a fallback log sink. Verify
> the outbound policy and handler fallback before relying on Loki in production.

---

## Environment variables

All variables are read from `.env` in the repo root (or from the environment).
Example values are in `.env.example`.

| Variable | Example value | Description |
|---|---|---|
| `AUDIT_METRICS_PORT` | `9108` | Port the audit-metrics Prometheus exporter listens on |
| `LOKI_PORT` | _(blank/TBD)_ | Loki push port on monitrix; leave blank until confirmed |
| `MONITRIX_SCRAPE_SRC_V4` | `192.168.1.0/24` | IPv4 source subnet for Prometheus scrapes |
| `MONITRIX_SCRAPE_SRC_V6` | `fd74:a5b1:bbd1:1::/64` | IPv6 source subnet for Prometheus scrapes |
| `MONITRIX_HOST` | `monitrix.kumajyo.com` | Hostname or IP of monitrix (outbound Loki target) |

> **`MONITRIX_HOST` must resolve to a LAN IP.** Use the host's private IP
> address (e.g. `192.168.1.50`) or a private hostname that resolves to one.
> If you use a public-facing hostname and DNS is misconfigured or hijacked,
> UFW will allow outbound TCP to whatever IP that name resolves to, which
> could be a public address — defeating the intent of a scoped outbound rule.

These are private LAN values, not secrets. They are safe to document and commit
to `.env.example`.

---

## Tightening the source restriction

The default source is the entire LAN subnet (`/24` IPv4, `/64` IPv6). If you
want to allow only the specific monitrix host rather than the whole subnet:

```
MONITRIX_SCRAPE_SRC_V4=192.168.1.<monitrix-ip>/32
MONITRIX_SCRAPE_SRC_V6=fd74:a5b1:bbd1:1::<monitrix-suffix>/128
```

Update `.env` on the host and re-run `sudo scripts/setup_firewall.sh`.

---

## Exact UFW rules the script applies

With the default example values, the script runs the following `ufw` invocations
(variables shown expanded):

```bash
# Inbound — IPv4
ufw allow in \
    from 192.168.1.0/24 \
    to any \
    port 9108 \
    proto tcp \
    comment "monitrix-scrape: audit-metrics exporter (v4 LAN subnet)"

# Inbound — IPv6
ufw allow in \
    from fd74:a5b1:bbd1:1::/64 \
    to any \
    port 9108 \
    proto tcp \
    comment "monitrix-scrape: audit-metrics exporter (v6 LAN subnet)"

# Outbound — only when LOKI_PORT is set
ufw allow out \
    to monitrix.kumajyo.com \
    port <LOKI_PORT> \
    proto tcp \
    comment "monitrix-loki: app→monitrix Loki push"
```

The script never applies a rule without an explicit source or destination
(no `allow to any port` without restriction).

---

## Deploy integration

`deploy.sh` calls `setup_firewall.sh` automatically after `collectstatic` on
every deploy via `sudo scripts/setup_firewall.sh`. The call is wrapped in an
error handler that emits a warning (not a fatal error) if the firewall step
fails, so the running stack is not interrupted — but the operator must fix the
firewall before the metrics scrape will work.

> **`sudo` strips the shell environment.** Because `deploy.sh` invokes
> `setup_firewall.sh` via `sudo`, variables that are merely exported in the
> operator's shell session (`export LOKI_PORT=3100`) are not visible to the
> script. All five monitoring variables — `AUDIT_METRICS_PORT`, `LOKI_PORT`,
> `MONITRIX_SCRAPE_SRC_V4`, `MONITRIX_SCRAPE_SRC_V6`, and `MONITRIX_HOST` —
> must be present in `.env` at the repo root, which the script sources
> explicitly. Relying on shell exports alone will cause the firewall step to
> fall back to defaults or skip optional rules silently.

To override the sudo invocation (e.g. when running deploy.sh as root):

```bash
FIREWALL_SUDO="" scripts/deploy.sh prod
```

To run the firewall step manually:

```bash
sudo scripts/setup_firewall.sh
```

---

## Constraints

**Exporter must bind host-internal only.** The audit-metrics exporter must
listen on a host-internal interface (e.g. `127.0.0.1:9108` or the LAN
interface), not `0.0.0.0`. It must NOT be routed through the public Caddy
reverse-proxy — Caddy terminates TLS for public traffic only.

**Postgres stays container-internal.** Port 5432 is not published to the host
in `docker-compose.yml` and this script never opens it. No external access to
Postgres is permitted.

**Audit-recovery JSONL must not be public.** The file at
`/app/logs/audit-recovery.jsonl` (on the `audit_logs` Docker volume) must never
be served via any public-facing route. It is for operator recovery use only.

**No broad rules.** The script refuses to run `ufw allow to any port` without a
source restriction. Missing required variables cause an immediate exit with a
clear error message before any rule is applied.
