# MONITRIX-SMS-PAGER — Operator Setup Runbook

**Applies to:** monitrix (the observability host running Grafana + Prometheus).
**Does NOT touch:** the CPS Django app, docker-compose.yml, or any app-host code.

**Cross-references:**
- CPS#98 — this work item
- ADR-002 §F.8 — pager fixed as SMS, hosted on monitrix
- CPS#87 / AUDIT-MONITORING-SPEC.md — monitoring spec (alert rules A6, A2)
- `monitoring/sms-pager/` — all scripts and units referenced below

---

## Overview

Two audit alerts route to this pager:

| Alert | Label | Condition | Severity |
|-------|-------|-----------|----------|
| A6 — liveness | `pager=sms` | `parkshare_audit_liveness_ok == 0` or age > 150 s or no data 5 m | critical |
| A2 — backlog | `pager=sms` | `backlog_records > 1` or `backlog_oldest_seconds > 3600` sustained 10 m | high |

Every paged alert reaches **both** SMS (via a localhost Twilio relay) and email (independent SMTP configured in Grafana) in parallel. Email is not optional — it is the safety net for an SMS-path failure.

**Credential separation:** The relay (`sms-pager.service`) holds only Twilio credentials and the webhook shared secret — it never sends email. SMTP credentials live in `grafana.ini [smtp]` and the Grafana systemd override only. This limits what is exposed if the relay process is ever compromised.

The **daily synthetic heartbeat** proves the full chain is working before a real incident needs it. If the heartbeat stops arriving, the pager is broken.

---

## Part 1 — Procurement

### Step P1 — Create a Twilio account

