# Audit-Logging Monitoring Specification (with Dashboards)

**Subsystem:** Impersonation audit log (`AdminAuditLog`) + fail-open recovery sink
**Status:** Design / specification deliverable — no application code changes in this document
**Owners:** System Architect (this doc), Infra (monitrix + app host), on-call operators
**Cross-refs:** CPS#78 (fail-open + recovery-grade audit design), CPS#88 / PR #88 (implementation), CPS#87 (monitoring — this spec is its design input)

---

## 0. Context and threat model (why this spec is load-bearing)

PR #88 made the impersonation audit log **fail-open**: in
`parkshare/middleware.py` (`ImpersonationMiddleware.__call__`), when an
`AdminAuditLog.objects.create(...)` write raises, the operator's impersonated
POST **still proceeds**, and a structured JSON **recovery record** is appended
to a durable JSONL sink via the dedicated `audit_recovery` logger
(`parkshare/settings/base.py`, `LOGGING`), at path `settings.AUDIT_RECOVERY_LOG`.
Those records are later reconciled into `AdminAuditLog` by
`python manage.py backfill_audit_log`
(`accounts/management/commands/backfill_audit_log.py`).

The security property "**every impersonated operator action is auditable**" is
therefore only preserved if **two** conditions hold:

1. The recovery JSONL is **durably persisted and shipped off-box** (so a
   container restart or disk loss does not erase the only record of an
   operator action that was *not* written to the DB), and
2. **Someone is paged promptly** when audit writes start failing, so the
   recovery log is actually reconciled (and the underlying DB fault fixed)
   rather than silently accumulating.

Fail-open without monitoring is fail-*silent*. This spec defines the signals,
liveness probe, data path to monitrix, dashboards, alerts, and the concrete
app/infra changes that make the fail-open design safe.

### Deployment facts this spec designs around

| Fact | Source | Consequence for monitoring |
|---|---|---|
| App runs in Docker Compose: `web` (gunicorn, **multiple workers**) + `db` (Postgres, internal-only) | `docker-compose.yml` | Multiple workers append to one JSONL; metrics must aggregate across workers; db is not reachable from monitrix |
| `web` publishes only `:8001` on the app host; db has **no** port binding | `docker-compose.yml` | monitrix can only pull from the app host over a host-published port + firewall allowance |
| Front TLS terminates on **Nexus** (Windows Caddy), reverse-proxies to `opus.kumajyo.com:8001` (prod) / `faberix.kumajyo.com:8001` (ppe) | `Caddyfile`, `docs/HANDOFF.md` | Public ingress is unrelated to the monitoring pull path; monitoring uses a **separate** host port, not the public domain |
| **Grafana runs on a separate machine, `monitrix`**, which will **pull** logs/metrics from the app host | user-supplied | monitrix is the observability plane; app host is an **exporter/shipper target**. Pull model. |
| `AUDIT_RECOVERY_LOG` defaults to `BASE_DIR/logs/audit-recovery.jsonl` — **not** currently on a named Docker volume | `parkshare/settings/base.py` (lines 264–276, with an explicit "MUST be on a persisted volume" production note) | **Blocker:** in the shipped compose, `logs/` is inside the container's ephemeral writable layer. Must be moved to a named volume before fail-open is safe. |
| Recovery record fields | `parkshare/middleware.py` lines 153–165 | `organization_id, actor_id, on_behalf_of_id, action, target_type, target_id, notes, attempted_at` |
| Backfill reconciliation embeds `[recovered:attempted_at=<ISO>]` in `notes` and backdates `created_at` | `accounts/management/commands/backfill_audit_log.py` lines 162, 206 | This string is the **reconciliation fingerprint** — backlog = recovery records whose fingerprint is not yet present in `AdminAuditLog` |

---

## 1. Signals to monitor

Each signal is defined precisely: what it measures, where it originates, how it
is derived, and the desired steady state. Labels listed are the metric labels
the exporter/log-derived metrics must carry so dashboards can break down by
`env` (prod/ppe) and `host`.

Common labels for all signals: `env` (`prod`|`ppe`), `host`
(`opus`|`faberix`), `service` (`parkshare-web`).

### S1 — Audit-write failure count / rate
- **Definition:** number of times the `AdminAuditLog.objects.create(...)` call in
  `ImpersonationMiddleware` raised and fell through to the recovery path, per unit
  time. This is the primary "audit is broken" signal.
- **Source of truth:** every failure emits exactly one `audit_recovery` record at
  `ERROR`. So **failure count == recovery-record emission count** (see S2). They
  are the same event counted two ways; S1 is the *rate* framing for alerting.
