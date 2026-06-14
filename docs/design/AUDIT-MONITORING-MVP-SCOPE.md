# Audit-Monitoring MVP Scope Decision

**Status:** Scope ruling — supersedes phased rollout table in AUDIT-MONITORING-SPEC.md §7 for
purposes of the pilot build. Spec phases remain valid as future-state; this document decides
what counts as "MVP done."
**Relates to:** CPS#87 (monitoring), CPS#91 (NAS backup / RPO), PR #88 (fail-open implementation)
**Source spec:** `docs/design/AUDIT-MONITORING-SPEC.md` (branch `docs/audit-monitoring-spec`)
**Date:** 2026-06-14

---

## 1. MVP goal

Make the fail-open audit design safe to operate in the pilot by ensuring that a broken audit
path or an unreconciled backlog is detected and pages on-call — even during periods of zero
impersonation traffic.

---

## 2. In scope for MVP

### 2.1 Prerequisite (must ship before any monitoring is meaningful)

**Named Docker volume for the recovery JSONL (spec §6.1, Phase 0 blocker)**

Move `AUDIT_RECOVERY_LOG` onto a named `audit_logs` volume in `docker-compose.yml`. Without
this, the recovery JSONL lives in the container's ephemeral layer; a container restart destroys
the only safety-net record of an operator action that never reached the DB. This is not a
monitoring item — it is the storage prerequisite that makes monitoring meaningful. Nothing else
in this list ships first.

### 2.2 Signals

| Signal | Metric(s) | Why it is in MVP |
|---|---|---|
| **S5 — Liveness** | `parkshare_audit_liveness_ok`, `parkshare_audit_liveness_age_seconds` | The only traffic-independent signal. Without it, a broken audit path at zero impersonation traffic is completely invisible. This is the non-negotiable backstop. |
| **S3a/S3b — Backlog count + oldest age** | `parkshare_audit_backlog_records`, `parkshare_audit_backlog_oldest_seconds` | Measures whether the safety net is actually being reconciled. A growing or aging backlog means operator actions exist only in the fragile JSONL — the precise condition monitoring must detect. |
| **S3c — Backlog rejected** | `parkshare_audit_backlog_rejected` | A nonzero rejected count is a security signal (forgery/tamper/cross-tenant attempt caught by backfill anti-forgery). The cost to export it alongside S3a/S3b is zero; omitting it would leave a security-class event silent. |

S1 (write-failure rate) and S2 (recovery-record emission) are produced by the log-shipping
pipeline (Loki + log-shipper), which is Phase 2 infrastructure. They are deferred — see §3.
S5 and S3 require only the lightweight audit-exporter sidecar (Phase 1 in the spec), which
is all MVP demands.

### 2.3 App-side components

**`manage.py audit_healthcheck`** (new management command, spec §6.3)
- Performs synthetic write + read-back via the same ORM/DB path the middleware uses.
- Writes to a separate `AuditProbe` model (not `AdminAuditLog`) to keep real audit records
  pristine.
- Emits liveness gauges and appends a one-line JSON status to `AUDIT_LIVENESS_STATUS` on the
  `audit_logs` volume.
- Catches its own exceptions and emits `ok:false` + nonzero exit code on DB failure.

**`--dry-run` mode on `backfill_audit_log`** (spec §6.4)
- Computes `created_would_be / skipped / rejected / malformed` without writing.
- Produces S3a/S3b/S3c gauge values using the exact same matching logic as the live reconciliation.
  This is what guarantees the backlog number matches what a real backfill run would fix.

**`audit-exporter` sidecar** (spec §6.5, option 3.2a)
- Lightweight Python `prometheus_client` HTTP server on port `9108`.
- Exposes S5 (liveness, by reading `AUDIT_LIVENESS_STATUS`) and S3a/S3b/S3c (backlog, by
  running `backfill_audit_log --dry-run`).
- Mounts the `audit_logs` volume read-only.
- Does NOT require multiprocess Prometheus mode (no in-app instrumentation of gunicorn workers).

**Host cron scheduler** (spec §2.2, §6.3)
- Runs `docker compose exec -T web python manage.py audit_healthcheck` every 60 seconds.
- Runs `backfill_audit_log --dry-run` on the same or a slightly longer interval for backlog
  gauges (60–300 s acceptable; pick the same 60 s for simplicity).
- Host cron on opus/faberix. No sidecar container needed for the pilot.

### 2.4 Dashboard

**Dashboard A — "Audit Health" (overview), panels A2 + A4 + A5 only**

The full Dashboard A JSON already exists at `monitoring/grafana/dashboards/audit-health.json`.
For MVP, the functional panels are:

| Panel | Why it is in MVP |
|---|---|
| A2 — Liveness Status + Liveness Age | Directly displays S5; the on-call first-look. |
| A4 — Recovery Backlog (count + oldest age) | Directly displays S3a/S3b. |
| A5 — Rejected / Malformed Records | Directly displays S3c (security signal). |

Panels A1 (write success rate), A3 (failures over time), and A6 (backfill runs) depend on
Loki log data (log-shipper, Phase 2). They are present in the dashboard JSON and will show
"No data" until Phase 2 ships; that is acceptable. The existing JSON does not need to be
modified or trimmed — import it as-is. Panels showing "No data" on unbuilt data sources do
no harm and give operators a clear picture of what monitoring is not yet live.

Dashboard B (Audit Forensics) is entirely deferred — see §3.

### 2.5 Prometheus scrape job

`monitoring/prometheus/parkshare-audit.scrape.yml` already exists and targets the correct
exporter port. Replace the IP placeholders for opus and faberix, add the job block to
monitrix's `prometheus.yml`, and reload. No changes to the scrape config file itself are
required.

### 2.6 Alerts

For MVP, wire exactly two alert rules on monitrix:

| ID | Condition | Severity | Rationale for MVP inclusion |
|---|---|---|---|
| **A6** | `parkshare_audit_liveness_ok == 0` OR `liveness_age_seconds > 150` OR no liveness data for 5 m | critical / page | The traffic-independent backstop. Without this, a broken audit path at zero activity is undetectable. |
| **A2** | `backlog_records > 1` OR `backlog_oldest_seconds > 3600` sustained 10 m | high / page | Detects that the safety net is accumulating un-reconciled records. The age threshold catches a stalled backfill even if count is low. |

