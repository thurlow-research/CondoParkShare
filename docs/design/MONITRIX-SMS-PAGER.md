# monitrix SMS Pager — Design + Procurement Checklist (MVP)

*June 2026. **monitrix-side** infrastructure only (separate host: Grafana + Prometheus). **Not** CPS application code — nothing here ships in the CPS repo.*

Tracks: **CPS#98** (this work). Supplements: **CPS#87** (monitoring spec), **ADR-002 §F.8 / finding #3** (pager fixed as "SMS, hosted on monitrix"), **CPS#78** (fail-open audit). Locked direction: **Option A — cloud SMS via Grafana Alerting → Twilio** (self-hosted GSM rejected).

---

## 0. Why this exists (one paragraph)

The fail-open audit design (CPS#78) lets impersonated operator actions proceed even when the `AdminAuditLog` DB write fails, leaving only a JSONL recovery record. That is safe **only if on-call is paged** when the audit path breaks. Two alerts carry that obligation, and both route here:

- **A6 — Liveness** (`critical`): `parkshare_audit_liveness_ok == 0` OR `liveness_age_seconds > 150` OR no liveness data for 5 m. The traffic-independent backstop — a broken audit path at zero impersonation traffic is otherwise invisible.
- **A2 — Backlog** (`high`): `backlog_records > 1` OR `backlog_oldest_seconds > 3600` sustained 10 m. The safety net is accumulating un-reconciled records.

> **Label note:** the authoritative source (`AUDIT-MONITORING-MVP-SCOPE.md` §2.6) maps **A6 = liveness, A2 = backlog**. The CPS#98 task text used the inverse parenthetical ("A2 liveness / A6 backlog"); the spec mapping above governs. Both route to the same SMS pager, so the swap does not change the wiring — but use **A6=liveness, A2=backlog** in rule names to stay consistent with the spec.

Delivery reliability is paramount: a pager that silently fails to deliver is worse than no pager.

---

## 1. Topology / mechanism

**Assumption (flag):** monitrix runs **Grafana OSS** (no Grafana Cloud, no Enterprise). The recommendation holds under that assumption; it is noted below where Cloud/Enterprise would change it.

Two candidate mechanisms were considered:

| | (a) Grafana **webhook** contact point → tiny relay → Twilio REST | (b) **Grafana OnCall (OSS)** self-hosted on monitrix, native Twilio SMS |
|---|---|---|
| New moving parts | One small relay service (e.g. a ~30-line Flask/FastAPI app or a CGI-style script) + its env file | OnCall engine + its own DB (Postgres/SQLite) + Celery/Redis worker + a Grafana plugin |
| Native SMS | No — relay implements the Twilio `Messages` REST call | Yes — OnCall has a built-in Twilio integration |
| Escalation / ack / on-call schedules | None (out of MVP scope anyway) | Yes (the reason to adopt it later) |
| Operational surface | Minimal: one process, one secret file | Substantial: multi-service stack to install, run, patch, and back up |
| Failure blast radius | Relay down ⇒ SMS fails, **but alert still visible in Grafana + email fallback** (see §4) | More components that can independently break the pager path |

### Recommendation: **(a) webhook contact point → tiny Twilio relay**, for MVP.

Rationale:

1. **MVP needs delivery, not orchestration.** MVP scope is "page two alerts to one or two phones." Escalation rotations, ack, and schedules are explicitly out of scope (CPS#98). OnCall's value is exactly those features — paying its operational cost now buys nothing MVP needs.
2. **Smallest reliable surface.** A single relay process with one secret file is the least that can break. OnCall adds a DB + worker + Redis + plugin — every one a new failure mode on the host whose *job* is to be more reliable than the app it watches ("who watches the watcher").
3. **Grafana OSS has no native SMS contact point.** Under the flagged OSS assumption, there is no built-in SMS channel; the webhook contact point is the idiomatic OSS path to a custom sender. (Grafana Cloud/Enterprise change this — see below.)

### Upgrade path (note, do not build now)

Adopt **Grafana OnCall (OSS)** when MVP grows real on-call needs: multiple responders, **escalation chains** (SMS → call → next person), **ack/resolve from the phone**, and **rotation schedules**. OnCall has native Twilio SMS *and* voice, so it subsumes the relay. Migration is a contact-point swap in Grafana, not a re-architecture. Alternatively, if monitrix ever moves to **Grafana Cloud or Enterprise**, those ship OnCall-as-a-service / native channels and the relay can be retired — **flag this assumption-break to revisit the recommendation**.

### Chosen data path

```
audit_healthcheck cron (opus/faberix)
  → parkshare_audit.prom  (node_exporter textfile dir, ADR-002 decision A)
  → node_exporter :9100   → Prometheus scrape (monitrix)
  → Grafana alert rules A6 (liveness) + A2 (backlog)
  → notification policy (label routing, grouping, dedup)
  → contact point "sms-pager" (webhook → relay)  → Twilio Messages API → recipient phone(s)
                                  └─ contact point "email-fallback" (always, in parallel)
```

The relay is a localhost-only HTTP endpoint on monitrix (bind `127.0.0.1`, no inbound firewall opening). Grafana posts Alertmanager-format JSON; the relay maps it to one Twilio `Messages.create` call per recipient and returns 200. Keep it stateless and dumb.

---

## 2. A6 / A2 routing to SMS

Alert *rule* definitions are a build item (CPS#87 §5.1) — kept light here. Focus is the routing.

**Labels on the rules** (set in the rule definition so the policy can route on them):

| Rule | `severity` | `team` | `pager` |
|---|---|---|---|
| A6 — liveness | `critical` | `oncall` | `sms` |
| A2 — backlog | `high` | `oncall` | `sms` |

**Notification policy (Grafana Alerting):**

- **Root policy** → contact point `email-fallback` (catch-all, so nothing is ever unrouted).
- **Nested policy: match `pager = sms`** → contact point `sms-pager` (the webhook relay), **with `continue = true`** so the same alert *also* falls through to `email-fallback`. Result: every paged alert is delivered by **both** SMS and email in parallel — email is the safety net for an SMS-path failure (§4).

**Grouping / dedup / anti-storm (the part that matters for a sustained outage):**

- **Group by** `["alertname"]` (and optionally `severity`). A6 and A2 group separately; a single sustained liveness outage is **one** notification group, not one SMS per scrape.
- **Group wait** `30s` — brief coalesce before first page.
- **Group interval** `5m` — new alerts joining an existing group wait up to 5 m before a follow-up SMS.
- **Repeat interval** `1h` — a *still-firing* alert re-pages at most hourly. This is the primary SMS-storm guard: a multi-hour Postgres outage costs ≤ ~1 SMS/hour per alert, not hundreds.
- **Silences:** on-call applies a Grafana **silence** (by `alertname` label, time-boxed) during a known maintenance window or an acknowledged incident, suppressing repeat SMS without disabling the rule. Prefer time-boxed silences over editing rules.

Net: worst-case sustained dual outage (A6 + A2 both firing for hours) = ~2 SMS/hour. Acceptable cost, no storm.

---

## 3. Secret handling

Twilio **Account SID** + **Auth Token** (and the Twilio sender number + recipient numbers) live **on monitrix only**. They are **never** committed to the CPS repo, never in CPS app config, never in any container image, never in Grafana dashboard/alert JSON that gets exported or version-controlled.

**Location (webhook-relay path — recommended):**

- Secrets live in the **relay's environment file**, e.g. `/etc/monitrix/sms-pager.env`, read by the relay's systemd unit via `EnvironmentFile=`.
- Contents: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `SMS_RECIPIENTS` (comma-separated E.164).
- **File posture:** owner = the dedicated relay service user (e.g. `monitrix-pager`), `chmod 600` (`-rw-------`), parent dir `/etc/monitrix` `chmod 750` owned by that user. Not world-readable, not group-readable by `grafana`.
- The systemd unit runs the relay as that **dedicated unprivileged user** (`User=monitrix-pager`, `DynamicUser` acceptable), `NoNewPrivileges=yes`, `ProtectSystem=strict`, with read access only to its env file.

**Grafana side:** the `sms-pager` contact point is a webhook to `http://127.0.0.1:<relay-port>` — it holds **no Twilio secret** (the relay does). If a future shared secret authenticates Grafana→relay, store it via Grafana **provisioning secrets** (`$__file{}` / env-substituted provisioning), not inline in dashboard JSON.

**OnCall path (upgrade only):** secrets go in OnCall's env (its `.env` / compose secrets on monitrix), same `chmod 600` + dedicated-user posture. Same rule: never in the CPS repo.

**Rotation:** Twilio Auth Token rotation = update the env file + `systemctl restart` the relay. No CPS deploy involved (correctly — this host is independent of the app).

---

## 4. Failure modes (delivery reliability)

| Failure | Does the alert still surface? | Mitigation |
|---|---|---|
| **Twilio API unreachable / 5xx / throttled** | Yes — alert is **firing and visible in Grafana**, and the parallel **email-fallback** contact point still delivers. | Email fallback (§2, `continue=true`). Relay logs the Twilio error to journald; relay returns non-200 so Grafana records a contact-point failure (surfaced in Grafana's notification error state). Relay does a bounded retry (e.g. 2 attempts, short backoff) then gives up to email. |
| **Relay process down** | Yes — alert visible in Grafana; **email-fallback** still fires (independent contact point). Grafana logs the webhook as failed. | `systemd` `Restart=on-failure`. Email fallback covers the gap. |
| **monitrix Grafana/Prometheus down** | The pager path is down. | Out of MVP scope to fully self-monitor monitrix, but: the **synthetic heartbeat** below makes a dead path *detectable by its silence*. A simple external uptime check on monitrix is a reasonable Phase-2 add. |
| **Recipient phone off / no signal** | SMS undelivered (Twilio may report delivery status async). | Email fallback; multiple recipients (§5) reduce single-point risk. |

**Fallback channel — required, not optional:** every paged alert **also** goes to **email** (`email-fallback` contact point, SMTP configured on monitrix to a real on-call inbox). This guarantees a pager-delivery failure is **not silent**. Email is *not* the app's own SMTP (which is impaired if Postgres is down — spec §5.3); use monitrix's own SMTP relay / a third-party transactional SMTP independent of the CPS app stack.

**Who watches the watcher — synthetic test alert (required for MVP):** stand up a **always-firing low-severity heartbeat alert** (e.g. a Grafana alert on a constant expression, label `pager = sms-heartbeat`) that routes to the SMS pager on a **slow repeat interval (e.g. once every 24h)**. If on-call stops receiving the daily heartbeat SMS, the *entire pager path* (Grafana → relay → Twilio → phone) is proven broken **before** a real incident needs it. Route the heartbeat to email too. This is the single most important reliability control here: it converts a silent-failure pager into a self-testing one. Keep its repeat interval long enough not to be annoying (daily) but short enough to bound undetected-breakage to ≤ 1 day.

---

## 5. Human prerequisites checklist (THE deliverable)

Nothing below can be automated by an agent — all require a human with a Twilio account and a payment method. **Start the 10DLC registration first; it has multi-day lead time and gates US delivery.**

- [ ] **1. Twilio account** — create at twilio.com; add a payment method / billing. Note the **Account SID** and **Auth Token** (Console dashboard). Treat the Auth Token like a password.
- [ ] **2. Twilio sender phone number** — buy an SMS-capable number (a US **local 10DLC** number, or a Toll-Free number). ~**$1–2/month** rental. This is the `From` number.
- [ ] **3. Account SID + Auth Token** — copy both into monitrix at `/etc/monitrix/sms-pager.env`, `chmod 600`, owned by the relay service user. **Never** put them in the CPS repo or app.
- [ ] **4. Recipient number(s)** — the on-call mobile number(s) in **E.164** (e.g. `+15551234567`). Recommend **≥ 2 recipients** so a single dead phone doesn't drop the page. Goes in `SMS_RECIPIENTS`.
- [ ] **5. US A2P 10DLC registration (if sending to US numbers) — START EARLY.** Application-to-Person 10DLC requires registering a **Brand** (your org/identity) and a **Campaign** (use case: transactional/alerting) via Twilio. **Lead time: typically a few hours to several business days** (Brand vetting + Campaign approval; can be longer if vetting flags). Unregistered traffic to US numbers is **heavily filtered/blocked by carriers** — an unregistered pager will silently fail to deliver, defeating the entire purpose. *Toll-Free numbers use a separate verification process with similar lead time.* If recipients are **non-US**, 10DLC may not apply but check the destination country's rules. **Do this step first.**
- [ ] **6. SMTP for the email fallback** — a monitrix-side SMTP relay or transactional email provider (SendGrid/SES/etc.) **independent of the CPS app's SMTP**, plus the on-call inbox address. Configure as the `email-fallback` contact point.
- [ ] **7. Confirm geographic / compliance permissions** — in Twilio Console, enable **Messaging Geographic Permissions** for the recipient countries (off by default for some regions).

**Rough cost (US, indicative — verify current Twilio pricing):**

- Sender number: **~$1.15/mo** (local) or **~$2/mo** (toll-free).
- Per outbound SMS (US): **~$0.0079–0.0083 per segment** + **carrier/10DLC pass-through fees ~$0.003–0.005/segment**, so **~$0.012–0.015 per message** all-in is a safe planning number.
- One-time/recurring 10DLC: Brand registration **~$4 one-time**, Campaign **~$10–15/mo** (varies by use case).
- **Volume is tiny:** anti-storm caps (§2) hold this to a few SMS/hour worst case + one daily heartbeat (~30/mo baseline). Expected monthly SMS spend is **< $1**; the dominant cost is the number rental + 10DLC campaign fee (~$11–17/mo total).

---

## 6. Out of scope (per CPS#98)

- **Building** any of this — follow-up work once the §5 checklist is complete (the human steps gate the build).
- **Escalation rotations / schedules / ack** beyond MVP single-tier paging (that's the Grafana OnCall upgrade path, §1).
- **Deferred alerts A1/A3/A4/A5** — those land with the Loki log-shipper (Phase 2) / latency (Phase 3); they are not wired to the pager now.

---

## 7. Acceptance (when the build later happens)

1. Sending a manual test alert with `pager=sms` delivers an SMS to **all** recipients **and** an email.
2. Stopping Postgres on an app host makes **A6** fire within ~2 probe intervals and SMS + email arrive.
3. A sustained outage produces **≤ ~1 SMS/hour per alert** (anti-storm verified).
4. Killing the relay process still delivers the **email** fallback and shows a contact-point failure in Grafana (no silent loss).
5. The **daily synthetic heartbeat** SMS arrives; deliberately breaking the relay stops it within 24h.
6. `grep -ri twilio` and a secret scan over the **CPS repo** return **nothing** — secrets live only on monitrix.