- **Derivation (log path):** `count_over_time` of `audit_recovery` JSONL lines.
- **Derivation (metric path):** a counter `parkshare_audit_write_failures_total`.
- **Steady state:** `0`. **Any** non-zero value over a short window is alertable
  (see A1). There is no acceptable nonzero baseline.

### S2 — Recovery-record emission rate
- **Definition:** rate of new lines appended to the `AUDIT_RECOVERY_LOG` JSONL
  (equivalently, rate of `audit_recovery` logger ERROR events).
- **Source:** the JSONL sink / `audit_recovery` log stream.
- **Why separate from S1:** S1 is "the DB write failed" (intent); S2 is "a
  recovery record was durably written" (the safety net fired). Divergence
  between S1 and S2 (failures occurring but recovery records **not** landing in
  the shipped log) is itself a critical fault — it means the safety net is not
  catching, e.g. read-only volume, disk full, or handler error. Monitoring both
  lets us detect that divergence.
- **Metric:** `parkshare_audit_recovery_records_total` (counter).
- **Steady state:** `0`, and `S2 == S1` whenever either is nonzero.

### S3 — Backfill backlog (unreconciled recovery records)
- **Definition:** count of recovery records that have been **written** to the
  JSONL but are **not yet reconciled** into `AdminAuditLog`. This is the real
  measure of "how much audit history currently exists only in the fragile log
  and not in the durable DB."
