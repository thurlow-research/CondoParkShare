# ADR-002 — Host Ingress & Audit-Monitoring Security (MVP)

*June 2026. Scope: MVP only ("minimum to operate the fail-open audit design safely in the pilot"). Supplements `docs/design/AUDIT-MONITORING-SPEC.md` (CPS#87), CPS#78 (fail-open audit), CPS#95 (restrict `:8001`), CPS#94/#96 (firewall). Binding on infra and coder agents.*

**Numbering:** the established ADR convention in this repo is `docs/architecture/ADR-NNN-<slug>.md` (see `ADR-001-pilot.md`). There is no `docs/adr/` directory. This is the second ADR, so it is **ADR-002** and lives alongside ADR-001. Do **not** create a parallel `docs/adr/` tree.

---

## Status

**Proposed (unratified).** MVP-scoped. Firewall/network changes herein still require human approval **before application on hosts** (per repo security policy and CPS#94); this ADR fixes the *design*, not the act of applying rules.

**Revision (2026-06): new material fact about existing monitoring on opus/faberix.** A live UFW rule on the app hosts is:

```
9100/tcp   ALLOW   192.168.1.7   # Prometheus scrape from monitrix
```

This establishes two facts that revise decisions A, B, D, F and the implementation contract:

- **node_exporter already runs on `:9100` as a host process.** UFW governs `:9100`, which means `:9100` is **not** a Docker-published port — the host-process exporter model this ADR chose **already exists** on these hosts.
- **monitrix is `192.168.1.7`, and the live scrape rule restricts to that single host (`/32`)** — tighter than the `/24` subnet finding #5 / PR #96 used.

The recommended MVP path below **rides the existing node_exporter via its textfile collector** instead of standing up a dedicated `:9108` exporter. This is presented for human ratification.

---

## Context

The fail-open audit design (CPS#78) means impersonated operator actions proceed even when the `AdminAuditLog` DB write fails, leaving a JSONL recovery record as the only trace. That is only safe if (a) the recovery sink is durable and shipped off-box, and (b) on-call is **paged** when audit writes fail. monitrix (separate host) pulls metrics and receives shipped logs. The monitoring spec (CPS#87) defines the signals, exporter on `:9108`, and the firewall opening.

Two ingress facts collide and must be reconciled here:

1. **Docker bypasses UFW for *published* ports.** A `ports:` mapping binds `0.0.0.0` by default and Docker installs `DOCKER`/`FORWARD`-chain DNAT rules that **skip UFW's INPUT chain**. A plain `ufw allow from X to any port P` does **not** restrict a Docker-published port. (Ratified finding #2.)
2. PR #96's `scripts/setup_firewall.sh` restricts `:9108` using exactly that ineffective pattern (`ufw allow in from <subnet> to any port $AUDIT_METRICS_PORT`). **If `:9108` is a Docker-published container port, those UFW rules are silently bypassed** and `:9108` is reachable from anything routable to the host. This is the central correctness problem this ADR resolves.

### Ratified findings (carried from infra-review; not re-derived)

| # | Finding | Disposition |
|---|---------|-------------|
| 1 | Docker only publishes ports under `ports:`. Only published port is `web: "8001:8000"`. `db` has no `ports:` (internal-only). `EXPOSE 8000` does not publish. | **Ratified.** No surprise listeners. |
| 2 | Docker-published ports bypass UFW's INPUT chain. `ufw allow ... to any port P` does NOT restrict them. | **Ratified.** Drives decisions A–D. |
| 3 | `setup-run.sh` opens 22/80/443; 80/443 are vestigial (no local Caddy in compose; Nexus front-proxies to `:8001`). | **Ratified.** See decision E. |
| 4 | Mechanisms that actually restrict Docker-published ports: (a) interface-bind the publish (`127.0.0.1:P` / `<LAN-IP>:P`), (b) `DOCKER-USER` chain iptables rules, (c) run as a host process (then UFW governs). | **Ratified.** Decisions A–C pick among these. |
| 5 | Human-locked: separate ports for web vs monitoring; metrics source = monitrix LAN `/24` (v4 `192.168.1.0/24`, v6 `fd74:a5b1:bbd1:1::/64`); logs pushed outbound to Loki (no inbound port); security changes need approval. | **Partially superseded** by finding #6: the live host rule restricts scrape to monitrix `/32` (`192.168.1.7`), tighter than the `/24`. The `/24` instruction is revised — see decision B. |
| 6 | **Live fact (2026-06):** node_exporter runs as a **host process on `:9100`**, scraped by monitrix at **`192.168.1.7/32`** (single host, not subnet), per the live UFW rule `9100/tcp ALLOW 192.168.1.7`. | **Ratified (observed on host).** Drives the textfile-ride recommendation (A) and the `/32` source reconciliation (B). |

---

## Decision A — How audit metrics are exposed: **ride the existing node_exporter textfile collector** (recommended)

**Decision (recommended, MVP):** do **not** stand up a dedicated audit-metrics exporter on `:9108`. Instead, **ride the node_exporter already running on `:9100`** (finding #6) via its **textfile collector**. An MVP `manage.py audit_healthcheck` (and the backlog dry-run) writes a `parkshare_audit.prom` file into node_exporter's textfile-collector directory; node_exporter exposes those gauges on the existing `:9100`; monitrix's existing scrape of `:9100` (the live `192.168.1.7` rule) picks them up unchanged.

**Why this is the right MVP path:** all MVP signals are **periodic gauges** — S5 liveness and S3a/S3b/S3c backlog — refreshed on a fixed cadence. That is exactly the workload the textfile collector exists for (a cron/timer writes a `.prom` snapshot; node_exporter re-reads it on each scrape). There is no need for a long-lived HTTP listener, a new port, a new scrape job, a new systemd service unit, or a new firewall rule.

**The one dependency, stated explicitly:** node_exporter must be started with **`--collector.textfile.directory=<dir>`** (and the textfile collector enabled). If the live exporter does not already have that flag, enabling it is a **flag/systemd change on the existing node_exporter** (edit its unit/args + restart) — a host-config change, human-gated like any host change, but **not** a new service.

**Consequences of the textfile ride (state plainly):**
- **No new port.** Metrics ride `:9100`.
- **No new firewall rule.** The existing `9100/tcp ALLOW 192.168.1.7` already admits monitrix. **The inbound `:9108` allow that PR #96 adds becomes unnecessary for MVP.**
- **No new scrape job** on monitrix. The existing `:9100` job carries the new `parkshare_audit_*` series.
- **No new systemd unit** for an exporter. Only the periodic `.prom` writer (the `audit_healthcheck` cron already required by the design) and the one-time textfile-dir enablement on node_exporter.
- The PII-bearing recovery JSONL is **never** written to the textfile dir — only non-PII gauges. (Same property as before: Loki path carries forensics.)

**What still must exist:** the `.prom` writer must emit valid Prometheus exposition text **atomically** (write to a temp file in the same dir, then `rename(2)`) so node_exporter never reads a half-written file, and it must include a freshness gauge (e.g. `parkshare_audit_healthcheck_unixtime`) so a stalled writer is detectable as staleness rather than silently serving last-good values.

**Fallback — dedicated `:9108` host-process exporter (NOT recommended for MVP):** if the textfile collector cannot be enabled on node_exporter, fall back to the previously-chosen model: a small `prometheus_client` host-process HTTP server (spec §6.5 option 3.2a) bound to `<LAN-IP>:9108`, **not** a container, **not** in-app. That model's full rationale (makes finding #2 a non-issue; avoids in-app multiprocess; reads the same durable artifacts) is retained below for completeness, but it is the fallback, not the primary path:

- It makes finding #2 a non-issue because UFW governs host processes (only relevant if a new `:9108` rule is opened — see decision B).
- It avoids in-app `/internal/metrics`, which would require `prometheus_client` multiprocess mode across gunicorn workers and couple metrics to the public `:8001` route — Phase-3 scope (decision F).
- It reads the same artifacts (S3 backlog via `backfill_audit_log --dry-run`, S5 liveness via the status file) the design already produces.

Under the fallback, the exporter is a host-level systemd service exposing gauges on `<LAN-IP>:9108` and does **not** appear in `docker-compose.yml`. The `audit-exporter` "sidecar" phrasing in the spec is overruled for MVP either way.

---

## Decision B — Scrape source restriction: **match the existing single-host (`/32`) convention**

**Under the recommended textfile ride (decision A), inbound restriction is moot for MVP.** Audit gauges ride the existing `:9100`, whose live UFW rule already restricts the scrape to `192.168.1.7` (monitrix, `/32`). No new inbound rule is added, so there is nothing new to restrict. This bullet's reconciliation applies only to the `:9108` fallback.

**Recommendation (revises the earlier `/24` instruction — for human ratification):** if the dedicated-`:9108` fallback is taken, restrict its inbound UFW allow to the **single host `192.168.1.7/32`** (and the IPv6 equivalent as a `/128`), **not** the `/24`+`/64` that finding #5 / PR #96 currently use. Rationale: the human's **own live rule** on `:9100` already restricts scrape to `192.168.1.7/32` — the tighter convention already exists on the host. Matching it gives consistency across the two scrape ports and least privilege. This **supersedes the earlier `/24` human instruction** (finding #5); it is presented as a recommendation because reversing a prior human-locked decision requires the human to ratify.

**Fallback restriction, two layers (only if `:9108` is built):**
1. **Bind the exporter listener to the host's LAN IP only** (`<LAN-IP>:9108`), never `0.0.0.0:9108`.
2. **Source-restrict the UFW allow to `192.168.1.7/32` + the monitrix `/128`** (set `MONITRIX_SCRAPE_SRC_V4/V6` accordingly — the PR #96 script is already parameterized, so this is an `.env` value, not a code change). Because the fallback exporter is a host process (decision A), this UFW rule is effective.

**Residual exposure (fallback only):** with the `/32`+`/128`, only monitrix can reach `:9108`. Metrics are non-PII gauges regardless. Under the textfile ride, exposure is identical to the existing `:9100` posture — no change.

---

## Decision C — How `:8001` (web) is restricted to Nexus: **interface-bind, and it is MVP**

**Decision:** restrict `:8001` to Nexus by **interface-binding the Docker publish**, and this is **in MVP scope** (closes CPS#95).

**Mechanism:** change `docker-compose.yml` from `"8001:8000"` to a **specific-interface publish**: `"<LAN-IP>:8001:8000"` (the host's private-segment IP that Nexus routes to). This removes the `0.0.0.0` wildcard bind so `:8001` is no longer reachable on every interface.

**Why interface-bind, not `DOCKER-USER`, not plain UFW:**
- Plain UFW is **out** — finding #2: it does not filter Docker-published ports.
- `DOCKER-USER` iptables rules **would** work and allow a true Nexus-only `/32` source restriction, but they are an extra, hand-maintained iptables surface (ordering, persistence across reboots, IPv6 parity) that is heavier than MVP needs.
- **Interface-bind** is a one-line compose change, declarative, survives restarts natively, and removes the wildcard exposure — which is the actual CPS#95 gap ("reachable from anywhere routable to the host"). It does **not** restrict to Nexus's single IP (any host that can route to `<LAN-IP>` can still reach `:8001`), but it removes the broad `0.0.0.0` exposure and confines reachability to the private segment Nexus lives on.

**Why MVP, not deferred:** CPS#95 is a *pre-existing, currently-live ingress gap* — `:8001` is wildcard-bound **today**. Shipping the monitoring/firewall work while leaving the larger web port wide open is incoherent. The fix is a single compose line; there is no reason to defer it.

**Residual exposure & the deferred tighten:** interface-bind confines `:8001` to the LAN segment but not to Nexus's exact IP. True Nexus-`/32` restriction (via a `DOCKER-USER` rule) is **deferred** hardening (decision F). For MVP, segment-confinement on a private LAN behind a TLS-terminating front proxy is acceptable.

---

## Decision D — Where each control is codified

**Decision (recommended textfile-ride path):** the audit signal is codified as a **`.prom` file written into node_exporter's textfile directory** by the periodic `audit_healthcheck` cron — no new exporter, no new compose service, **no new firewall rule**. The host firewall script `scripts/setup_firewall.sh` and its invoker `scripts/deploy.sh` (both from CPS#96 — **do not duplicate or rewrite them**) are **retained but their inbound `:9108` branch is not applied for MVP**. Codify the `:8001` interface-bind in `docker-compose.yml`. Reconcile `tools/setup-run.sh` so it does not re-establish broad rules.

| Control | Codified in | Notes |
|---------|-------------|-------|
| Audit gauges as `parkshare_audit.prom` in node_exporter textfile dir | `audit_healthcheck` cron writer + node_exporter `--collector.textfile.directory` (host systemd/args change) | **Recommended (decision A).** Rides existing `:9100`. No compose service. |
| Audit-metrics inbound firewall allow | **None for MVP.** Rides the existing `9100/tcp ALLOW 192.168.1.7` host rule. | Textfile ride needs no new inbound rule (decision A/B). |
| `:8001` interface-bind to `<LAN-IP>` | `docker-compose.yml` (`"${WEB_BIND_IP}:8001:8000"`) | Decision C. Declarative; needs no firewall rule. |
| 22 (SSH) inbound | `tools/setup-run.sh` UFW section | Keep. |
| 80/443 inbound | `tools/setup-run.sh` UFW section | **Remove** — decision E. |
| **Loki outbound** push (Phase 2) | `scripts/setup_firewall.sh` (CPS#96 `LOKI_PORT` branch, skipped until set) | The PR #96 script is **kept for this**: its parameterized outbound rule serves the Phase-2 Loki push. |
| `:9108` dedicated exporter + inbound allow | **Fallback only** — `scripts/setup_firewall.sh` `:9108` branch with source = `192.168.1.7/32` + `/128` | Not applied unless textfile collector cannot be enabled (decision A fallback). |

**PR #96 impact (precise).** Under the recommended textfile ride:
- **Remains useful:** the `.env` vars PR #96 introduced (`MONITRIX_HOST`, `MONITRIX_SCRAPE_SRC_V4/V6`, `LOKI_PORT`); and the **parameterized firewall script itself, kept for the Phase-2 Loki *outbound* rule** (the `LOKI_PORT` branch). `scripts/deploy.sh` wiring stays.
- **Becomes unnecessary for MVP:** the **inbound `:9108` allow** PR #96 adds — the textfile ride opens no new port, so this rule is not applied. `AUDIT_METRICS_PORT` becomes a fallback-only var.

Under the dedicated-`:9108` **fallback**, PR #96 stands as written **except** its inbound source must change from the `/24`+`/64` to **`192.168.1.7/32` + the monitrix `/128`** (decision B).

**Reconciliation of the two scripts (explicit, because they overlap):**
- `tools/setup-run.sh` is the **host bring-up** script (Docker install, base UFW posture, deploy, systemd). It owns the **baseline** UFW state: `default deny incoming`, `default allow outgoing`, and `allow 22`.
- `scripts/setup_firewall.sh` (CPS#96) is the **monitrix-integration** firewall script: it *adds* the `:9108` inbound allow and the Loki outbound allow on top of the baseline. It is idempotent and re-run on every deploy by `scripts/deploy.sh`.
- **Decision: keep them separate, with a clean seam.** `setup-run.sh` establishes baseline + SSH; `setup_firewall.sh` owns all monitrix-related openings. Do **not** fold the monitrix rules into `setup-run.sh` (that would split the monitoring firewall logic across two files), and do **not** move SSH/baseline into `setup_firewall.sh` (deploy re-runs it every push; baseline belongs in bring-up). The one required change to `setup-run.sh` is removing 80/443 (decision E) so it doesn't advertise a misleading posture.

---

## Decision E — Vestigial 80/443: **remove from `tools/setup-run.sh`**

**Decision:** **remove** the `ufw allow 80/tcp` and `ufw allow 443/tcp` lines from `tools/setup-run.sh` (lines 51–52).

**Rationale:** there is no local Caddy in `docker-compose.yml` (finding #3); TLS terminates on Nexus and is reverse-proxied to `:8001`. Nothing on opus/faberix listens on 80 or 443. Leaving them open advertises a service that does not exist, widens the host's attack surface for no benefit, and contradicts the (correct) deny-by-default posture. The comments referencing "(Caddy)" are stale. Remove both lines and their comments.

**Note:** if a future tenant requires HTTP-01 ACME on the app host directly (ADR-001 mentions a `bellevuetowers.org` HTTP-01 path), 80 would be re-added **then, deliberately, scoped to that need** — not kept speculatively now.

---

## Decision F — Explicitly OUT of MVP scope (do not build)

Infra/coder must **not** over-build these. They are deferred, not rejected:

1. **In-app `/metrics` (3.2b) + `prometheus_client` multiprocess mode** — Phase-3 latency fidelity (S4 histogram, exact S1/S2 counters). MVP emits only S3 (backlog) + S5 (liveness) as textfile gauges. Not needed to page on a broken audit path.
2. **`audit-exporter` as a compose sidecar** — overruled for MVP in favor of the textfile ride (decision A). No sidecar service in `docker-compose.yml`. A dedicated `:9108` host process is the fallback, not the primary path.
3. **A dedicated `:9108` exporter, its systemd unit, and its inbound firewall rule** — unnecessary under the textfile ride (decision A). Build only if the textfile collector cannot be enabled. **`DOCKER-USER` Nexus-`/32` restriction for `:8001`** remains a deferred hardening (decision C); MVP ships segment confinement.
4. **`DOCKER-USER` iptables framework** of any kind — not introduced for MVP; interface-binds cover the MVP need without a hand-maintained iptables chain.
5. **Loki log-shipper, the `log-shipper` compose service, and the Loki outbound firewall rule (`LOKI_PORT`)** — Phase 2 (failure forensics, A1/A4/A5 alerts). MVP = Phase 0 (durable volume — already done in `docker-compose.yml`) + Phase 1 (liveness + backlog gauges, scrape, A2/A6 paging). The `LOKI_PORT` branch in `setup_firewall.sh` correctly **skips** until set; leave it skipped for MVP.
6. **mTLS / basic-auth on the `:9108` scrape** — the commented blocks in `monitoring/prometheus/parkshare-audit.scrape.yml` stay commented. Source-IP firewall on a private LAN is the MVP control.
7. **cAdvisor / `postgres_exporter` (Panel B5 DB context)** — explicitly optional in the spec; out of MVP.
8. **External pager backend selection (PagerDuty/Opsgenie/Grafana OnCall)** — a monitrix-side / human decision (CPS#87 open item 3), not an app-host architecture decision. MVP requires only that A2/A6 route to *an* independent pager on monitrix, not which one.

---

## Implementation contract (infra/coder execute against this)

> Firewall application on hosts still requires human approval (CPS#94). Items below are the *design to implement*; applying UFW remains human-gated.

1. **`docker-compose.yml` — interface-bind the web publish.** Change `web.ports` from `- "8001:8000"` to `- "${WEB_BIND_IP}:8001:8000"` (or a hardcoded per-host `<LAN-IP>`). Add `WEB_BIND_IP` to `.env.example` with a comment that it is the host's private-segment IP that Nexus routes to. `db` stays unpublished. (Closes CPS#95 at MVP level.)
2. **Audit metrics — textfile collector ride (recommended).** Have the `audit_healthcheck` cron (item 3) **also write `parkshare_audit.prom`** atomically (temp file in the same dir + `rename(2)`) into node_exporter's textfile-collector directory, emitting S3a/S3b/S3c backlog gauges (from `backfill_audit_log --dry-run`), S5 liveness gauges, and a `parkshare_audit_healthcheck_unixtime` freshness gauge. **Enable `--collector.textfile.directory=<dir>` on the existing node_exporter** (its systemd unit/args — a host-config change, human-gated). No new port, no new exporter, no new compose service, no new firewall rule, no new scrape job. *(Fallback, only if textfile collector cannot be enabled: a `prometheus_client` host-process server bound to `<LAN-IP>:${AUDIT_METRICS_PORT}` (9108), not in compose, with PR #96's `:9108` inbound allow sourced to `192.168.1.7/32` + the monitrix `/128`.)*
3. **Liveness scheduler — host cron** running `docker compose exec -T web python manage.py audit_healthcheck` every `AUDIT_LIVENESS_INTERVAL_SECONDS` (default 60), per spec §6.3, writing the status JSONL onto the `audit_logs` volume **and** the `.prom` snapshot (item 2). (The `audit_healthcheck` command + `AuditProbe` table are spec §6.3 deliverables.)
4. **`scripts/setup_firewall.sh` (CPS#96) — keep, but do not apply the `:9108` inbound branch for MVP.** The textfile ride opens no new port; the existing `9100/tcp ALLOW 192.168.1.7` host rule already admits monitrix. Keep the script for its **`LOKI_PORT` outbound branch** (Phase 2), which stays skipped until `LOKI_PORT` is set. If the `:9108` fallback is taken instead, set `MONITRIX_SCRAPE_SRC_V4/V6` to `192.168.1.7/32` + the monitrix `/128` (decision B) and verify `AUDIT_METRICS_PORT` matches the exporter bind port.
5. **`tools/setup-run.sh` — remove 80/443.** Delete lines 51–52 (`ufw allow 80/tcp` / `ufw allow 443/tcp`) and their comments. Keep `default deny incoming`, `default allow outgoing`, `allow 22`. (Decision E.)
6. **`.env.example` — carries the monitoring vars** (`MONITRIX_SCRAPE_SRC_V4/V6`, `LOKI_PORT=` empty, `MONITRIX_HOST`; `AUDIT_METRICS_PORT=9108` now **fallback-only**). Add `WEB_BIND_IP` (decision C/contract item 1). Add the node_exporter textfile-collector directory path (e.g. `NODE_EXPORTER_TEXTFILE_DIR`) the `.prom` writer targets. No other env changes for MVP.
7. **`monitoring/prometheus/parkshare-audit.scrape.yml` — fill the `OPUS_LAN_IP_OR_HOSTNAME` / `FABERIX_LAN_IP_OR_HOSTNAME` placeholders** with the actual app-host LAN addresses on monitrix. Leave TLS/basic_auth blocks commented (decision F.6). This is a monitrix-side change, not app-host.
8. **Post-apply verification (record in the deploy runbook):**
   - **Textfile ride:** from monitrix, the existing `:9100` scrape must show the new `parkshare_audit_*` series (including the freshness gauge); confirm `parkshare_audit.prom` is being rewritten on cadence and node_exporter has the textfile dir flag. No `:9108` listener should exist.
   - **Fallback only:** from a host **not** `192.168.1.7`, `curl <LAN-IP>:9108/metrics` must **fail/time out**; from monitrix it must **succeed**.
   - From a host on the LAN segment that is **not** Nexus: `:8001` reachability is acceptable for MVP (segment-confined) but must **not** be reachable from outside the segment.
   - `db` (5432) must be unreachable from every external host.

---

## Decision summary

| | Decision |
|---|---|
| A | Audit metrics **ride the existing node_exporter `:9100` via its textfile collector** (`parkshare_audit.prom` written by the `audit_healthcheck` cron). One dependency: enable `--collector.textfile.directory` on node_exporter. No new port/exporter/scrape job/firewall rule. Dedicated `:9108` host process is the **fallback** only. |
| B | Scrape source: **match the existing single-host `192.168.1.7/32` convention** (+ monitrix `/128`), **revising** finding #5's `/24` — for human ratification. Under the textfile ride this is **moot** (rides the existing `.7` rule on `:9100`); it applies only to the `:9108` fallback. |
| C | `:8001` restricted to Nexus by **interface-binding the compose publish** (`${WEB_BIND_IP}:8001:8000`); **in MVP** (closes #95). `DOCKER-USER` `/32` tighten deferred. *(Unchanged by the new fact.)* |
| D | No new firewall rule for MVP audit metrics (textfile ride). **`scripts/setup_firewall.sh` kept** for the Phase-2 Loki **outbound** rule + its `.env` vars; its **inbound `:9108` branch is not applied for MVP**. Interface-binds in `docker-compose.yml`; `tools/setup-run.sh` keeps baseline+SSH only. |
| E | **Remove** vestigial 80/443 from `tools/setup-run.sh` (no local Caddy). *(Unchanged.)* |
| F | Deferred / do NOT build: in-app `/metrics`+multiprocess, exporter-as-sidecar, **the dedicated `:9108` exporter + its inbound rule** (fallback only), `DOCKER-USER` framework, Loki shipper + outbound rule, scrape mTLS/auth, cAdvisor/pg_exporter, pager-backend choice. |