Both must route to an external pager **independent of the application's notification stack**
(spec §5.3 — if Postgres is down, the app's own email/push queue is also impaired).

A1/A4/A5 are deferred with the log-shipping pipeline. A3 (latency) is deferred with Phase 3.

### 2.7 Firewall rule (requires human approval before application)

Monitrix → app host inbound on `9108/tcp`, source-restricted to monitrix's LAN IP. This is
required before the Prometheus scrape can deliver any data. Per spec §6.7 and repo security
policy, this rule must not be written or applied without explicit human sign-off.

---

## 3. Deferred (post-MVP)

| Item | Rationale for deferral |
|---|---|
| **S1 / S2 — write-failure + recovery-emission counters** | Require the Loki log-shipping pipeline (log-shipper compose service, §6.6). Log-shipper is Phase 2 infrastructure; adding it now doubles MVP scope. S5 liveness covers the "is it broken" question without logs. |
| **S4 — write-latency histogram (p99)** | Requires in-app instrumentation of the success path with a timer, plus Prometheus multiprocess mode for gunicorn workers (§6.5, 3.2b). Useful as a leading indicator but S6 liveness fires first; latency is Phase 3. |
| **Alerts A1, A4, A5** | A1/A4 depend on S1/S2 (log-derived, Phase 2). A5 (rejected) is already surfaced by S3c in panel A5 — an alert on it is additive and can be wired when A1/A4 land. |
| **Alert A3** | Depends on S4 histogram. Phase 3. |
| **Dashboard B — Audit Forensics (all panels)** | B1/B2/B4 require Loki. B3 requires S4 histogram. B5 (DB context) requires cAdvisor/postgres_exporter. All are Phase 2–3. |
| **Dashboard A panels A1, A3, A6** | Depend on Loki log data (log-shipper). They exist in the dashboard JSON and will show "No data" until Phase 2 ships; no action needed now. |
| **Log-shipping agent (Grafana Agent / Promtail)** | Phase 2. Required for S1/S2 visibility and all Loki-backed panels. |
| **JSON structured app logging (§6.2)** | Useful for uniform `| json` parsing in Loki. Deferred with log-shipper to Phase 2. |
| **In-app `/internal/metrics` endpoint (3.2b)** | Full histogram + exact per-worker counters. Phase 3 only. |
| **RPO / compliance formalization** | Folds into #91 (NAS backup + dual-write decision). Already tracked there; not a monitoring deliverable. |
| **Backlog alert threshold tuning (N, T)** | Pilot defaults `N=1`, `T=1h` are reasonable starting values. Tune after observing real traffic patterns. |
| **Pager backend selection** | The specific pager tool on monitrix (Grafana OnCall vs PagerDuty vs Opsgenie) is an infra/human decision. MVP requires only that A2/A6 route somewhere independent of the app — the exact tool is not in scope here. |

---

## 4. Acceptance criteria for "MVP monitoring done"

MVP is complete when all of the following are true:

1. **Volume in place:** `AUDIT_RECOVERY_LOG` and `AUDIT_LIVENESS_STATUS` are on the named
   `audit_logs` Docker volume; a `docker compose restart web` does not lose either file.

2. **Liveness probe running:** `manage.py audit_healthcheck` runs on a 60-second host-cron
   schedule on both opus (prod) and faberix (PPE); `AUDIT_LIVENESS_STATUS` shows a fresh
   `ok:true` entry within the last 2 minutes under normal DB conditions.

3. **Exporter serving metrics:** `curl http://app-host:9108/metrics` returns
   `parkshare_audit_liveness_ok`, `parkshare_audit_liveness_age_seconds`,
   `parkshare_audit_backlog_records`, `parkshare_audit_backlog_oldest_seconds`, and
   `parkshare_audit_backlog_rejected` with current values.

4. **Prometheus scraping:** monitrix Prometheus shows the five metrics above in its TSDB
   with no scrape errors.

5. **Dashboard importing:** Dashboard A imports cleanly to monitrix Grafana; panels A2, A4,
   and A5 display live data from Prometheus; panels with Loki dependencies show "No data"
   (acceptable at MVP).

6. **Alerts wired:** A6 and A2 alert rules exist in monitrix Grafana (or Alertmanager) and
   are in "normal" state under healthy conditions. A test can be demonstrated by stopping
   Postgres briefly and confirming A6 fires within ~2 probe intervals (~2 minutes).

7. **Firewall rule in place:** monitrix can reach app-host:9108; no other IP can
   (human-approved and applied).

8. **External pager target configured:** A6 and A2 route to a pager channel that does not
   depend on the application's DB, email, or push infrastructure.

---

## 5. Existing artifacts: MVP-ready vs needs work

| Artifact | Location | MVP status |
|---|---|---|
| Dashboard A JSON | `monitoring/grafana/dashboards/audit-health.json` | Ready — import as-is. Panels with no Loki data source show "No data" harmlessly. No trimming needed. |
| Dashboard B JSON | `monitoring/grafana/dashboards/audit-forensics.json` | Deferred — do not import at MVP; it requires Loki for all meaningful panels and adds operational noise with zero-data panels until Phase 2. |
| Prometheus scrape config | `monitoring/prometheus/parkshare-audit.scrape.yml` | Ready — replace IP placeholders and add to monitrix `prometheus.yml`. No other changes. |
| monitoring/README.md | `monitoring/README.md` | Accurate. No changes needed for MVP. |
| `backfill_audit_log` command | `accounts/management/commands/backfill_audit_log.py` | Needs `--dry-run` mode added (§6.4). |
| Docker compose | `docker-compose.yml` | Needs `audit_logs` named volume + mount (§6.1). |
| Settings | `parkshare/settings/base.py` | Needs `AUDIT_LIVENESS_STATUS` and `AUDIT_LIVENESS_INTERVAL_SECONDS` added (§6.8). `AUDIT_RECOVERY_LOG` path moves to the new volume mount point. |
| Liveness command | `accounts/management/commands/audit_healthcheck.py` | Does not exist yet — new deliverable (§6.3). |
| Audit-exporter sidecar | (none) | Does not exist yet — new deliverable (§6.5, 3.2a). |
| Alert rules | (none) | Do not exist yet — new monitrix-side deliverable (A2 + A6 per §5.1). |
| Log-shipper service | (none) | Deferred to Phase 2. |

---

## 6. Human decisions still required

**[HUMAN-DECISION-1] Firewall rule approval (blocking)**
Spec §6.7 and repo security policy require explicit human sign-off before the
`monitrix → app-host:9108/tcp` inbound rule is written or applied. Infra cannot
proceed on this item without it.

**[HUMAN-DECISION-2] External pager backend**
Which pager tool on monitrix handles A2/A6 alerts (Grafana OnCall, PagerDuty,
Opsgenie, or other)? The spec requires it be independent of the app stack; the
specific tool is a human/infra decision. Must be resolved before alert wiring
can be completed.

**[HUMAN-DECISION-3] Log-transport posture confirmation (not blocking for MVP)**
Spec §3.2 flags that "monitrix pulls" is satisfied for metrics via Prometheus
scrape, but logs require an agent on the app host pushing to Loki. Confirm this
push-for-logs model is acceptable before Phase 2 begins. This does not block
MVP (no Loki in MVP), but should be decided before Phase 2 is scoped.

---

*Cross-references: AUDIT-MONITORING-SPEC.md (branch `docs/audit-monitoring-spec`),
CPS#87 (monitoring tracking), CPS#91 (NAS backup / RPO), PR #88 (fail-open implementation).*