- **Derivation:** `backlog = (lines in AUDIT_RECOVERY_LOG that are well-formed
  and pass the backfill allowlist) − (AdminAuditLog rows whose notes match the
  `[recovered:attempted_at=...]` fingerprint)`.
  - This is exactly the set `backfill_audit_log` would `create` on its next run
    (`created_count`), minus already-`skipped` (already-reconciled) rows.
  - **Canonical producer:** add a `--dry-run` mode to `backfill_audit_log` (or a
    thin `audit_backlog` reporting command) that computes and prints
    `created_would_be / skipped / rejected / malformed` **without writing**, and
    emit those four numbers as gauges. Reusing the backfill command's own
    matching logic guarantees the backlog number is computed identically to the
    reconciliation it predicts (no drift between "what we report" and "what we
    fix").
- **Two backlog dimensions (both matter):**
  - **S3a — backlog count:** `parkshare_audit_backlog_records` (gauge).
  - **S3b — backlog age:** age of the **oldest** unreconciled record, derived from
    its `attempted_at`. `parkshare_audit_backlog_oldest_seconds` (gauge).
    A small backlog that is *hours old* is worse than a larger one that is
    seconds old (it means backfill is not running).
- **Steady state:** `S3a == 0`, `S3b == 0`. After an incident, both should drop
  to zero promptly once `backfill_audit_log` runs and the DB is healthy.
- **Also export:** `parkshare_audit_backlog_rejected` and
  `parkshare_audit_backlog_malformed` gauges from the same dry-run pass —
  nonzero **rejected** means forged/tampered or cross-tenant records in the sink
  (the backfill anti-forgery checks tripped) and is a security signal, not just
  an ops signal.

### S4 — Audit-write latency
- **Definition:** wall-clock duration of the `AdminAuditLog.objects.create(...)`
  call in the middleware (success path), as a histogram.
- **Why:** rising latency is the **leading indicator** that precedes outright
  failures (DB under pressure, lock contention, connection-pool exhaustion). It
  lets us page *before* S1 goes nonzero.
- **Metric:** `parkshare_audit_write_seconds` (histogram; buckets e.g.
  `.005,.01,.025,.05,.1,.25,.5,1,2.5,5`).
- **Steady state:** p99 well under ~250 ms; alert on sustained p99 growth.
- **Note:** this requires instrumenting the success path (a timer around the
  `create`). Until that instrumentation lands (Phase 2), S4 is **best-effort**
  and the liveness probe (S5) covers the "is the path healthy" question.

### S5 — Liveness signal (synthetic audit-path health)
- **Definition:** a periodic, traffic-independent verification that the audit
  write path works end-to-end (Django → ORM → Postgres insert → read-back),
  surfaced as a gauge: `1` = healthy, `0` = failing, plus a "seconds since last
  successful probe" gauge.
- **Why this is mandatory:** S1–S4 are **traffic-driven**. During a quiet period
  with zero impersonation activity, a totally broken audit path produces **zero**
  failures and looks identical to a healthy idle system. Without a synthetic
  probe, the first time we learn the audit path is dead is when an operator
  impersonates someone — exactly when we most need it to work. S5 closes that
  gap. Design in §2.
- **Metrics:** `parkshare_audit_liveness_ok` (gauge 0/1),
  `parkshare_audit_liveness_age_seconds` (gauge, time since last success).
- **Steady state:** `parkshare_audit_liveness_ok == 1`,
  `parkshare_audit_liveness_age_seconds` < 2× probe interval.

### Signal summary table

| ID | Signal | Type | Metric name | Steady state | Primary alert |
|---|---|---|---|---|---|
| S1 | Audit-write failure rate | counter | `parkshare_audit_write_failures_total` | 0 | A1 (page) |
| S2 | Recovery-record emission | counter | `parkshare_audit_recovery_records_total` | 0; ==S1 | A1 / A4 (divergence) |
| S3a | Backfill backlog count | gauge | `parkshare_audit_backlog_records` | 0 | A2 |
| S3b | Backlog oldest age | gauge | `parkshare_audit_backlog_oldest_seconds` | 0 | A2 |
| S3c | Backlog rejected (forgery) | gauge | `parkshare_audit_backlog_rejected` | 0 | A5 (security) |
| S4 | Audit-write latency | histogram | `parkshare_audit_write_seconds` | p99 < 250 ms | A3 (warn) |
| S5 | Liveness | gauge | `parkshare_audit_liveness_ok` / `_age_seconds` | 1 / fresh | A6 (page) |

---

## 2. Liveness probe design

**Goal:** verify the audit write path independently of real impersonation
traffic, without polluting `AdminAuditLog` with synthetic rows that look like
real operator actions, and export the result for monitrix to pull.

### 2.1 Mechanism — `manage.py audit_healthcheck` (new management command)

A new, idempotent management command (Phase 2 deliverable, defined here, not
implemented in this doc):

1. **Write** a synthetic probe row via the same ORM/DB path the middleware uses,
   but **clearly marked**:
   - `action = "audit_probe"` (a value **not** in the middleware's real action
     set and **not** in `backfill_audit_log._ALLOWED_ACTIONS`, so a probe row can
     never be confused with or reconstructed from a recovery record).
   - `actor` = a dedicated, non-login system user (e.g. `audit-probe@system`,
     `is_active=False`), or — preferred — write to a **separate tiny table**
     `AuditProbe(created_at)` so probes never touch `AdminAuditLog` at all and
     cannot skew audit dashboards or retention. **Recommendation: separate
     `AuditProbe` table.** It exercises the identical DB/connection path
     (insert + read-back) while keeping the real audit table pristine.
2. **Read back** the row just written (round-trip), confirming commit visibility.
3. **Delete** probe rows older than a short TTL (e.g. keep last N) so the table
   stays bounded.
4. **Emit the result** in two ways:
   - Append a one-line JSON status to a dedicated **liveness status file**
     (e.g. `AUDIT_LIVENESS_STATUS` → `logs/audit-liveness.jsonl` on the shipped
     volume): `{"ts": "...", "ok": true, "write_ms": 12.3}` on success;
     `{"ts": "...", "ok": false, "error": "<class>"}` on failure. This is the
     fallback path that works even if the metrics endpoint is down.
   - Update the Prometheus gauges `parkshare_audit_liveness_ok` and
     `parkshare_audit_liveness_age_seconds` (via the metrics mechanism in §3).

The command must **catch its own exceptions** and still emit `ok:false` +
nonzero exit code, so a DB outage produces an explicit "failing" signal rather
than a missing data point. (Missing-data is also alertable — see A6 — but an
explicit `0` is faster and unambiguous.)

### 2.2 Scheduling

- Run **every 60 seconds** (configurable). On a multi-worker gunicorn deployment
  do **not** run it inside a web worker; run it as a scheduler:
  - **Recommended:** a dedicated lightweight `cron` sidecar container in compose
    (`audit-cron`) that runs `docker compose exec`-equivalent
    `python manage.py audit_healthcheck` on a schedule, OR a host-level cron on
    `opus`/`faberix` invoking `docker compose exec web python manage.py
    audit_healthcheck`. Host cron is simplest given the existing single-host
    compose; a sidecar is cleaner for portability. **Pick host cron for the
    pilot** (one host, fewer moving parts), revisit a sidecar if a second app
    host is added.
- Probe interval (60 s) sets the **detection floor**: a broken audit path is
  detected within ~2 probe intervals (~2 min) even at zero traffic. Tune the
  interval down if a tighter RTO is required; 60 s is a reasonable pilot value.

### 2.3 Why read-back, not just write

A write that "succeeds" but is rolled back, or that hits a stale read-replica,
is still a broken audit path. The read-back of the just-written probe row (in
the same logical operation) is what makes the probe trustworthy: it asserts
**durability + visibility**, which is the property the audit log actually needs.

---

## 3. Getting data to monitrix (pull model)

monitrix runs Grafana and **pulls** from the app host. Two distinct data
classes, two transports. **What runs where** is called out explicitly because
the db is internal-only and the app host publishes a single port today.

### 3.1 Data classes

| Class | Content | Source on app host |
|---|---|---|
| **Logs** | JSONL recovery sink (`AUDIT_RECOVERY_LOG`), liveness status file, structured gunicorn/Django app logs (stdout/stderr) | files on the persisted volume + container stdout |
| **Metrics** | S1–S5 counters/gauges/histograms | a metrics surface the app exposes, or metrics **derived** from the logs |

### 3.2 Recommended architecture

**Logs → Loki, via Grafana Agent / Promtail running on the app host; monitrix
pulls? No — clarify the topology.** Loki/Promtail is fundamentally a **push**
pipeline (the agent ships to Loki). The user constraint is "monitrix pulls."
Reconcile as follows, and this is the **recommended** split:

- **Metrics: pull (Prometheus scrape).** Run a Prometheus exporter surface on
  the **app host** and have monitrix's Prometheus **scrape** it. This honors the
  pull model directly. Two sub-options:
  - **3.2a (recommended) — log-derived metrics on monitrix:** ship logs to Loki
    and derive S1/S2 via LogQL/recording rules; export S3/S5 as real gauges via
    a tiny exporter. Fewer moving parts in the app.
  - **3.2b — app-exposed `/metrics`:** add `django-prometheus` (or a minimal
    custom view) exposing `/internal/metrics` on the web container, scraped by
    monitrix. This gives true histograms for S4 and exact counters for S1/S2,
    but the multi-worker gunicorn requires the `prometheus_client` **multiprocess
    mode** (a shared `PROMETHEUS_MULTIPROC_DIR` on a tmpfs/volume) so counters
    aggregate across the 3–4 workers instead of reporting one worker at random.

- **Logs: push to Loki, but keep monitrix the only *initiator of trust*.** Run
  **Grafana Agent** (or Promtail) **on the app host** as a log shipper to Loki
  **on monitrix**. This is technically a push, but it is the standard,
  low-friction pattern and keeps the app host from exposing log contents on an
  open pull port. If a strict pull is mandated (no outbound from app host),
  the alternative is **Loki acting as a syslog/SSH log pull** — not
  recommended; it is more fragile than the agent. **Recommendation: app-host
  shipper → Loki (push for logs), Prometheus scrape → exporter (pull for
  metrics).** Document this nuance with infra so "monitrix pulls" is understood
  to mean *metrics are pulled; logs are shipped by a thin agent*.

### 3.3 Concrete component placement

| Component | Runs on | Role |
|---|---|---|
| `web` (gunicorn) | app host (opus/faberix) | emits JSONL recovery records + stdout app logs; optionally exposes `/internal/metrics` |
| Persisted volume `audit_logs` | app host | holds `AUDIT_RECOVERY_LOG` + liveness status JSONL (see §6) |
| **Grafana Agent / Promtail** (new compose service `log-shipper`) | app host | tails the JSONL files + container logs, ships to Loki on monitrix, **adds labels** `env`, `host`, `service`, `stream=audit_recovery\|liveness\|app` |
| **metrics exporter** (new compose service `audit-exporter`, or `/metrics` on web) | app host | exposes S3/S5 gauges (and S1/S2/S4 in 3.2b) on a host port, e.g. `:9108` |
| `audit_healthcheck` cron | app host | runs the liveness probe (§2.2) |
| **Prometheus** | monitrix | scrapes `app-host:9108/metrics` over the firewall-allowed port (pull) |
| **Loki** | monitrix | receives shipped logs; backs LogQL panels + log-derived metrics |
| **Grafana** | monitrix | dashboards (§4) + alerting (§5) |

### 3.4 Network / firewall (see §6 for exact rules)

monitrix → app host must be allowed on the **metrics scrape port** only
(e.g. `9108/tcp`). The recovery JSONL must **never** be exposed raw on a public
port: it contains `actor_id`/`on_behalf_of_id`/`org_id` and request paths
(`notes = "POST <path>"`). Shipping happens over an authenticated/TLS Loki push
endpoint on monitrix; the scrape port is restricted by source IP to monitrix.

---

## 4. Grafana dashboards

Two dashboards: a **single-pane overview** ("Audit Health") for at-a-glance
on-call, and a **drill-down** ("Audit Forensics") for incident investigation.
Every panel lists its data source and an example query. LogQL targets Loki;
PromQL targets Prometheus. Where 3.2a (log-derived) and 3.2b (`/metrics`) differ,
both are shown.

### 4.1 Dashboard A — "Audit Health" (single pane / overview)

Template variables: `$env` (prod|ppe), `$host`.

**Panel A1 — Audit-write success rate (stat / gauge, big number)**
- Source: Prometheus.
- PromQL (with `/metrics`, 3.2b):
  ```promql
  1 - (
    sum(rate(parkshare_audit_write_failures_total{env="$env"}[15m]))
    /
    clamp_min(sum(rate(parkshare_audit_write_attempts_total{env="$env"}[15m])), 1)
  )
  ```
  (requires also exporting `parkshare_audit_write_attempts_total` = successes +
  failures; with log-derived only, approximate success rate from S5 liveness +
  S1.)
- Thresholds: green `==1`, red `<1`. Any failure turns this red.

**Panel A2 — Liveness status (stat, colored)**
- Source: Prometheus.
- PromQL: `min(parkshare_audit_liveness_ok{env="$env"})`
- Mapping: `1`→green "HEALTHY", `0`→red "AUDIT PATH DOWN".
- Secondary stat: `max(parkshare_audit_liveness_age_seconds{env="$env"})` with
  red threshold `> 150` (2.5× the 60 s probe interval) to catch a stalled probe.

**Panel A3 — Audit-write failures over time (time series)**
- Source: Prometheus (3.2b) or Loki-derived (3.2a).
- PromQL: `sum by (host) (rate(parkshare_audit_write_failures_total{env="$env"}[5m]))`
- LogQL (3.2a):
  ```logql
  sum by (host) (
    count_over_time({service="parkshare-web", stream="audit_recovery", env="$env"} [5m])
  )
  ```
- Steady state: flat at 0; any spike is an incident marker.

**Panel A4 — Recovery backlog (count) + oldest age (two-series time graph)**
- Source: Prometheus.
- PromQL:
  ```promql
  max(parkshare_audit_backlog_records{env="$env"})
  ```
  and on a second axis:
  ```promql
  max(parkshare_audit_backlog_oldest_seconds{env="$env"}) / 60
  ```
  (oldest age in minutes).
- Annotation: overlay backfill runs (Panel A6) so an operator can see backlog
  drop to zero right after a backfill.

**Panel A5 — Rejected / malformed recovery records (stat, security tint)**
- Source: Prometheus.
- PromQL: `max(parkshare_audit_backlog_rejected{env="$env"})` and
  `max(parkshare_audit_backlog_malformed{env="$env"})`.
- Any nonzero `rejected` is a **security** signal (forgery/tamper/cross-tenant
  attempt caught by backfill's anti-forgery checks) — red, links to Forensics.

**Panel A6 — Backfill runs (state timeline / table)**
- Source: Loki.
- LogQL (parse the command's summary line
  `backfill_audit_log: created=… skipped=… rejected=… malformed=…`):
  ```logql
  {service="parkshare-web", env="$env"} |= "backfill_audit_log:"
    | pattern "<_> created=<created> skipped=<skipped> rejected=<rejected> malformed=<malformed>"
  ```
- Shows when reconciliation last ran and what it did. A long gap here while
  backlog (A4) is nonzero means backfill is not being run — operational miss.

### 4.2 Dashboard B — "Audit Forensics" (drill-down)

Template variables: `$env`, `$host`, `$actor_id`, `$org_id`.

**Panel B1 — Raw recovery records (logs panel)**
- Source: Loki.
- LogQL:
  ```logql
  {service="parkshare-web", stream="audit_recovery", env="$env"}
    | json
    | actor_id = `$actor_id`
  ```
- Lets an investigator read the exact JSONL the safety net captured during an
  incident, filtered to one operator / org.

**Panel B2 — Failures by actor / org (table)**
- Source: Loki.
- LogQL:
  ```logql
  sum by (actor_id, organization_id) (
    count_over_time(
      {service="parkshare-web", stream="audit_recovery", env="$env"} | json [1h]
    )
  )
  ```
- Identifies whether failures concentrate on one tenant (suggests a tenant-
  specific data issue) or are global (suggests DB-wide fault).

**Panel B3 — Audit-write latency heatmap / quantiles (3.2b only)**
- Source: Prometheus.
- PromQL:
  ```promql
  histogram_quantile(0.99,
    sum by (le) (rate(parkshare_audit_write_seconds_bucket{env="$env"}[5m])))
  ```
- The leading indicator: rising p99 here typically precedes A3 going nonzero.

**Panel B4 — Liveness probe history (time series + table)**
- Source: Loki (liveness status JSONL) and/or Prometheus.
- LogQL:
  ```logql
  {service="parkshare-web", stream="liveness", env="$env"}
    | json | line_format "{{.ts}} ok={{.ok}} write_ms={{.write_ms}}"
  ```
- Confirms whether the path was healthy at a given moment and how slow the probe
  write was — useful to correlate with B3.

**Panel B5 — DB / web container health (context)**
- Source: Prometheus (cAdvisor / postgres_exporter if present on monitrix).
- Context only: CPU/mem/connections on `db`, so an investigator can tie audit
  failures to a DB resource event. Not required for the pilot but recommended.

---

## 5. Alerting

Alerts are defined in Grafana (or Prometheus Alertmanager) on monitrix. Severity
and routing reflect the central fact: **an audit-write failure means operator
impersonation actions are, for now, recorded only in the recovery log** — a
compliance/security gap that must be closed fast.

### 5.1 Alert rules

| ID | Condition | Severity | Window | Routes to | Notes |
|---|---|---|---|---|---|
| **A1** | `increase(parkshare_audit_write_failures_total[5m]) > 0` (any failure) | **critical / page** | 5m, fire fast | external pager | Fail-open fired. Audit now depends on the recovery log. |
| **A2** | `parkshare_audit_backlog_records > N` **OR** `parkshare_audit_backlog_oldest_seconds > T` | **high** | 10m sustained | pager + ticket | Reconciliation lagging. Pilot defaults: `N=1`, `T=3600` (1h). Even one record older than 1h means backfill isn't running. |
| **A3** | `histogram_quantile(0.99, …audit_write_seconds…) > 1s` sustained | **warning** | 15m | chat/ticket | Leading indicator; pre-failure DB pressure. |
| **A4** | `increase(S1) > 0` but `increase(S2) == 0` (failures without recovery records landing) | **critical / page** | 5m | external pager | **Safety net not catching** — read-only volume / disk full / handler broken. Worse than A1. |
| **A5** | `parkshare_audit_backlog_rejected > 0` | **high (security)** | immediate | security on-call | Forged/tampered/cross-tenant recovery records caught by backfill anti-forgery. Investigate via Forensics B1/B2. |
| **A6** | `parkshare_audit_liveness_ok == 0` **OR** `parkshare_audit_liveness_age_seconds > 150` **OR** no liveness data for 5m | **critical / page** | 2 consecutive probe failures (~2m) | external pager | Audit path broken even at zero traffic. The traffic-independent backstop. |

### 5.2 Severity rationale

- A1, A4, A6 are **paging** because they each mean the audit guarantee is
  actively degraded (A1/A4) or unverifiable (A6). Audit integrity for operator
  impersonation is a security/compliance control; silent loss is unacceptable
  per CPS#78.
- A2 is high but not paging-on-first-occurrence: a brief backlog during a known
  DB blip is expected; it becomes actionable only if it **persists** (backfill
  not run) — hence the sustained window and the age threshold.
- A3 is a warning: it predicts, but does not constitute, audit loss.

### 5.3 Routing / notification stack vs external pager

- **Use an external pager** (e.g. PagerDuty/Opsgenie/Grafana OnCall on monitrix)
  for A1/A4/A6 — **not** the application's own notification stack
  (`notifications/` + email/web-push via Brevo/VAPID). Reason: the app's
  notification path shares infrastructure (DB, the same `web` containers) with
  the failing subsystem. If Postgres is down, audit writes fail **and** the
  app's email/push queue may be impaired — alerting through the same plane is
  self-defeating. monitrix is an **independent** host; its alerting must not
  depend on the app being healthy.
- A2/A3/A5 may route to a chat channel + ticket; A5 additionally to the security
  on-call.

### 5.4 Runbook note (attach to A1/A4/A6)

> **Audit-write failure — what it means and what to do**
> When this fires, operator impersonation actions are proceeding (fail-open) but
> are being recorded **only** in the recovery log
> (`AUDIT_RECOVERY_LOG`, JSONL), not in `AdminAuditLog`.
> The audit guarantee is intact **only if** that log is durably shipped.
>
> 1. **Confirm the safety net is catching:** check A4 / Panel A3 — failures (S1)
>    should be matched by recovery records (S2) in Loki. If not (A4 firing), the
>    JSONL is not being written/shipped — treat as **data-loss in progress**:
>    verify the `audit_logs` volume is mounted RW and not full; restart the
>    log-shipper.
> 2. **Fix the root cause:** almost always Postgres (down, out of connections,
>    locked). Restore DB health (check `db` container, connections, disk).
> 3. **Reconcile once DB recovers:** run
>    `docker compose exec web python manage.py backfill_audit_log`. It is
>    idempotent (dedupes on the `[recovered:attempted_at=…]` fingerprint) and
>    backdates `created_at` to the original incident time — safe to run multiple
>    times. Watch Panel A4 drop to zero and A6 (backfill run) record the result.
> 4. **Verify liveness recovers:** A6/Panel A2 should return to green within ~2
>    probe intervals.
> 5. **If A5 (rejected) fired:** do **not** blindly re-run backfill expecting it
>    to clear; rejected records are intentionally not inserted. Investigate the
>    rejected lines in Forensics B1 (possible tampering with the JSONL sink).

---

## 6. Required app / infra changes

These are the concrete changes that must land for this spec to be operable.
They are listed as requirements for the coder/infra agents — **not implemented
here**. File paths are real.

### 6.1 Persist + ship the recovery sink (BLOCKER — do first)

- **`docker-compose.yml`:** add a **named volume** `audit_logs` and mount it into
  `web` at the directory holding `AUDIT_RECOVERY_LOG` (e.g. `/app/logs`), so the
  JSONL survives container restarts. Today `logs/` is in the ephemeral container
  layer; `parkshare/settings/base.py` (lines 261–262) already flags this as a
  pre-launch infra requirement — this closes it.
- **`AUDIT_RECOVERY_LOG`** (and new `AUDIT_LIVENESS_STATUS`) must point inside
  that volume. Set explicitly in the production `.env`.
- The volume must be **readable by the log-shipper** container/agent (shared
  mount or read-only bind).

### 6.2 Structured / JSON application logging

- The recovery sink is already pure JSONL (`plain` formatter,
  `parkshare/settings/base.py`). For Loki-side parsing of **app** logs (backfill
  summary lines, request logs), add a JSON formatter for the root/console
  handlers so `| json` works uniformly and `env`/`host` labels can be attached
  by the shipper. Keep the `audit_recovery` formatter as-is (it must stay raw
  JSONL with no level prefix so the file is line-parseable and backfill-readable).

### 6.3 Liveness command + scheduler

- New management command `accounts/management/commands/audit_healthcheck.py`
  per §2 (synthetic write+read-back, emits status JSONL + updates gauges).
- New model `AuditProbe` (tiny: `created_at`) **or** a dedicated system user —
  §2.1 recommends the separate table.
- Scheduler: host cron on opus/faberix (pilot) running
  `docker compose exec -T web python manage.py audit_healthcheck` every 60 s.
- New settings: `AUDIT_LIVENESS_STATUS` (path on the `audit_logs` volume),
  `AUDIT_LIVENESS_INTERVAL_SECONDS` (default 60).

### 6.4 Backlog reporting

- Add `--dry-run` to `backfill_audit_log`
  (`accounts/management/commands/backfill_audit_log.py`) that computes
  `created_would_be / skipped / rejected / malformed` **without** writing, so the
  backlog gauges (S3a/S3b/S3c) are produced by the exact same matching logic that
  performs reconciliation (no drift). A small cron emits these as gauges (via the
  exporter or a textfile-collector file the exporter reads).
- The oldest-age gauge (S3b) is derived from the minimum unreconciled
  `attempted_at`.

### 6.5 Metrics surface

- **Recommended (3.2b for full fidelity):** add `django-prometheus` (or a minimal
  custom `/internal/metrics` view wired in `parkshare/urls.py`) exposing S1, S2,
  S4 and the backlog/liveness gauges. Configure `prometheus_client` **multiprocess
  mode** (`PROMETHEUS_MULTIPROC_DIR` on a tmpfs in the `web` container) so the
  3–4 gunicorn workers aggregate correctly. **The metrics endpoint must not be
  internet-exposed** — bind it to the host-internal port and restrict by firewall
  (it leaks operational shape and must not sit behind the public Caddy route).
- **Minimum-viable alternative (3.2a):** skip in-app metrics; derive S1/S2 from
  Loki via LogQL recording rules, and export only S3/S5 gauges from a tiny
  `audit-exporter` sidecar (Python `prometheus_client` http server on `:9108`)
  that runs the dry-run backlog and reads the liveness status file.

### 6.6 Log-shipping agent in compose

- New compose service `log-shipper` (Grafana Agent or Promtail) on the app host,
  mounting the `audit_logs` volume (ro) and the container log stream, labeling
  `env`/`host`/`service`/`stream`, shipping to Loki on monitrix over TLS.

### 6.7 Network / firewall (security-sensitive — requires human approval)

> Per repo policy, firewall/network changes require human approval before
> writing or executing. This spec only **states the requirement**; infra must
> approve and apply.

- Allow **monitrix → app host** inbound on the **metrics scrape port** only
  (e.g. `9108/tcp`), source-restricted to monitrix's IP (UFW rule on
  opus/faberix). Do **not** open it to the world.
- Allow **app host → monitrix** outbound to the Loki push endpoint (TLS).
- The Postgres port stays **unpublished** (unchanged from `docker-compose.yml`);
  monitrix must **not** reach the DB directly — DB metrics, if any, come from a
  `postgres_exporter` co-located on the app host and scraped like the audit
  exporter.
- The recovery JSONL must never be served over the public Caddy route
  (Nexus → `:8001`); it is shipped only via the authenticated Loki path.

### 6.8 Settings additions (summary)

`parkshare/settings/base.py` / production `.env`:
`AUDIT_RECOVERY_LOG` (move onto volume), `AUDIT_LIVENESS_STATUS`,
`AUDIT_LIVENESS_INTERVAL_SECONDS`, `PROMETHEUS_MULTIPROC_DIR` (if 3.2b),
backlog alert thresholds `N`/`T` documented for monitrix.

---

## 7. Phased rollout

**Phase 0 — Make fail-open actually durable (BLOCKER, do before anything else)**
- §6.1: move `AUDIT_RECOVERY_LOG` onto a named `audit_logs` volume.
  Without this, the recovery log is ephemeral and the whole fail-open design is
  unsafe regardless of monitoring. This is the single highest-priority item.

**Phase 1 — Minimum viable monitoring (detect + page)**
- Liveness command + host cron (§6.3) writing the status JSONL.
- `audit-exporter` sidecar (3.2a) exposing S5 (liveness) and S3 (backlog via
  `--dry-run`) gauges on `:9108`.
- Firewall rule monitrix → `:9108` (human-approved).
- Prometheus on monitrix scrapes `:9108`.
- Alerts A2 and A6 wired to the external pager.
- **Outcome:** a broken audit path (even at zero traffic) and an unreconciled
  backlog both page on-call. This is the smallest set that makes fail-open safe.

**Phase 2 — Failure visibility + log forensics**
- `log-shipper` to Loki (§6.6); ship recovery JSONL + app logs.
- Alerts A1 (any failure), A4 (failures-without-recovery divergence), A5
  (rejected/forgery).
- Dashboard A (overview) + Dashboard B panels B1/B2/B4 (log-based).

**Phase 3 — Latency + full dashboards**
- In-app `/internal/metrics` (3.2b) with multiprocess mode for S4 histogram and
  exact S1/S2 counters.
- Alert A3 (latency leading indicator); Dashboard B3 (latency), B5 (DB context).
- Tune thresholds `N`/`T`/probe interval from observed pilot baselines.

---

## 8. Open items for infra / human decision

1. **Firewall rules (§6.7)** require human approval before application.
2. **Log transport nuance (§3.2):** confirm "monitrix pulls" is acceptable as
   *metrics pulled (scrape) + logs pushed by a thin app-host agent*. A strict
   no-outbound-from-app-host posture would force a less robust syslog-pull and
   should be explicitly chosen if required.
3. **External pager choice (§5.3):** which pager backend on monitrix
   (Grafana OnCall vs PagerDuty/Opsgenie). Must be independent of the app's
   notification stack.
4. **Backlog thresholds (A2):** pilot defaults `N=1`, `T=1h`; confirm with
   compliance whether any nonzero unreconciled audit gap is tolerable and for
   how long.

---

*Cross-references: CPS#78 (fail-open recovery design), CPS#88 / PR #88
(`parkshare/middleware.py`, `parkshare/settings/base.py`,
`accounts/management/commands/backfill_audit_log.py`, `accounts/models.py`),
CPS#87 (monitoring — this document is its design input).*