1. Go to [twilio.com](https://twilio.com) and create an account.
2. Add a payment method.
3. From the Console dashboard, note your **Account SID** (starts with `AC`) and **Auth Token**. Treat the Auth Token like a password — it is a credential that grants full API access to your account.

### Step P2 — US A2P 10DLC registration (START THIS FIRST — multi-day lead time)

**If you are sending to US phone numbers, this step gates all US SMS delivery.** Unregistered Application-to-Person (A2P) traffic to US numbers is silently filtered or blocked by carriers. An unregistered pager will appear to work but SMS will never arrive — defeating the entire purpose.

**Lead time: typically 1–5 business days** for Brand vetting + Campaign approval. It can be longer. Start immediately.

Steps in the Twilio Console (under Messaging → Regulatory Compliance / A2P 10DLC):

1. **Register a Brand** — your organization identity (legal name, EIN/tax ID, website, contact). One-time fee: **~$4**.
2. **Register a Campaign** — the use case. Select "Transactional" or "Notifications / Alerts". Monthly fee: **~$10–15/month** (varies by use case). Typical campaign description: "Automated infrastructure alerts for condominium management system. Recipients are on-call staff who have opted in."
3. **Link your sender number** to the registered Campaign once both are approved.

For Toll-Free numbers: use Toll-Free Verification instead of 10DLC. Similar lead time.

If recipients are **non-US**, check destination-country requirements in the Twilio docs — 10DLC may not apply but other restrictions may.

### Step P3 — Purchase an SMS-capable sender number

In the Twilio Console under Phone Numbers → Buy a Number:

- Filter by capability: **SMS**.
- For US delivery: a **local 10DLC** number (~$1.15/month) or a **Toll-Free** number (~$2/month).
- Note the number in E.164 format (e.g. `+15550001234`). This is `TWILIO_FROM`.

### Step P4 — Collect recipient phone numbers

- Gather the on-call mobile number(s) in **E.164 format** (e.g. `+15551112222`).
- Use **at least 2 recipients** so a single dead phone does not silently drop the page.
- These go in `SMS_RECIPIENTS` as a comma-separated list.

### Step P5 — Set up independent SMTP for the email fallback

The email fallback contact point must use an SMTP relay that is **independent of the CPS application's SMTP**. The app's mail path shares the Postgres-dependent stack — if Postgres is down, the app's SMTP may also be impaired, making it useless as a fallback.

SMTP credentials are configured in Grafana, not in the relay's env file. The relay never sends email.

Options:
- **Transactional provider** (SendGrid, AWS SES, Mailgun, Postmark). Free tiers cover the tiny volume here.
- **Monitrix-local Postfix** relay that forwards out through an independent upstream.

Collect: SMTP host, port (587 for STARTTLS), username, password, from address, and the on-call inbox address.

### Rough cost summary (US, indicative — verify current Twilio pricing)

| Item | Cost |
|------|------|
| Sender number | ~$1.15/month (local) or ~$2/month (toll-free) |
| 10DLC Brand registration | ~$4 one-time |
| 10DLC Campaign | ~$10–15/month |
| Per SMS (US, all-in) | ~$0.012–0.015/segment |
| Expected volume (anti-storm + heartbeat) | ~30 SMS/month baseline |
| **Expected total/month** | **~$12–20/month** |

The dominant costs are the number rental and the 10DLC Campaign fee. SMS volume is tiny — the anti-storm 1h repeat cap holds sustained outages to ≤ ~1 SMS/hour per alert.

---

## Part 2 — Setup

### Prerequisites

- monitrix is running Grafana OSS (tested with Grafana 9+) and Prometheus.
- You have root access on monitrix.
- You have completed all of Part 1 and have your Twilio credentials ready.
- Python 3.8+ is installed on monitrix (`python3 --version`).
- `curl` is available on monitrix.
- `systemd` is the init system.

### Step S1 — Run the installer

```bash
# On monitrix, as root:
sudo bash /path/to/monitoring/sms-pager/install.sh
```

The installer is idempotent — safe to re-run. It will:
- Create the `monitrix-pager` system user (no shell, no home directory).
- Install the relay, heartbeat, and test scripts to `/usr/local/lib/monitrix/sms-pager/`.
- Create or upgrade a Python venv at `/usr/local/lib/monitrix/sms-pager/venv/` (stdlib only, no packages).
- Create `/etc/monitrix/sms-pager.env` from the example file **if it does not already exist**.
- Set `/etc/monitrix` to `root:monitrix-pager 750` so the service user can traverse it.
- Install and enable the systemd units.

The installer will **refuse to start the service** if the env file still contains placeholder values or if `WEBHOOK_SHARED_SECRET` is shorter than 32 characters. You will see an error like:

```
[install] PLACEHOLDER DETECTED in /etc/monitrix/sms-pager.env: ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
[install] ERROR: The env file ... still contains placeholder values.
```

That is expected on first run. Proceed to Step S2.

### Step S2 — Fill in the relay env file

```bash
# On monitrix, as root:
sudo nano /etc/monitrix/sms-pager.env
```

Replace every placeholder value with the real values from Part 1. This file carries **relay-only** vars — do not add SMTP credentials here (those go in Grafana):

```
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx   # from Twilio Console
TWILIO_AUTH_TOKEN=your_auth_token_here                 # from Twilio Console
TWILIO_FROM=+15550001234                               # your purchased sender number
SMS_RECIPIENTS=+15551112222,+15553334444               # on-call numbers, E.164, comma-sep
WEBHOOK_SHARED_SECRET=<generate below>
RELAY_BIND=127.0.0.1
RELAY_PORT=9876
```

Generate a strong shared secret (minimum 32 characters):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

The installer enforces `monitrix-pager:monitrix-pager 600` on this file — no manual `chown`/`chmod` is needed after editing.

### Step S3 — Start the relay

Re-run the installer to start the service now that the env file is filled in:

```bash
sudo bash /path/to/monitoring/sms-pager/install.sh
```

Or start manually:

```bash
sudo systemctl start sms-pager.service
sudo systemctl status sms-pager.service
```

Check the logs:

```bash
sudo journalctl -u sms-pager -n 30 --no-pager
```

Expected output includes a startup line like:

```
action=startup bind=127.0.0.1 port=9876 recipients=2
```

### Step S4 — Provision Grafana contact points

Copy the provisioning files to Grafana's alerting provisioning directory:

```bash
sudo cp /path/to/monitoring/sms-pager/grafana/contact-points.yaml \
    /etc/grafana/provisioning/alerting/sms-pager-contact-points.yaml

sudo cp /path/to/monitoring/sms-pager/grafana/notification-policies.yaml \
    /etc/grafana/provisioning/alerting/sms-pager-notification-policies.yaml
```

The `sms-pager` webhook contact point uses `$__env{WEBHOOK_SHARED_SECRET}` so Grafana injects the secret at runtime rather than storing it in the provisioning file. Make the secret and the email-fallback recipient available to Grafana's systemd unit via a protected `EnvironmentFile`.

> **Security note:** Do NOT use inline `Environment=KEY=value` lines in a systemd override. That override file is `0644` (world-readable) by default, and the secret is exposed to all local users via `systemctl cat grafana-server` and `systemctl show grafana-server`. Use an `EnvironmentFile` pointing at a `0600` file instead.

```bash
# 1. Create the secrets file, owned by grafana, mode 0600:
sudo install -m 0600 -o grafana -g grafana /dev/null /etc/grafana/grafana-pager.env

# 2. Fill in the values (use your actual values):
sudo nano /etc/grafana/grafana-pager.env
```

The file should contain:

```
WEBHOOK_SHARED_SECRET=<paste your WEBHOOK_SHARED_SECRET value here>
SMTP_TO=oncall@example.com
```

The `WEBHOOK_SHARED_SECRET` value must match exactly what is in `/etc/monitrix/sms-pager.env`.

```bash
# 3. Wire the EnvironmentFile into the grafana-server unit:
sudo systemctl edit grafana-server
```

Add ONLY these lines to the override file:

```ini
[Service]
EnvironmentFile=/etc/grafana/grafana-pager.env
```

Do not add any `Environment=` lines — `EnvironmentFile` keeps the secret in the `0600` file where only root and the grafana user can read it.

**Configure Grafana SMTP** in `/etc/grafana/grafana.ini` (or a `grafana.ini` override) for the email fallback contact point. SMTP credentials do NOT go in the relay env file or in the systemd override — they belong in `grafana.ini` which Grafana reads directly:

```ini
[smtp]
enabled = true
host = smtp.sendgrid.net:587
user = apikey
password = <your smtp password>
from_address = alerts@example.com
from_name = monitrix
```

See `monitoring/sms-pager/grafana-server.env.example` for a reference of what goes where.

Restart Grafana to load the provisioning files and the new environment:

```bash
sudo systemctl restart grafana-server
sudo systemctl status grafana-server
```

Verify the contact points appeared in Grafana:
- Open Grafana → Alerting → Contact points.
- You should see `sms-pager` (Webhook) and `email-fallback` (Email).
- Click the test button on each to send a test notification (optional at this stage — Step S5 does a full end-to-end test).

### Step S5 — Wire the notification policy

The notification policy provisioning file (`notification-policies.yaml`) configures:
- **Root policy**: routes all alerts to `email-fallback` (catch-all).
- **Nested policy**: matches `pager=sms`, routes to `sms-pager`, with `continue=true` so the alert also reaches `email-fallback`.

Verify the policy is active in Grafana → Alerting → Notification policies. The tree should show:

```
Default policy → email-fallback
  └─ pager = sms → sms-pager  [continue]
```

If you manage notification policies via the UI, be aware that file provisioning and UI edits can conflict. The provisioned policy takes precedence on Grafana restart.

### Step S6 — Wire alert rules A6 and A2

Alert rule definitions are a separate build item (CPS#87 §5.1). When those rules are created, verify each carries the labels:

```
pager=sms
team=oncall
severity=critical   (A6)
severity=high       (A2)
```

These labels are what the notification policy matches on.

### Step S7 — Enable the daily heartbeat timer

The heartbeat timer should already be enabled by the installer. Verify:

```bash
sudo systemctl status sms-pager-heartbeat.timer
sudo systemctl list-timers | grep sms-pager
```

The timer fires daily at 09:00 local time. If monitrix was offline at 09:00, it fires as soon as it comes back up (`Persistent=true`).

---

## Part 3 — Verification

### Test 1 — End-to-end SMS

Run the test script. Source the env file first so `WEBHOOK_SHARED_SECRET` is available:

```bash
sudo -u monitrix-pager bash -c '
    set -a; source /etc/monitrix/sms-pager.env; set +a
    bash /usr/local/lib/monitrix/sms-pager/test_send.sh
'
```

Expected output:

```
Sending test alert to relay at http://127.0.0.1:9876/alert …
Relay HTTP status: 200
OK: relay accepted the test alert.
```

**Manually verify:**
1. SMS arrives on ALL numbers in `SMS_RECIPIENTS`. Check every phone.
2. Relay logs show `action=dispatch_ok`:

```bash
sudo journalctl -u sms-pager -n 20 --no-pager
```

### Test 2 — Email fallback

Test the email fallback via Grafana's contact-point test (SMTP credentials live in Grafana, not the relay):

```
Grafana → Alerting → Contact points → email-fallback → Test
```

Verify email arrives at the address in `SMTP_TO` (configured in the Grafana systemd override).

### Test 3 — Email fallback when relay is down

```bash
# Stop the relay:
sudo systemctl stop sms-pager.service

# Trigger a test alert via Grafana UI:
# Grafana → Alerting → Contact points → email-fallback → Test

# Verify email arrives.

# Verify Grafana shows a contact-point failure for sms-pager:
# Grafana → Alerting → Contact points — look for the error state on sms-pager.

# Restart the relay:
sudo systemctl start sms-pager.service
```

This confirms the email fallback is independent and catches a relay outage.

### Test 4 — Heartbeat fires

Trigger the heartbeat immediately (without waiting for the daily timer):

```bash
sudo systemctl start sms-pager-heartbeat.service
sudo journalctl -u sms-pager-heartbeat -n 10 --no-pager
```

Verify:
- The service exits 0.
- SMS arrives on all recipient phones with the text "SMS pager heartbeat".

To verify the timer's schedule:

```bash
sudo systemctl list-timers sms-pager-heartbeat.timer
```

---

## Part 4 — Credential Rotation

Rotate credentials immediately if a Twilio Auth Token or shared secret is suspected compromised. Rotate on a regular schedule regardless.

### Rotate the Twilio Auth Token

1. In the Twilio Console, issue a new Auth Token (Twilio shows both Primary and Secondary during transition).
2. Update `/etc/monitrix/sms-pager.env` on monitrix:
   ```bash
   sudo nano /etc/monitrix/sms-pager.env
   # Update: TWILIO_AUTH_TOKEN=<new token>
   ```
3. Restart the relay to pick up the new token:
   ```bash
   sudo systemctl restart sms-pager
   ```
4. Once confirmed working, revoke the old Auth Token in the Twilio Console.

### Rotate the Webhook Shared Secret

The shared secret must be updated in two places and both services restarted atomically.

1. Generate a new secret:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. Update `/etc/monitrix/sms-pager.env` (relay side):
   ```bash
   sudo nano /etc/monitrix/sms-pager.env
   # Update: WEBHOOK_SHARED_SECRET=<new secret>
   ```
3. Update `/etc/grafana/grafana-pager.env` (Grafana side — the `0600` EnvironmentFile):
   ```bash
   sudo nano /etc/grafana/grafana-pager.env
   # Update: WEBHOOK_SHARED_SECRET=<same new secret>
   ```
   Do not edit the systemd override directly — the secret lives in the `0600` EnvironmentFile, not in any `Environment=` line.
4. Restart both services:
   ```bash
   sudo systemctl restart sms-pager grafana-server
   ```
5. Run the end-to-end test to confirm:
   ```bash
   sudo -u monitrix-pager bash -c '
       set -a; source /etc/monitrix/sms-pager.env; set +a
       bash /usr/local/lib/monitrix/sms-pager/test_send.sh
   '
   ```

### Rotate SMTP credentials

SMTP credentials live in `/etc/grafana/grafana.ini`. Update them there and restart Grafana:

```bash
sudo nano /etc/grafana/grafana.ini
# Update [smtp] section
sudo systemctl restart grafana-server
```

---

## Part 5 — Troubleshooting

### Relay does not start

```bash
sudo journalctl -u sms-pager -n 50 --no-pager
```

Common causes:
- `action=startup error=missing_env vars=TWILIO_ACCOUNT_SID` — the env file is not readable by `monitrix-pager` or has missing values. Check: `sudo -u monitrix-pager cat /etc/monitrix/sms-pager.env`
- `action=startup error=weak_secret` — `WEBHOOK_SHARED_SECRET` is shorter than 32 characters. Regenerate: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
- `action=startup error=unsafe_bind bind_host=0.0.0.0` — `RELAY_BIND` must be `127.0.0.1`.
- Port already in use — check if another process uses `RELAY_PORT`. Change the port in the env file.

### SMS not delivered (relay returns 200 but no SMS arrives)

1. Check Twilio Console → Monitor → Logs → Error Logs for delivery errors.
2. Confirm the sender number is linked to an approved 10DLC Campaign (US). An unlinked number will show Twilio error code 30034 or similar.
3. Confirm `TWILIO_FROM` is in E.164 format and purchased in your Twilio account.
4. Confirm `SMS_RECIPIENTS` numbers are in E.164 format.
5. In Twilio Console → Phone Numbers → Manage → Active Numbers → your number → Messaging Geographic Permissions — ensure the recipient's country is enabled.

### Relay returns 403

The `X-Relay-Secret` header sent by Grafana does not match `WEBHOOK_SHARED_SECRET`. Verify:
- The value in `/etc/monitrix/sms-pager.env` matches the `WEBHOOK_SHARED_SECRET` value in `/etc/grafana/grafana-pager.env`.
- Restart both `sms-pager.service` and `grafana-server` after changing the value.

### Email fallback not arriving

1. Test Grafana SMTP directly: Grafana → Alerting → Contact points → `email-fallback` → Test.
2. Check Grafana logs: `sudo journalctl -u grafana-server -n 50 --no-pager | grep -i smtp`
3. Verify `grafana.ini` `[smtp]` settings and that `enabled = true`.
4. Confirm the SMTP provider allows connections from monitrix's IP (some providers require IP allowlisting).

### Heartbeat stopped arriving

This is the primary signal that the pager chain is broken. Diagnose in order:

1. **Timer firing?** `sudo systemctl list-timers sms-pager-heartbeat.timer`
2. **Service succeeding?** `sudo systemctl status sms-pager-heartbeat.service` — check exit code.
3. **Relay running?** `sudo systemctl status sms-pager.service`
4. **Twilio account standing?** Check Twilio Console for billing issues or account suspension.
5. **Relay log:** `sudo journalctl -u sms-pager -n 50 --no-pager`

### Rollback

To disable and remove the SMS pager:

```bash
sudo systemctl disable --now sms-pager.service sms-pager-heartbeat.timer sms-pager-heartbeat.service
sudo rm /etc/systemd/system/sms-pager.service
sudo rm /etc/systemd/system/sms-pager-heartbeat.service
sudo rm /etc/systemd/system/sms-pager-heartbeat.timer
sudo systemctl daemon-reload
```

The env file and installed relay files are left in place. To remove entirely:

```bash
sudo rm -rf /usr/local/lib/monitrix/sms-pager
# Leave /etc/monitrix/sms-pager.env in place for re-installation,
# or remove it: sudo rm /etc/monitrix/sms-pager.env
```

Remove the contact point and notification policy provisioning files from `/etc/grafana/provisioning/alerting/` and restart Grafana.

---

## Reference: file inventory

| File | Location on monitrix | Purpose |
|------|-----------------------|---------|
| `sms_relay.py` | `/usr/local/lib/monitrix/sms-pager/sms_relay.py` | Webhook → Twilio relay |
| `heartbeat.sh` | `/usr/local/lib/monitrix/sms-pager/heartbeat.sh` | Daily synthetic heartbeat |
| `test_send.sh` | `/usr/local/lib/monitrix/sms-pager/test_send.sh` | End-to-end test script |
| `sms-pager.env` (real) | `/etc/monitrix/sms-pager.env` | Relay secrets only (chmod 600) |
| `sms-pager.service` | `/etc/systemd/system/sms-pager.service` | Relay systemd unit |
| `sms-pager-heartbeat.service` | `/etc/systemd/system/sms-pager-heartbeat.service` | Heartbeat one-shot unit |
| `sms-pager-heartbeat.timer` | `/etc/systemd/system/sms-pager-heartbeat.timer` | Daily heartbeat timer |
| `contact-points.yaml` | `/etc/grafana/provisioning/alerting/sms-pager-contact-points.yaml` | Grafana contact points |
| `notification-policies.yaml` | `/etc/grafana/provisioning/alerting/sms-pager-notification-policies.yaml` | Grafana notification policy |

Source files (this repo): `monitoring/sms-pager/`

SMTP configuration: `/etc/grafana/grafana.ini [smtp]` — not in the relay env file.
Grafana-side env vars (`WEBHOOK_SHARED_SECRET`, `SMTP_TO`): `/etc/grafana/grafana-pager.env` (chmod 600, owned by grafana), loaded via `EnvironmentFile=` in the grafana-server systemd override. Never use inline `Environment=` lines for secrets.
